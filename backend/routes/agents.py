import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services import agents_registry, build_agents, dev_servers, jobs, paths, triggers
from services import agent_authority

router = APIRouter()

HEARTBEATS_DIR = paths.env_path("WORKSHOP_HEARTBEATS_DIR", "heartbeats")

# Tool handles the platform itself provides. An app's tools come from its
# AppSpec.tools and appear only while it is installed.
PLATFORM_TOOLS = ["filesystem", "google-docs", "playwright", "ollama", "web"]


def known_tools() -> list[str]:
    from services import app_registry

    return PLATFORM_TOOLS + [
        t for t in app_registry.installed_tools() if t not in PLATFORM_TOOLS
    ]


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    persona: str = ""
    human: str = ""
    role: str | None = None          # closed enum: maker|reviewer|triager|keeper|broker
    latitude: str | None = None      # strict | standard | open (null = inherit)
    autonomy: str | None = None      # reactive | supervised | autonomous (null = reactive)
    project_slug: str | None = None  # links this agent into a project scope


# ---- Templates + groups (P4) -----------------------------------------------
# Declared BEFORE `GET /{agent_id}` so a literal path never has to compete with
# the slug wildcard.

class FromTemplateIn(BaseModel):
    template: str
    slug: str | None = None       # minted from the template's name when absent
    project: str | None = None    # project scope; absent = the user scope
    overrides: dict = Field(default_factory=dict)


class AdoptGroupIn(BaseModel):
    group: str
    project: str | None = None


@router.get("/templates")
async def list_agent_templates():
    """The shipped agent templates and template groups. Templates are files, not
    registry members: nothing here is registered or dispatched until it is forked
    (POST /agents/from-template)."""
    return {
        "templates": agents_registry.list_templates(),
        "groups": agents_registry.list_groups(),
    }


@router.post("/from-template")
async def create_agent_from_template(body: FromTemplateIn):
    """Fork a template into a real agent. The copy is independent — the only
    trace of its origin is a `template: <slug>@<version>` stamp in frontmatter.
    Nothing syncs afterward, by design (roles report 5.3)."""
    tpl = agents_registry.get_template(body.template)
    if tpl is None:
        raise HTTPException(404, f"unknown template: {body.template}")
    slug = (body.slug or "").strip().lower() or agents_registry.mint_slug(
        f"{body.project}-{tpl['template']}" if body.project else tpl["template"])
    try:
        return agents_registry.fork_from_template(
            body.template, slug, body.project, body.overrides)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/adopt-group")
async def adopt_agent_group(body: AdoptGroupIn):
    """Adopt a template group into a scope: fork each member with its overrides,
    then apply the group's `scope_defaults` to the project's autonomy record.
    Adopting twice is a no-op for members that would violate the one-per-(scope,
    role) constraint; they come back in `skipped` with the incumbent named."""
    try:
        res = agents_registry.adopt_group(body.group, body.project)
    except ValueError as e:
        raise HTTPException(404, str(e))
    res["scope_applied"] = _apply_group_to_project(
        body.project, res.get("scope_defaults") or {},
        [a["slug"] for a in res.get("adopted") or []])
    return res


def _apply_group_to_project(project_slug: str | None, defaults: dict,
                            adopted: list[str]) -> bool:
    """Land a group's non-agent half on the project record: its `scope_defaults`
    (the autonomy preset) and the adopted slugs on the scope's resident roster.
    A group's `scope_defaults.depth`, if a template still carries one, is ignored:
    project autonomy has no depth any more, because no sweep ever read it.
    A group carries settings, not just a member list (ask D4); this is where the
    settings half goes. No project named, nothing to apply (a user-scope adoption
    has no project record to write).

    The autonomy record is the same shape PUT /projects/{slug}/autonomy writes, so
    the preset expands into its trigger list exactly as it does there — a group
    must not be able to produce a project state the autonomy page cannot read."""
    if not project_slug or (not defaults and not adopted):
        return False
    preset = defaults.get("autonomy")
    if preset is not None and preset not in triggers.PRESETS:
        raise HTTPException(400, f"unknown autonomy preset in group: {preset}")
    from routes import projects as projects_route
    data = projects_route._load_registry()
    proj = (data.get("projects") or {}).get(project_slug)
    if not proj:
        raise HTTPException(404, f"project not found: {project_slug}")
    autonomy = dict(proj.get("autonomy") or {})
    if preset is not None:
        norm = triggers.preset_to_triggers(preset, limits=autonomy.get("limits") or None)
        autonomy.update({
            "preset": preset,
            "triggers": norm,
        })
    proj["autonomy"] = autonomy
    roster = [s for s in (proj.get("agents") or []) if isinstance(s, str)]
    proj["agents"] = roster + [s for s in adopted if s not in roster]
    projects_route._save_registry(data)
    return True


def _drop_from_project_roster(rec: dict) -> None:
    """Take a retired agent off its project's resident roster, so the roster is
    the scope's live residents rather than everyone who ever lived here."""
    slug, project_slug = rec.get("slug"), rec.get("project_slug")
    if not slug or not project_slug:
        return
    from routes import projects as projects_route
    data = projects_route._load_registry()
    proj = (data.get("projects") or {}).get(project_slug)
    if not proj or slug not in (proj.get("agents") or []):
        return
    proj["agents"] = [s for s in proj["agents"] if s != slug]
    projects_route._save_registry(data)


@router.get("/")
async def list_agents():
    """One unified roster: every Agent across both scopes, sourced from the
    agents registry (definition files + a maker's execution state).
    Per-channel bridge agents are excluded by the registry."""
    reg = agents_registry.list_agents()

    out: list[dict] = []
    for a in reg:
        out.append({
            "id": a.get("id"),
            "slug": a.get("slug"),
            "name": a.get("name"),
            "purpose": a.get("purpose"),
            "triggers": a.get("triggers") or [],
            "limits": a.get("limits") or {},
            "autonomy": triggers.triggers_to_preset(a.get("triggers") or []),
            # Per-agent autonomy MODE (autonomy-model 2026-07-18): the target
            # field name is `autonomy`, but until C7 rewrites AutonomyPanel to
            # read this shape, we surface it under `autonomy_mode` so nothing
            # breaks. Coherence-migration C7 flips the frontend + backend swap.
            "autonomy_mode": a.get("autonomy") or "reactive",
            "role": a.get("role"),
            "mentionable": bool(a.get("mentionable")),
            "delegatable": bool(a.get("delegatable")),
            # null = inherit, and it must stay null over the wire: defaulting it
            # here would make an unset agent look like a deliberate `standard`.
            "latitude": a.get("latitude"),
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


@router.get("/delegates")
async def list_delegates():
    """Claude Code subagents this instance can DELEGATE to.

    Deliberately not part of the roster above, and the difference is lifecycle,
    not schema — same markdown, same frontmatter, same parser. A roster agent is
    DISPATCHED: it has triggers, it can run on a schedule, it is a chat scope. A
    delegate only ever runs because another agent called it, so there is no
    trigger to enable and no autonomy of its own to grant. Merging the two lists
    would put rows in the roster with every control permanently inert.

    This answers the question the roster cannot: who can call whom.
    """
    return {"delegates": agents_registry.native_subagents()}


@router.get("/tools")
async def list_tools():
    return {"tools": known_tools()}


# ---- Build agents (Claude Code coding sessions) ----------------------------
# Distinct kind of Agent per ADR-008. Each Build agent is scoped to a cwd
# (either a Project directory or a freestanding scratch dir). The chat surface
# uses contextKey="agent:<slug>", which routes through chat_context's
# MakerAgentContextProvider (selected by the agent's `role`) and sets cwd at
# call_cloud spawn time. `build:<slug>` is DELETED as both a context key and a
# task scope: an agent has one address, and it is `agent:<slug>`.

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


def _agent_scope_key(rec: dict) -> str | None:
    """The task scope this agent's tickets live in, as a scope KEY.

    A key, not a path, because the UI has to be able to LINK to it and a
    filesystem path is not a link. This was only ever derived as a path, so the
    agent detail page listed ticket ids it had no way to address: every badge
    under "Tickets (N)" pointed at `/work?ticket=<id>`, a query string nothing
    parses. Returning the key is what lets those become real ticket links.
    """
    pslug = rec.get("project_slug")
    if pslug:
        return f"project:{pslug}"
    slug = rec.get("slug") or rec.get("id")
    return f"agent:{slug}" if slug else None


def _agent_scope_root(rec: dict) -> _P | None:
    """The tree this agent's tasks live in: its linked project dir, else a
    maker's own cwd. An agent with no project link and no tree of its own has an
    empty queue snapshot.

    Resolved from the scope key through task_scope, so the tree the server reads
    and the scope the UI links to cannot disagree."""
    from services import task_scope
    key = _agent_scope_key(rec)
    root = task_scope.root_for_context(key) if key else None
    if root is None and rec.get("role") == "maker" and rec.get("cwd"):
        # A maker that predates the build-agents registry still has a tree.
        root = rec["cwd"]
    return _P(root) if root else None


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
    and any pending `awaiting` prompts, plus the cross-link ids the UI uses to
    render the Involved-in section (B1)."""
    rec = agents_registry.get(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    snap = _agent_queue_snapshot(rec)
    # Cross-link denormalization (B1). Small, best-effort passes over the task
    # store + jobs feed + chat store. Bounded by limits so a big project cannot
    # make this endpoint slow. Fail-open: any error yields an empty list rather
    # than a 500 (the detail page must always render, even without cross-links).
    linked_ticket_ids: list[str] = []
    linked_run_ids: list[str] = []
    linked_chat_ids: list[str] = []
    try:
        root = _agent_scope_root(rec)
        if root is not None:
            items = tasks.list_tasks(root, include_done=True)
            slug = rec.get("slug") or ""
            assignee = f"agent:{slug}" if slug else None
            for t in items[:200]:
                tid = t.get("id")
                if not tid:
                    continue
                a = (t.get("assignee") or "").strip()
                if assignee and a == assignee:
                    linked_ticket_ids.append(tid)
                elif not a and root is not None:
                    # scope-implied membership: no explicit assignee, agent works
                    # its scope by default. Include so the agent has something to
                    # show; the UI can differentiate assigned vs scope-implied.
                    linked_ticket_ids.append(tid)
    except Exception:
        pass
    try:
        from services import jobs as _jobs
        job_recs = _jobs.read(agent_id=rec.get("id"), limit=100)
        linked_run_ids = [j.get("id") for j in job_recs if j.get("id")]
    except Exception:
        pass
    try:
        from services import chat_store as _cs
        slug = rec.get("slug") or ""
        if slug:
            # Cheap contextKey-prefix scan: chats whose thread key names this
            # agent's scope. Not a graph query; just what is easy to compute
            # without a schema change.
            ck = f"agent:{slug}"
            threads = _cs.list_threads()
            for th in threads:
                if (th.get("context_key") or "").startswith(ck):
                    linked_chat_ids.append(th.get("id"))
    except Exception:
        pass
    return {
        **rec,
        "autonomy": triggers.triggers_to_preset(rec.get("triggers") or []),
        # See list_agents comment. Autonomy-model per-agent MODE lives here until
        # C7 flips the field name.
        "autonomy_mode": rec.get("autonomy") or "reactive",
        "mentionable": bool(rec.get("mentionable")),
        "delegatable": bool(rec.get("delegatable")),
        "queue": snap["queue"],
        "awaiting": snap["awaiting"],
        "linked_ticket_ids": linked_ticket_ids[:50],
        # Every id above came from ONE store, so they share one scope. Sent
        # because a ticket is addressed by (scope, id) and the client cannot
        # re-derive the scope without duplicating the routing rule above.
        "linked_ticket_scope": _agent_scope_key(rec),
        "linked_run_ids": linked_run_ids[:50],
        "linked_chat_ids": linked_chat_ids[:50],
        "activation": _activation_chain(rec),
        # What this role may write, and the tools that follow from it. Rendered
        # on the page so the contract is legible without reading the table.
        "authority": {
            "writes": sorted(agent_authority.writes_for_role(rec.get("role"))),
            "describes": agent_authority.describe(rec.get("role")),
            "tools": agent_authority.narrow_tools(rec.get("role"), rec.get("tools")),
        },
    }


def _activation_chain(rec: dict) -> dict:
    """Why this agent does or does not run, as an ordered chain of conditions.

    "Autonomy is on" has never been answerable from this page, because the answer
    is a conjunction spread across three places: a global brake, the agent's own
    declared mode, and whether anything can trigger it. An agent that has never
    run therefore rendered as empty panels with no explanation, which reads as a
    broken page rather than as a correctly-idle agent.

    So the chain is returned in order, each link with its own verdict, and the
    FIRST failing link is named. This is a read-only explanation of state that
    already exists; it grants nothing and gates nothing.
    """
    from services import autonomy_override

    brake = autonomy_override.read()
    autonomy = rec.get("autonomy") or "reactive"
    triggers = rec.get("triggers") or []
    # A manual-only trigger set cannot start anything on its own, so it is not a
    # way in. Distinguish "no triggers" from "only manual".
    auto_triggers = [t for t in triggers if isinstance(t, dict) and t.get("kind") != "manual"]

    links = [
        {
            "id": "brake",
            "label": "Global autonomy brake is clear",
            "ok": not brake.get("engaged"),
            "detail": (brake.get("reason") or "engaged") if brake.get("engaged") else "clear",
        },
        {
            "id": "autonomy",
            "label": "Agent may run unattended",
            "ok": autonomy in ("supervised", "autonomous"),
            "detail": autonomy,
        },
        {
            "id": "trigger",
            "label": "Something can start it",
            "ok": bool(auto_triggers),
            "detail": (", ".join(sorted({str(t.get("kind")) for t in auto_triggers}))
                       if auto_triggers else
                       ("manual invocation only" if triggers else "no triggers declared")),
        },
    ]
    blocking = next((l for l in links if not l["ok"]), None)
    return {
        "links": links,
        "blocked_by": blocking["id"] if blocking else None,
        "runs_unattended": blocking is None,
        "ever_ran": bool(rec.get("last_active")),
        "last_active": rec.get("last_active"),
    }


class AgentDefinitionUpdate(BaseModel):
    name: str | None = None
    purpose: str | None = None
    triggers: list[dict] | None = None
    limits: dict | None = None
    capabilities: list[str] | None = None
    role: str | None = None            # closed enum: maker|reviewer|triager|keeper|broker
                                       # ("" clears it; unknown -> 400)
    latitude: str | None = None        # decision rights: strict | standard | open
                                       # ("" or "standard" clears it to inherit;
                                       # unknown -> 400)
    autonomy: str | None = None        # per-agent mode: reactive | supervised | autonomous
                                       # (autonomy-model 2026-07-18; "" or the
                                       # reactive default clears the emitted field)
    # Interaction opt-ins, independent of each other and of role. Both default
    # False: an agent is not conversational and not delegatable until it is
    # deliberately made so.
    mentionable: bool | None = None     # may be @-mentioned in a chat
    delegatable: bool | None = None     # may be handed a ticket
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
            latitude=body.latitude,
            autonomy=body.autonomy,
            mentionable=body.mentionable,
            delegatable=body.delegatable,
            body=body.definition,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/")
async def create_agent(body: AgentCreate):
    """Create an Agent in the registry (a markdown definition file). The
    persona/instructions become the definition body; there is no separate memory
    engine to provision. A maker with a working tree is created via POST
    /agents/build. Triggers and limits are edited afterward on the detail page
    (PUT /agents/{id})."""
    parts: list[str] = []
    if body.persona.strip():
        parts.append(body.persona.strip())
    if body.human.strip():
        parts.append("## Operator context\n\n" + body.human.strip())
    try:
        return agents_registry.create(
            slug=agents_registry.mint_slug(body.name),
            name=body.name,
            memory="notes",
            role=body.role,
            latitude=body.latitude,
            autonomy=body.autonomy,
            project_slug=body.project_slug,
            body="\n\n".join(parts),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str):
    """Retire an agent from the registry: it leaves the roster while the
    declarative definition is preserved (retired flag). A maker also flips its
    execution state."""
    rec = agents_registry.get(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    agents_registry.retire(rec["slug"])
    _drop_from_project_roster(rec)
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
