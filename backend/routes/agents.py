import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services.memory_tree import effective_memory as resolve_memory_tree
from services import build_agents, dev_servers, jobs, agents_registry, triggers

router = APIRouter()

HEARTBEATS_DIR = Path(os.environ.get("WORKSHOP_HEARTBEATS_DIR", "/home/clawd-a/workshop/.runtime/heartbeats"))

KNOWN_TOOLS = ["filesystem", "plaid", "google-docs", "playwright", "ollama", "web"]


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    persona: str = ""
    human: str = ""


@router.get("/")
async def list_agents():
    """One unified roster: every Agent across both scopes and both runtimes,
    sourced from the agents registry (definition files + builder runtime state).
    Per-channel masini agents are excluded by the registry."""
    reg = agents_registry.list_agents()

    out: list[dict] = []
    for a in reg:
        out.append({
            "id": a.get("id"),
            "slug": a.get("slug"),
            "runtime": a.get("runtime"),
            "name": a.get("name"),
            "purpose": a.get("purpose"),
            "triggers": a.get("triggers") or [],
            "limits": a.get("limits") or {},
            "autonomy": triggers.triggers_to_preset(a.get("triggers") or []),
            "role": a.get("role"),
            "depth": a.get("depth") or "standard",
            "fanout": a.get("fanout") or "read-only",
            "capabilities": a.get("capabilities") or [],
            "memory": a.get("memory"),
            "project_slug": a.get("project_slug"),
            "scope": a.get("scope"),
            "state": a.get("state"),
            "claude_session": a.get("claude_session"),
            "created_at": a.get("created"),
            "last_active": a.get("last_active") or a.get("created"),
            "memory_blocks": [],
            "source": a.get("source"),
        })

    out.sort(key=lambda a: a.get("last_active") or a.get("created_at") or "", reverse=True)
    return out


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


class BuildAgentRename(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    purpose: str | None = Field(default=None, max_length=280)


@router.patch("/build/{slug}")
async def rename_build_agent(slug: str, body: BuildAgentRename):
    try:
        return build_agents.rename(slug, name=body.name, purpose=body.purpose)
    except KeyError:
        raise HTTPException(404, f"build agent not found: {slug}")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/build/{slug}")
async def get_build_agent(slug: str):
    try:
        rec = build_agents.get_agent(slug)
    except KeyError:
        raise HTTPException(404, f"build agent not found: {slug}")
    # Make the detail view's status honest, matching the roster rule:
    #  - `state` "running" only if a turn is genuinely in flight (not the stale
    #    flag that ratcheted on first turn).
    #  - `session_alive` is a REAL check that the CLI session file still exists,
    #    not merely "a session id string is stored" (which stayed true after the
    #    session was pruned, so the pill lied).
    from services import build_streaming
    if rec.get("state") == "running" and slug not in agents_registry.live_builder_slugs():
        rec["state"] = "idle"
    rec["session_alive"] = build_streaming.session_alive(
        rec.get("claude_session"), rec.get("cwd") or "")
    return rec


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


def _agent_scope_root(rec: dict) -> _P | None:
    """The tree this agent's tasks live in: its linked project dir, else a
    builder's own cwd. Assistant agents with no project link have no code tree,
    so their queue snapshot is empty."""
    pslug = rec.get("project_slug")
    if pslug:
        pd = build_agents._project_dir(pslug)
        if pd is not None:
            return _P(pd)
    if rec.get("runtime") == "builder" and rec.get("cwd"):
        return _P(rec["cwd"])
    return None


def _agent_queue_snapshot(rec: dict) -> dict:
    """Queue counts (`ready`/`doing`/`in_review`/`blocked`) + pending `awaiting`
    payloads for an agent's scope. Mirrors the project autonomy snapshot; returns
    zeros when the agent has no resolvable task tree."""
    empty = {"queue": {"ready": 0, "doing": 0, "in_review": 0, "blocked": 0,
                       "parked": 0, "todo": 0}, "awaiting": []}
    root = _agent_scope_root(rec)
    if root is None:
        return empty
    try:
        items = tasks.list_tasks(root, include_done=True)
    except Exception:
        return empty
    by_id = {t.get("id"): t for t in items}
    tally = {"todo": 0, "doing": 0, "in_review": 0, "blocked": 0,
             "parked": 0, "done": 0, "cancelled": 0}
    awaiting: list[dict] = []
    ready = 0
    for t in items:
        st = t.get("status")
        if st in tally:
            tally[st] += 1
        if st == "todo":
            deps = t.get("depends_on") or []
            if all((by_id.get(d) or {}).get("status") in tasks.TERMINAL
                   for d in deps if d in by_id):
                ready += 1
        aw = t.get("awaiting")
        if isinstance(aw, dict) and aw.get("question"):
            awaiting.append({
                "id": t.get("id"),
                "status": st,
                "text": (t.get("text") or "").split("\n")[0][:140],
                "question": aw.get("question"),
                "since": aw.get("since"),
            })
    return {
        "queue": {
            "ready": ready,
            "doing": tally["doing"],
            "in_review": tally["in_review"],
            "blocked": tally["blocked"],
            "parked": tally["parked"],
            "todo": tally["todo"],
        },
        "awaiting": awaiting,
    }


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    """Unified detail, resolved from the registry (by slug, routing id, or linked
    id). The assistant persona/instructions live in the definition body; there is
    no separate Letta memory layer to enrich from. Enriched with the autonomy
    preset (classified from the agent's triggers) plus the scope's queue counts
    and any pending `awaiting` prompts."""
    rec = agents_registry.get(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    snap = _agent_queue_snapshot(rec)
    return {
        **rec,
        "autonomy": triggers.triggers_to_preset(rec.get("triggers") or []),
        "queue": snap["queue"],
        "awaiting": snap["awaiting"],
    }


class AgentDefinitionUpdate(BaseModel):
    name: str | None = None
    purpose: str | None = None
    triggers: list[dict] | None = None
    limits: dict | None = None
    capabilities: list[str] | None = None
    role: str | None = None            # role taxonomy tag (e.g. "memory")
    depth: str | None = None           # effort tier: quick | standard | deep
    fanout: str | None = None          # off | read-only
    definition: str | None = None  # the markdown body (persona / instructions)


@router.put("/{agent_id}")
async def update_agent_definition(agent_id: str, body: AgentDefinitionUpdate):
    """Write the declarative definition (name/purpose/triggers/limits/body) to the
    agent's markdown file via the registry. Trigger writes land in frontmatter and
    regenerate `.runtime/agent-subscriptions.json` as a union (non-registry
    entries preserved). This is the Phase-2 trigger-write path; Letta metadata is
    no longer the backing store for autonomy."""
    rec = agents_registry.get(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    try:
        return agents_registry.update(
            rec["slug"],
            name=body.name,
            purpose=body.purpose,
            triggers_list=body.triggers,
            limits=body.limits,
            capabilities=body.capabilities,
            role=body.role,
            depth=body.depth,
            fanout=body.fanout,
            body=body.definition,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/")
async def create_agent(body: AgentCreate):
    """Create an assistant Agent in the registry (a markdown definition file). The
    persona/instructions become the definition body; there is no separate memory
    runtime to provision. Builders are created via POST /agents/build. Triggers
    and limits are edited afterward on the detail page (PUT /agents/{id})."""
    parts: list[str] = []
    if body.persona.strip():
        parts.append(body.persona.strip())
    if body.human.strip():
        parts.append("## Operator context\n\n" + body.human.strip())
    try:
        return agents_registry.create(
            slug=agents_registry.mint_slug(body.name),
            runtime="assistant",
            name=body.name,
            memory="notes",
            body="\n\n".join(parts),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str):
    """Retire an agent from the registry: it leaves the roster while the
    declarative definition is preserved (retired flag). Builders also flip their
    build-runtime state."""
    rec = agents_registry.get(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    agents_registry.retire(rec["slug"])
    return {"ok": True, "retired": rec["slug"]}


@router.get("/{agent_id}/jobs")
async def agent_jobs(agent_id: str, limit: int = 50):
    """Job history filtered to this agent, sourced from the unified Job store
    (`services.jobs` / `.runtime/jobs.jsonl`). Declared as a two-segment path so
    it never falls into the `/{agent_id}` catch-all. Interactive chat turns are
    not Jobs and never appear here."""
    limit = max(1, min(limit, 200))
    records = jobs.read(agent_id=agent_id, limit=limit)
    return {"agent_id": agent_id, "jobs": records, "count": len(records)}


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

    # Recent Jobs for this agent, sourced from the unified Job store
    # (.runtime/jobs.jsonl) rather than the retired agent-runs.jsonl. Same
    # response shape; the frontend reads `runs`.
    runs = jobs.read(agent_id=agent_id, limit=limit)

    return {
        "agent_id": agent_id,
        "heartbeat": heartbeat,
        "heartbeat_age_s": heartbeat_age_s,
        "liveness": "unresponsive" if (heartbeat_age_s is not None and heartbeat_age_s > 120) else ("alive" if heartbeat else "no-heartbeat"),
        "runs": runs,
        "runs_count": len(runs),
    }
