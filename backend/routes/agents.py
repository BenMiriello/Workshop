import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services.memory_tree import effective_memory as resolve_memory_tree
from services.budget import budget_for
from services import global_memory, per_channel_agents, build_agents, dev_servers

router = APIRouter()

RUNS_JSONL = Path(os.environ.get("WORKSHOP_RUNS_JSONL", "/home/clawd-a/workshop/.runtime/agent-runs.jsonl"))
HEARTBEATS_DIR = Path(os.environ.get("WORKSHOP_HEARTBEATS_DIR", "/home/clawd-a/workshop/.runtime/heartbeats"))
SUBSCRIPTIONS_PATH = Path(os.environ.get("WORKSHOP_SUBSCRIPTIONS", "/home/clawd-a/workshop/.runtime/agent-subscriptions.json"))

KNOWN_TOOLS = ["filesystem", "plaid", "google-docs", "playwright", "ollama", "web"]
KNOWN_SOURCES = ["plaid", "gmail", "calendar", "github", "open-wearables", "bluebubbles", "custom"]
AUTONOMY_MODES = {"off", "scheduled", "event-driven", "continuous"}


def _update_subscriptions(agent_id: str, sources: list[str]) -> None:
    try:
        data = json.loads(SUBSCRIPTIONS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {s: [] for s in KNOWN_SOURCES}
    for key in list(data.keys()):
        if isinstance(data[key], list) and agent_id in data[key]:
            data[key].remove(agent_id)
    for src in sources:
        if src in data and isinstance(data[src], list):
            if agent_id not in data[src]:
                data[src].append(agent_id)
    SUBSCRIPTIONS_PATH.write_text(json.dumps(data, indent=2))


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    persona: str = ""
    human: str = ""
    persona_path: str = ""  # e.g. "ops/personal-ai" — drives memory inheritance tree
    tools: list[str] = []
    autonomy_mode: str = "off"
    cron_expression: str = ""
    event_sources: list[str] = []


class AgentSpinoff(BaseModel):
    base_agent_id: str
    new_name: str = Field(min_length=1, max_length=64)
    persona_override: str = ""


class AgentMetadataUpdate(BaseModel):
    tools: list[str] = []
    autonomy_mode: str | None = None
    cron_expression: str | None = None
    event_sources: list[str] | None = None
    persona_path: str | None = None


class MemoryBlockUpdate(BaseModel):
    value: str


@router.get("/")
async def list_agents(request: Request):
    try:
        data = await request.app.state.letta.list_agents()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")
    return [
        {
            "id": a.get("id"),
            "name": a.get("name"),
            "kind": "data",
            "created_at": a.get("created_at"),
            "memory_blocks": [b.get("label") for b in a.get("memory", {}).get("blocks", [])],
        }
        for a in data
    ]


@router.get("/tools")
async def list_tools():
    return {"tools": KNOWN_TOOLS}


# ---- Build agents (Claude Code coding sessions) ----------------------------
# Distinct kind of Agent per ADR-008. Each Build agent is scoped to a cwd
# (either a Project directory or a freestanding scratch dir). The chat surface
# uses contextKey="build:<slug>" to route through chat_context's
# BuildAgentContextProvider which sets cwd at call_cloud spawn time.

class BuildAgentCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=48)
    name: str = Field(min_length=1, max_length=64)
    purpose: str = ""
    project_slug: str | None = None
    branch_slug: str | None = None


@router.get("/build")
async def list_build_agents():
    return build_agents.list_agents()


@router.post("/build")
async def create_build_agent(body: BuildAgentCreate):
    try:
        return build_agents.create_agent(
            slug=body.slug,
            name=body.name,
            purpose=body.purpose,
            project_slug=body.project_slug,
            branch_slug=body.branch_slug,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/build/{slug}")
async def get_build_agent(slug: str):
    try:
        return build_agents.get_agent(slug)
    except KeyError:
        raise HTTPException(404, f"build agent not found: {slug}")


@router.post("/build/{slug}/retire")
async def retire_build_agent(slug: str):
    build_agents.retire(slug)
    return {"ok": True}


@router.post("/build/{slug}/session/reset")
async def reset_build_session(slug: str):
    """Detach this agent record from its Claude Code session so the next turn
    starts fresh. Useful when the conversation gets confused or you want to
    restart the agent's thinking from scratch."""
    build_agents.reset_session(slug)
    return {"ok": True}


# ---- Build agent file ops (the coding-UI surface) --------------------------
# Mirror the Project file-IO endpoints but scoped to the build agent's cwd.
# Path-traversal guard: target must be under the agent's cwd.

from pathlib import Path as _P

class _Content(BaseModel):
    content: str


def _agent_cwd(slug: str) -> _P:
    try:
        a = build_agents.get_agent(slug)
    except KeyError:
        raise HTTPException(404, f"build agent not found: {slug}")
    return _P(a["cwd"])


def _safe_resolve(root: _P, path: str) -> _P:
    target = (root / path).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise HTTPException(400, "path escapes agent cwd")
    return target


@router.get("/build/{slug}/tree")
async def build_agent_tree(slug: str):
    root = _agent_cwd(slug)
    SKIP = {".git", "node_modules", "dist", ".next", "__pycache__"}

    def walk(p: _P) -> list[dict]:
        items = []
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name)):
            if child.name in SKIP or child.name.startswith("."):
                continue
            item = {"name": child.name, "path": str(child.relative_to(root))}
            if child.is_dir():
                item["kind"] = "dir"
                item["children"] = walk(child)
            else:
                item["kind"] = "file"
                try:
                    item["size"] = child.stat().st_size
                except OSError:
                    item["size"] = None
            items.append(item)
        return items

    return {"root": str(root), "tree": walk(root) if root.exists() else []}


@router.get("/build/{slug}/file")
async def build_agent_file_get(slug: str, path: str):
    root = _agent_cwd(slug)
    target = _safe_resolve(root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file not found")
    try:
        return {"path": path, "content": target.read_text()}
    except UnicodeDecodeError:
        raise HTTPException(415, "binary file")


@router.put("/build/{slug}/file")
async def build_agent_file_put(slug: str, path: str, body: _Content):
    root = _agent_cwd(slug)
    target = _safe_resolve(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content)
    build_agents.touch(slug)
    return {"ok": True}


@router.get("/build/{slug}/brief")
async def build_agent_brief_get(slug: str):
    root = _agent_cwd(slug)
    p = root / "PROJECT.md"
    return {"content": p.read_text() if p.exists() else ""}


@router.put("/build/{slug}/brief")
async def build_agent_brief_put(slug: str, body: _Content):
    root = _agent_cwd(slug)
    (root / "PROJECT.md").write_text(body.content)
    build_agents.touch(slug)
    return {"ok": True}


@router.get("/build/{slug}/todo")
async def build_agent_todo_get(slug: str):
    root = _agent_cwd(slug)
    p = root / "TODO.md"
    return {"content": p.read_text() if p.exists() else ""}


@router.put("/build/{slug}/todo")
async def build_agent_todo_put(slug: str, body: _Content):
    root = _agent_cwd(slug)
    (root / "TODO.md").write_text(body.content)
    build_agents.touch(slug)
    return {"ok": True}


@router.get("/build/{slug}/interventions")
async def build_agent_interventions_get(slug: str):
    root = _agent_cwd(slug)
    p = root / "INTERVENTIONS.md"
    return {"content": p.read_text() if p.exists() else ""}


# ---- Structured tasks (plan item 14; docs/workshop-v1 02 §3) ---------------
# A build agent owns its OWN task list. Tasks live in the agent's linked
# project (so they roll up into the project view) when the agent has a
# project_slug, else in the agent's own cwd. Owner is forced to the agent slug
# and all reads/writes are scoped to that owner so an agent can only touch its
# own tasks. The structured store (.tasks.json) is authoritative; TODO.md is
# the regenerated export so chat_context (which reads TODO.md) stays current.

from services import tasks


class _TaskCreate(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    parent: str | None = None


class _TaskPatch(BaseModel):
    status: str | None = None
    text: str | None = None


def _tasks_root(slug: str) -> _P:
    """Where this agent's structured task store lives: the linked project dir
    if the agent has one, else the agent's own cwd."""
    try:
        a = build_agents.get_agent(slug)
    except KeyError:
        raise HTTPException(404, f"build agent not found: {slug}")
    project_slug = a.get("project_slug")
    if project_slug:
        pd = build_agents._project_dir(project_slug)
        if pd is not None:
            return pd
    return _P(a["cwd"])


@router.get("/build/{slug}/tasks")
async def build_agent_tasks_list(slug: str, include_done: bool = True):
    return {"tasks": tasks.list_tasks(_tasks_root(slug), include_done=include_done, owner=slug)}


@router.post("/build/{slug}/tasks")
async def build_agent_tasks_add(slug: str, body: _TaskCreate):
    root = _tasks_root(slug)
    # Guard: a parent (if given) must belong to this agent's own tasks.
    if body.parent is not None and tasks.get_task(root, body.parent, owner=slug) is None:
        raise HTTPException(400, f"parent task not found: {body.parent}")
    try:
        task = tasks.add(root, text=body.text, owner=slug, parent=body.parent)
    except ValueError as e:
        raise HTTPException(400, str(e))
    build_agents.touch(slug)
    return task


@router.patch("/build/{slug}/tasks/{task_id}")
async def build_agent_tasks_patch(slug: str, task_id: str, body: _TaskPatch):
    if body.status is None and body.text is None:
        raise HTTPException(400, "no fields to update")
    try:
        task = tasks.update(_tasks_root(slug), task_id, status=body.status, text=body.text, owner=slug)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if task is None:
        raise HTTPException(404, f"task not found: {task_id}")
    build_agents.touch(slug)
    return task


@router.delete("/build/{slug}/tasks/{task_id}")
async def build_agent_tasks_delete(slug: str, task_id: str):
    if not tasks.remove(_tasks_root(slug), task_id, owner=slug):
        raise HTTPException(404, f"task not found: {task_id}")
    build_agents.touch(slug)
    return {"deleted": True}


# ---- Interventions: append-only operator nudges (ADR-009 section 5) --------
# A short note the operator drops into the agent's working dir to bias the
# next turn without breaking the chat conversation cadence. The existing GET
# endpoint above reads the file; this POST appends a single stamped line.


class _InterventionIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/build/{slug}/interventions")
async def build_agent_interventions_post(slug: str, body: _InterventionIn):
    from datetime import datetime, timezone
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "text required")
    root = _agent_cwd(slug)
    p = root / "INTERVENTIONS.md"
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"[{ts}] operator: {text}\n"
    existing = p.read_text() if p.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(existing + line)
    build_agents.touch(slug)
    return {"ok": True, "ts": ts}


# ---- Diff + revert + commit ------------------------------------------------
# Build agents write directly to the working tree. The operator reviews via
# the Diff tab and can revert per-file or commit a snapshot when satisfied.

import subprocess


def _git(slug: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cwd = _agent_cwd(slug)
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=check, timeout=15,
    )


@router.get("/build/{slug}/diff")
async def build_agent_diff(slug: str):
    """Return per-file diff status + per-file unified diff content. Computed
    against HEAD (working tree + index combined: --cached + worktree). Files
    untracked by git are listed with status='??' and content from the new file."""
    cwd = _agent_cwd(slug)
    if not (cwd / ".git").exists():
        return {"files": [], "summary": "no git repo"}
    # Status: get one line per file. -z for null-delim to handle weird names.
    status_proc = _git(slug, "status", "--porcelain", check=False)
    files: list[dict] = []
    for line in status_proc.stdout.splitlines():
        if not line:
            continue
        # First two chars are XY status; rest after one space is the path.
        xy = line[:2]
        path = line[3:].strip()
        # `git diff HEAD -- <path>` shows unified diff for tracked changes.
        # For untracked (xy='??'), there's no diff vs HEAD; show whole file.
        if xy.strip() == "??":
            try:
                content = (cwd / path).read_text()
            except (OSError, UnicodeDecodeError):
                content = "(binary or unreadable)"
            diff = "@@ new file @@\n" + "\n".join("+" + ln for ln in content.splitlines())
        else:
            diff_proc = _git(slug, "diff", "HEAD", "--", path, check=False)
            diff = diff_proc.stdout
        files.append({"status": xy, "path": path, "diff": diff})
    # Summary line: total adds/dels via --shortstat.
    short_proc = _git(slug, "diff", "HEAD", "--shortstat", check=False)
    summary = short_proc.stdout.strip() or "no tracked changes"
    return {"files": files, "summary": summary}


class _RevertIn(BaseModel):
    path: str


@router.post("/build/{slug}/diff/revert-file")
async def build_agent_revert_file(slug: str, body: _RevertIn):
    """Restore <path> to HEAD's version. For untracked files, deletes them.
    Operator-confirmed in the UI."""
    cwd = _agent_cwd(slug)
    target = (cwd / body.path).resolve()
    if cwd.resolve() not in target.parents and target != cwd.resolve():
        raise HTTPException(400, "path escapes agent cwd")
    # Check tracked status.
    tracked = _git(slug, "ls-files", "--error-unmatch", "--", body.path, check=False)
    if tracked.returncode == 0:
        _git(slug, "checkout", "HEAD", "--", body.path)
    else:
        # Untracked → delete (matches what revert means visually).
        if target.exists():
            target.unlink()
    build_agents.touch(slug)
    return {"ok": True}


class _CommitIn(BaseModel):
    message: str = "Build agent checkpoint"


# ---- Dev-server preview ----------------------------------------------------
# Each Build agent can run at most one dev server. The Preview tab shows its
# iframe + boot/stop controls + a tail of the dev-server log.


class _StartPreviewIn(BaseModel):
    command: str
    port: int = 8501


@router.get("/build/{slug}/preview/status")
async def build_agent_preview_status(slug: str, request: Request):
    """Status, plus boot-time helpers per ADR-009 section 6.

    When stopped: include a `suggested_port` so the UI can default the port
    picker without the operator having to think about it. When running:
    include a `public_url` derived from the request host so the link works
    over the LAN, not just localhost.
    """
    suggestion = dev_servers.suggest_command(str(_agent_cwd(slug)))
    base = dev_servers.status(slug)
    payload: dict = {**base, "suggested": suggestion}
    host = (request.headers.get("host") or "localhost").split(":")[0]
    if base.get("running"):
        port = base.get("port")
        if port:
            payload["public_url"] = f"http://{host}:{port}/"
    else:
        try:
            payload["suggested_port"] = dev_servers.next_free_port()
        except RuntimeError:
            payload["suggested_port"] = None
    return payload


@router.post("/build/{slug}/preview/start")
async def build_agent_preview_start(slug: str, body: _StartPreviewIn):
    cwd = _agent_cwd(slug)
    r = dev_servers.start(slug, cwd=str(cwd), command=body.command, port=body.port)
    if not r.get("ok"):
        raise HTTPException(400, r.get("reason") or "start failed")
    build_agents.touch(slug)
    return r


@router.post("/build/{slug}/preview/stop")
async def build_agent_preview_stop(slug: str):
    return dev_servers.stop(slug)


@router.get("/build/{slug}/preview/log")
async def build_agent_preview_log(slug: str):
    return {"log": dev_servers.tail_log(slug)}


@router.post("/build/{slug}/commit")
async def build_agent_commit(slug: str, body: _CommitIn):
    """Stage everything in the agent's cwd and create a commit. The operator
    uses this to mark 'this state is good, snapshot it' so they can revert
    safely later. Committer matches the Workshop Projects identity used at
    scaffold time so the history reads cleanly."""
    cwd = _agent_cwd(slug)
    if not (cwd / ".git").exists():
        raise HTTPException(400, "no git repo in agent cwd")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Workshop Projects",
        "GIT_AUTHOR_EMAIL": "projects@workshop.local",
        "GIT_COMMITTER_NAME": "Workshop Projects",
        "GIT_COMMITTER_EMAIL": "projects@workshop.local",
    }
    subprocess.run(["git", "-C", str(cwd), "add", "."], env=env, check=True, timeout=15)
    # If nothing staged, surface that clearly rather than erroring.
    status = subprocess.run(
        ["git", "-C", str(cwd), "diff", "--cached", "--quiet"],
        env=env, timeout=15,
    )
    if status.returncode == 0:
        return {"ok": False, "reason": "nothing to commit"}
    subprocess.run(
        ["git", "-C", str(cwd), "commit", "-q", "-m", body.message or "Build agent checkpoint"],
        env=env, check=True, timeout=15,
    )
    build_agents.touch(slug)
    head = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "HEAD"],
        env=env, capture_output=True, text=True, check=True, timeout=15,
    ).stdout.strip()
    return {"ok": True, "commit": head}


@router.get("/memory/tree-nodes")
async def memory_tree_nodes():
    """Return all known global blocks + archival namespaces from the memory tree.
    Used by the UI to populate the memory-scope multi-selects."""
    def _collect(node: dict, blocks: set, namespaces: set):
        blocks.update(node.get("blocks") or [])
        namespaces.update(node.get("archival_namespaces") or [])
        for child in (node.get("children") or {}).values():
            _collect(child, blocks, namespaces)

    from services.memory_tree import _load_tree
    tree = _load_tree()
    root = tree.get("root", {})
    all_blocks: set = set()
    all_ns: set = set()
    _collect(root, all_blocks, all_ns)
    return {"blocks": sorted(all_blocks), "archival_namespaces": sorted(all_ns)}


@router.get("/memory/effective")
async def memory_effective_query(persona_path: str = ""):
    """Resolve the effective blocks + archival namespaces for a persona_path
    without needing an existing agent. Useful for planning and diagnostics."""
    if not persona_path:
        raise HTTPException(status_code=422, detail="persona_path query param required (e.g. ?persona_path=ops/personal-ai)")
    return resolve_memory_tree(persona_path)


@router.get("/{agent_id}/memory/effective")
async def agent_memory_effective(agent_id: str, request: Request):
    """Return the resolved memory inheritance for an existing agent. Reads
    persona_path from agent metadata and walks the tree."""
    try:
        agent = await request.app.state.letta.get_agent(agent_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")
    meta = agent.get("metadata_") or agent.get("metadata") or {}
    persona_path = meta.get("persona_path", "")
    resolved = resolve_memory_tree(persona_path) if persona_path else {
        "persona_path": "", "blocks": [], "archival_namespaces": [], "resolved_depth": 0
    }
    return {**resolved, "agent_id": agent_id, "agent_name": agent.get("name")}


@router.get("/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    try:
        return await request.app.state.letta.get_agent(agent_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")


@router.post("/")
async def create_agent(body: AgentCreate, request: Request):
    try:
        agent = await request.app.state.letta.create_agent(body.name, body.persona, body.human)
        meta: dict = {"tools": [t for t in body.tools if t in KNOWN_TOOLS]}
        if body.persona_path:
            resolved = resolve_memory_tree(body.persona_path)
            meta["persona_path"] = resolved["persona_path"]
            meta["effective_blocks"] = resolved["blocks"]
            meta["effective_namespaces"] = resolved["archival_namespaces"]
        if body.autonomy_mode and body.autonomy_mode in AUTONOMY_MODES:
            meta["autonomy"] = body.autonomy_mode
            meta["autonomy_mode"] = body.autonomy_mode
            if body.autonomy_mode == "scheduled":
                meta["cron_expression"] = body.cron_expression
            elif body.autonomy_mode == "event-driven":
                valid_sources = [s for s in body.event_sources if s in KNOWN_SOURCES]
                meta["event_sources"] = valid_sources
                _update_subscriptions(agent["id"], valid_sources)
        if any(v for v in meta.values() if v):
            await request.app.state.letta.update_agent_metadata(agent["id"], meta)
            agent.setdefault("metadata_", {}).update(meta)
        # Attach the Workshop global memory block (operator profile + active services + plan).
        try:
            await global_memory.attach_to(agent["id"])
        except Exception as e:
            # Non-fatal: agent exists; operator can re-sync later via /agents/global-memory/sync.
            pass
        return agent
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")


@router.patch("/{agent_id}/memory/{label}")
async def patch_memory_block(agent_id: str, label: str, body: MemoryBlockUpdate, request: Request):
    try:
        return await request.app.state.letta.update_memory_block(agent_id, label, body.value)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")


@router.patch("/{agent_id}/metadata")
async def patch_agent_metadata(agent_id: str, body: AgentMetadataUpdate, request: Request):
    meta: dict = {"tools": [t for t in body.tools if t in KNOWN_TOOLS]}
    if body.autonomy_mode is not None:
        if body.autonomy_mode not in AUTONOMY_MODES:
            raise HTTPException(status_code=422, detail=f"autonomy_mode must be one of {sorted(AUTONOMY_MODES)}")
        meta["autonomy"] = body.autonomy_mode
        meta["autonomy_mode"] = body.autonomy_mode
        if body.autonomy_mode == "scheduled":
            meta["cron_expression"] = body.cron_expression or ""
        elif body.autonomy_mode in ("event-driven", "continuous"):
            # Continuous agents may ALSO subscribe to event sources — supervisor's
            # loop can drain events for the agent the same way an event-driven
            # agent does, just on its own polling schedule. Honor event_sources
            # for both modes.
            valid_sources = [s for s in (body.event_sources or []) if s in KNOWN_SOURCES]
            meta["event_sources"] = valid_sources
            _update_subscriptions(agent_id, valid_sources)
        else:
            # off — clear subscriptions
            _update_subscriptions(agent_id, [])
    if body.persona_path is not None:
        resolved = resolve_memory_tree(body.persona_path) if body.persona_path else {
            "persona_path": "", "blocks": [], "archival_namespaces": [], "resolved_depth": 0
        }
        meta["persona_path"] = resolved["persona_path"]
        meta["effective_blocks"] = resolved["blocks"]
        meta["effective_namespaces"] = resolved["archival_namespaces"]
    try:
        return await request.app.state.letta.update_agent_metadata(agent_id, meta)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")


@router.post("/spinoff")
async def spinoff_agent(body: AgentSpinoff, request: Request):
    """Create a new agent by forking a base agent's persona block.
    The new agent inherits the human block and metadata (tools/autonomy) from
    the base, uses persona_override if given, and starts with empty archival
    memory (fresh namespace). Archival memories are intentionally NOT copied."""
    try:
        base = await request.app.state.letta.get_agent(body.base_agent_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")

    # Extract persona + human values from base agent memory blocks
    blocks = {b.get("label"): b.get("value", "") for b in base.get("memory", {}).get("blocks", [])}
    persona_val = body.persona_override if body.persona_override else blocks.get("persona", "")
    human_val = blocks.get("human", "")

    # Inherit metadata from base (tools, autonomy settings)
    base_meta = base.get("metadata_") or base.get("metadata") or {}

    llm_config = base.get("llm_config") or None
    embedding_config = base.get("embedding_config") or None

    try:
        new_agent = await request.app.state.letta.create_agent(
            body.new_name, persona_val, human_val,
            llm_config=llm_config, embedding_config=embedding_config,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta create failed: {e}")

    if base_meta:
        # Re-resolve memory inheritance for the inherited persona_path so the
        # new agent's effective_blocks/effective_namespaces are freshly computed.
        if base_meta.get("persona_path"):
            resolved = resolve_memory_tree(base_meta["persona_path"])
            base_meta = {**base_meta,
                         "effective_blocks": resolved["blocks"],
                         "effective_namespaces": resolved["archival_namespaces"]}
        try:
            await request.app.state.letta.update_agent_metadata(new_agent["id"], base_meta)
            new_agent.setdefault("metadata_", {}).update(base_meta)
        except Exception:
            pass  # metadata copy is best-effort

    # Wire event subscriptions if base was event-driven
    event_sources = base_meta.get("event_sources") or []
    if event_sources:
        _update_subscriptions(new_agent["id"], event_sources)

    # Attach the Workshop global memory block (same as create_agent).
    try:
        await global_memory.attach_to(new_agent["id"])
    except Exception:
        pass

    return {
        **new_agent,
        "spinoff_from": body.base_agent_id,
        "persona_source": "override" if body.persona_override else "inherited",
    }


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, request: Request):
    try:
        return await request.app.state.letta.delete_agent(agent_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")


@router.post("/per-channel/ensure")
async def per_channel_ensure(chat_id: str, channel_name: str = ""):
    """Return (or create) the masini spinoff agent for a Discord channel.
    chat_id is the Discord channel snowflake. channel_name is optional human
    label used for slug generation. Idempotent — repeat calls return the same
    agent_id unless the underlying Letta agent has been deleted."""
    if not chat_id:
        raise HTTPException(status_code=422, detail="chat_id required")
    try:
        return await per_channel_agents.ensure(chat_id, channel_name or None)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"per-channel ensure failed: {e}")


@router.get("/per-channel/list")
async def per_channel_list():
    """Diagnostic: full map of {chat_id: agent_id}."""
    return per_channel_agents.list_all()


@router.delete("/per-channel/{chat_id}")
async def per_channel_unmap(chat_id: str):
    """Remove a chat_id → agent_id mapping. Does NOT delete the Letta agent."""
    removed = per_channel_agents.unmap(chat_id)
    return {"removed": removed, "chat_id": chat_id}


@router.post("/global-memory/sync")
async def sync_global_memory():
    """Re-read shared/global-memory.md and PATCH the Letta operator-context
    block. All agents already attached will see the new value on next inference
    (Letta refreshes blocks per-request)."""
    try:
        return await global_memory.sync_value()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"global memory sync failed: {e}")


@router.get("/global-memory/info")
async def global_memory_info():
    """Return the cached block_id and file path — diagnostic."""
    from services.global_memory import GLOBAL_FILE, GLOBAL_ID_CACHE
    return {
        "file": str(GLOBAL_FILE),
        "file_bytes": GLOBAL_FILE.stat().st_size if GLOBAL_FILE.exists() else 0,
        "cached_block_id": GLOBAL_ID_CACHE.read_text().strip() if GLOBAL_ID_CACHE.exists() else None,
    }


@router.get("/{agent_id}/budget")
async def agent_budget(agent_id: str, request: Request):
    """Per-agent call budget. Cron consumer hits this before firing claude -p
    to enforce metadata.max_calls_per_hour. Returns {limit, used, remaining,
    over}. `over: true` means consumer should skip and write status=throttled."""
    try:
        agent = await request.app.state.letta.get_agent(agent_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")
    meta = agent.get("metadata_") or agent.get("metadata") or {}
    return budget_for(meta, agent_id)


@router.get("/{agent_id}/runs")
async def agent_runs(agent_id: str, limit: int = 50):
    """Recent activity feed for an agent: cron-runs JSONL (filtered) + last
    heartbeat from .runtime/heartbeats/{id}.json + heartbeat freshness flag.
    Powers per-agent activity cards on the Agents page."""
    limit = max(1, min(limit, 200))

    # Heartbeat
    heartbeat = None
    heartbeat_age_s = None
    hb_path = HEARTBEATS_DIR / f"{agent_id}.json"
    if hb_path.exists():
        try:
            heartbeat = json.loads(hb_path.read_text())
            from datetime import datetime, timezone
            hb_ts = heartbeat.get("ts")
            if hb_ts:
                hb_dt = datetime.fromisoformat(hb_ts.replace("Z", "+00:00"))
                heartbeat_age_s = (datetime.now(timezone.utc) - hb_dt).total_seconds()
        except (json.JSONDecodeError, ValueError):
            pass

    # Cron runs JSONL — filter by agent_id if records have it (cron_agent.sh
    # doesn't currently tag runs with agent_id since it's a single global loop;
    # future per-agent supervisor will. For now, return up to `limit` recent
    # runs if requested agent matches anything tagged, else empty.)
    runs = []
    if RUNS_JSONL.exists():
        for line in reversed(RUNS_JSONL.read_text().splitlines()):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            # If the record is tagged with agent_id, filter; else include for masini-style global.
            if "agent_id" in rec and rec["agent_id"] != agent_id:
                continue
            runs.append(rec)
            if len(runs) >= limit:
                break

    return {
        "agent_id": agent_id,
        "heartbeat": heartbeat,
        "heartbeat_age_s": heartbeat_age_s,
        "liveness": "unresponsive" if (heartbeat_age_s is not None and heartbeat_age_s > 120) else ("alive" if heartbeat else "no-heartbeat"),
        "runs": runs,
        "runs_count": len(runs),
    }
