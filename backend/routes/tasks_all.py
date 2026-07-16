"""Scope-generic Task API (the "Work" surface, plan D4 / sub-decision b).

The task store is per-tree: the workshop root, every project, every registry
assistant scope, and every build agent all keep their own `.tasks.json`. Before
this route there was no cross-scope view, so work parked in an agent scope or the
root was invisible in the UI. This aggregates every scope's tasks into one feed
and exposes CRUD on a single task by its scope-qualified id.

Terminology: the store, CLI, and API all say `task`. The UI may call these
"tickets" in prose, but every identifier here stays `task`.

Scope keys mirror `task_scope.root_for_context`:
  workshop        -> the top-level `.tasks.json`
  project:<slug>  -> projects/<slug>
  agent:<slug>    -> .runtime/agent-scopes/<slug> (assistant runtime)
  build:<slug>    -> a build agent's linked project dir or its own cwd

Cross-scope listing is cheap: `tasks.list_tasks` reconciles, and reconcile
short-circuits on an unchanged export hash, so a sweep across scopes does no work
when nothing moved.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import tasks, task_scope, task_dedup, jobs, build_agents, ownership

router = APIRouter(prefix="/tasks", tags=["tasks"])

_PROJECTS_REGISTRY = Path(os.environ.get(
    "WORKSHOP_PROJECTS_REGISTRY", str(ownership._RUNTIME / "projects.json")))
_AGENT_SCOPES_DIR = ownership._RUNTIME / "agent-scopes"

# The sweep launcher (project_ralph.sh) and the repo root Popen runs from.
_WORKSHOP_ROOT = task_scope.WORKSHOP_ROOT
_SCRIPTS_ROOT = _WORKSHOP_ROOT / "scripts"


# ---- Scope resolution ------------------------------------------------------

def _root_for_scope(scope: str | None) -> Path | None:
    """Resolve a scope key to its task-store root. `task_scope` owns every kind
    (workshop / project: / agent: / build:); an unknown scope returns None."""
    if not scope:
        return None
    return task_scope.root_for_context(scope)


def _project_slugs() -> list[str]:
    if not _PROJECTS_REGISTRY.exists():
        return []
    try:
        reg = json.loads(_PROJECTS_REGISTRY.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return list((reg.get("projects") or {}).keys())


def _scope_roots() -> list[tuple[str, Path]]:
    """Every task scope that actually has a store, deduped by resolved path so a
    build agent sharing a project's tree is not counted twice. Priority order:
    workshop root, projects, assistant scopes, build agents. Only a scope with an
    existing `.tasks.json` or `TODO.md` is surfaced, keeping the sweep cheap and
    non-destructive (no scope is created just by listing)."""
    seen: set[str] = set()
    order: list[tuple[str, Path]] = []

    def add(scope: str, root: Path | None) -> None:
        if root is None:
            return
        try:
            rp = root.resolve()
        except OSError:
            return
        if not rp.exists():
            return
        if not (rp / tasks.STORE_NAME).exists() and not (rp / tasks.EXPORT_NAME).exists():
            return
        key = str(rp)
        if key in seen:
            return
        seen.add(key)
        order.append((scope, rp))

    add("workshop", _root_for_scope("workshop"))
    for slug in _project_slugs():
        add(f"project:{slug}", _root_for_scope(f"project:{slug}"))
    if _AGENT_SCOPES_DIR.exists():
        for d in sorted(_AGENT_SCOPES_DIR.iterdir()):
            if d.is_dir():
                add(f"agent:{d.name}", d)
    for a in build_agents.list_agents():
        slug = a.get("slug")
        if slug:
            add(f"build:{slug}", _root_for_scope(f"build:{slug}"))
    return order


# ---- Aggregate list --------------------------------------------------------

@router.get("")
async def list_all(
    status: str | None = None,
    scope: str | None = None,
    include_done: bool = True,
) -> dict:
    """Tasks across every scope, each tagged with its resolved `scope` key.
    Optional filters: `status` (one of the seven task statuses) and `scope`
    (restrict to a single scope key)."""
    out: list[dict] = []
    scopes = 0
    for skey, root in _scope_roots():
        if scope and skey != scope:
            continue
        scopes += 1
        try:
            items = tasks.list_tasks(root, include_done=include_done)
        except Exception:
            continue
        for t in items:
            if status and t.get("status") != status:
                continue
            rec = dict(t)
            rec["scope"] = skey
            out.append(rec)
    return {"tasks": out, "count": len(out), "scopes": scopes}


@router.get("/scopes")
async def list_scopes() -> dict:
    """The scope keys that currently have a task store, for the create/filter
    controls. Cheap directory + registry scan, no task load."""
    return {"scopes": [skey for skey, _ in _scope_roots()]}


# ---- Autonomy scopes (central management surface) --------------------------
# One row per manageable autonomy scope: every project + every registry agent.
# The per-scope autonomy endpoints (routes.projects.get_autonomy and the agent
# detail endpoint) own the preset/queue/run summaries; this route reuses those
# exact helpers so the central Autonomy surface can never disagree with the
# per-scope panels. Lives under this router (prefix /tasks) at
# GET /api/tasks/autonomy-scopes, since tasks_all is the mounted router here.


def _last_run_summary(last: dict | None) -> dict | None:
    """A run down to the three fields the central list shows (id/status/ended)."""
    if not last:
        return None
    return {
        "id": last.get("id"),
        "status": last.get("status"),
        "ended_at": last.get("ended_at"),
    }


def _agent_run_state(agent_id: str) -> tuple[bool, dict | None]:
    """(running, last_job) for an agent scope, derived from the Job store the
    same way the project run-state is."""
    recs = jobs.read(agent_id=agent_id, limit=1)
    last = recs[0] if recs else None
    running = bool(last and last.get("status") == "running")
    return running, last


@router.get("/autonomy-scopes")
async def autonomy_scopes() -> dict:
    """Every manageable autonomy scope, summarized consistently with the
    per-scope autonomy endpoints. Each row:
      {scope_key, kind, title, preset, depth, running,
       queue:{ready,doing,in_review,blocked}, awaiting_count,
       last_run:{id,status,ended_at}|null}
    Bounded: one pass over the projects registry + the agents roster."""
    # Lazy imports keep this route free of any module-load ordering concern with
    # the project/agent routers (neither imports tasks_all, so there is no cycle).
    from routes import projects as projects_route
    from routes import agents as agents_route
    from services import agents_registry, triggers

    rows: list[dict] = []

    # ---- Projects ----
    reg = projects_route._load_registry()
    for slug, raw in (reg.get("projects") or {}).items():
        if raw.get("state") == "retired":
            continue
        autonomy = raw.get("autonomy") or {}
        trigs = projects_route._stored_triggers(autonomy)
        preset = autonomy.get("preset") or triggers.triggers_to_preset(trigs)
        running, last = projects_route._run_state(slug)
        snap = projects_route._queue_snapshot(projects_route._project_dir(slug))
        q = snap["queue"]
        rows.append({
            "scope_key": f"project:{slug}",
            "kind": "project",
            "title": raw.get("title") or slug,
            "preset": preset,
            "depth": autonomy.get("depth") or "standard",
            "running": running,
            "queue": {"ready": q["ready"], "doing": q["doing"],
                      "in_review": q["in_review"], "blocked": q["blocked"]},
            "awaiting_count": len(snap["awaiting"]),
            "last_run": _last_run_summary(last),
        })

    # ---- Agents (assistant + builder registry agents, incl. memory-keeper) ----
    for rec in agents_registry.list_agents():
        slug = rec.get("slug")
        if not slug:
            continue
        preset = triggers.triggers_to_preset(rec.get("triggers") or [])
        snap = agents_route._agent_queue_snapshot(rec)
        q = snap["queue"]
        running, last = _agent_run_state(rec.get("id") or slug)
        rows.append({
            "scope_key": f"agent:{slug}",
            "kind": "agent",
            "title": rec.get("name") or slug,
            "preset": preset,
            "depth": rec.get("depth") or "standard",
            "running": running,
            "queue": {"ready": q["ready"], "doing": q["doing"],
                      "in_review": q["in_review"], "blocked": q["blocked"]},
            "awaiting_count": len(snap["awaiting"]),
            "last_run": _last_run_summary(last),
        })

    return {"scopes": rows, "count": len(rows)}


# ---- Create ----------------------------------------------------------------

class TaskCreateIn(BaseModel):
    scope: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=4000)
    priority: int = 0
    effort: str | None = None  # s|m|l hint; set post-create, never a gate


@router.post("")
async def create(body: TaskCreateIn) -> dict:
    """File a task into a scope through the dedup path, so a manual ticket
    dedupes against open tasks exactly like a chat-filed one. Returns the task
    plus the dedup decision (exact | merge | related | distinct)."""
    root = _root_for_scope(body.scope)
    if root is None:
        raise HTTPException(400, f"unknown scope: {body.scope}")
    try:
        res = task_dedup.dedup_add(
            root,
            text=body.text,
            priority=int(body.priority or 0),
            source={"kind": "operator", "ref": "work-ui"},
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    task = dict(res["task"]) if res.get("task") else None
    # `effort` is a hint the create path did not carry; stamp it on a freshly
    # created task (not a merge, which would overwrite the existing one's).
    if task is not None and body.effort and res.get("decision") not in ("exact", "merge"):
        try:
            updated = tasks.update(root, task["id"], effort=body.effort)
            if updated:
                task = dict(updated)
        except ValueError:
            pass
    if task is not None:
        task["scope"] = body.scope
    return {
        "task": task,
        "decision": res.get("decision"),
        "matched_id": res.get("matched_id"),
        "cosine": res.get("cosine"),
        "message": res.get("message"),
    }


# ---- Single task -----------------------------------------------------------

class TaskAcceptanceIn(BaseModel):
    # Mirrors the store's acceptance shape (services/tasks.py Canonical Task):
    # {"kind": "reviewer"|"command"|"operator", "spec": str|None}. `spec` carries
    # the command line when kind == "command"; reviewer/operator ignore it.
    kind: str
    spec: str | None = None


class TaskPatchIn(BaseModel):
    status: str | None = None
    text: str | None = None
    priority: int | None = None
    assignee: str | None = None
    effort: str | None = None
    # Advanced fields tasks.update() already supports; None leaves each unchanged.
    acceptance: TaskAcceptanceIn | None = None
    max_attempts: int | None = None
    depends_on: list[str] | None = None


@router.get("/{scope}/{task_id}")
async def get_one(scope: str, task_id: str) -> dict:
    root = _root_for_scope(scope)
    if root is None:
        raise HTTPException(400, f"unknown scope: {scope}")
    t = tasks.get_task(root, task_id)
    if t is None:
        raise HTTPException(404, f"task not found: {task_id}")
    rec = dict(t)
    rec["scope"] = scope
    return rec


_ACCEPTANCE_KINDS = {"reviewer", "command", "operator"}


@router.patch("/{scope}/{task_id}")
async def patch_one(scope: str, task_id: str, body: TaskPatchIn) -> dict:
    root = _root_for_scope(scope)
    if root is None:
        raise HTTPException(400, f"unknown scope: {scope}")
    if all(v is None for v in (
        body.status, body.text, body.priority, body.assignee, body.effort,
        body.acceptance, body.max_attempts, body.depends_on,
    )):
        raise HTTPException(400, "no fields to update")
    # Acceptance: an empty/none kind clears it (falsy dict -> store sets None); a
    # real kind must be one of the three the store recognizes. tasks.update reads
    # None as "leave unchanged", so only forward a value when one was sent.
    acceptance_arg = None
    if body.acceptance is not None:
        kind = (body.acceptance.kind or "").strip()
        if kind in ("", "none"):
            acceptance_arg = {}  # falsy -> update() clears acceptance to None
        elif kind in _ACCEPTANCE_KINDS:
            acceptance_arg = {"kind": kind, "spec": body.acceptance.spec}
        else:
            raise HTTPException(400, f"acceptance kind must be one of {sorted(_ACCEPTANCE_KINDS)} or none")
    try:
        t = tasks.update(
            root, task_id,
            status=body.status,
            text=body.text,
            priority=body.priority,
            assignee=body.assignee,
            effort=body.effort,
            acceptance=acceptance_arg,
            max_attempts=body.max_attempts,
            depends_on=body.depends_on,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if t is None:
        raise HTTPException(404, f"task not found: {task_id}")
    rec = dict(t)
    rec["scope"] = scope
    return rec


@router.delete("/{scope}/{task_id}")
async def delete_one(scope: str, task_id: str) -> dict:
    root = _root_for_scope(scope)
    if root is None:
        raise HTTPException(400, f"unknown scope: {scope}")
    if not tasks.remove(root, task_id):
        raise HTTPException(404, f"task not found: {task_id}")
    return {"deleted": True}


@router.get("/{scope}/{task_id}/jobs")
async def task_jobs(scope: str, task_id: str) -> dict:
    """The Jobs (runs) this task produced, enriched like the /jobs feed so each
    run carries its title, status, and receipt summary."""
    recs = jobs.read(task_id=task_id, limit=200)
    try:
        from routes.jobs import _enrich, _name_map
        names = _name_map()
        cache: dict = {}
        enriched = [_enrich(r, names, cache) for r in recs]
    except Exception:
        enriched = recs
    return {"jobs": enriched, "count": len(enriched)}


# ---- Run now ---------------------------------------------------------------
# Chat/UI "run this task now": open a Job and launch a detached single-task sweep
# (project_ralph.sh --task-id) so work starts in seconds instead of waiting for
# the ~20-minute cron. The sweep owns claim/lease/fence/acceptance/receipt/budget
# admission; --force marks a deliberate operator action that legitimately bypasses
# the paused-autonomy flag, exactly like the existing per-project run endpoint.


def _sweep_dir_str(scope: str) -> str | None:
    """The DIR string the sweep keys its per-tree flock on, matching how
    project_ralph.sh resolves it: a project sweep reads the registry `dir` string
    verbatim; a --dir sweep uses the path we pass. Returns None for an unknown
    scope. Used only for the busy pre-check; a mismatch degrades to launching and
    letting the sweep's own flock abort cleanly, never to a wrong answer."""
    if scope.startswith("project:"):
        slug = scope.split(":", 1)[1]
        try:
            reg = json.loads(_PROJECTS_REGISTRY.read_text())
            d = ((reg.get("projects") or {}).get(slug) or {}).get("dir")
            if d:
                return str(d)
        except (json.JSONDecodeError, OSError):
            pass
    root = _root_for_scope(scope)
    return str(root) if root is not None else None


def _tree_busy(dir_str: str) -> bool:
    """True when a sweep already holds this tree's flock. Mirrors the exact lock
    path project_ralph.sh derives (md5 of `echo "$DIR"`, first 12 hex chars) and
    probes it non-blocking. The probe releases immediately; the launched sweep
    re-locks and aborts cleanly if we lose the (tiny) race."""
    digest = hashlib.md5((dir_str + "\n").encode()).hexdigest()[:12]
    lock = Path(f"/tmp/workshop-project-ralph-{digest}.lock")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def launch_task_run(scope: str, task_id: str) -> dict:
    """Open a manual Job and Popen a detached single-task sweep against `scope`
    for exactly `task_id`. Shared by the /tasks run endpoint and the chat
    spawn-and-run path so the Popen pattern lives in one place.

    Scope -> launcher (dir resolved via task_scope.root_for_context):
      project:<slug> -> project_ralph.sh <slug> --force --job-id --task-id
      workshop       -> ... --dir <workshop root> --force --job-id --task-id
      agent:<slug>   -> ... --dir <scope root> --assignee agent:<slug> --force ...
      build:<slug>   -> ... --dir <build agent root> --force --job-id --task-id

    Raises HTTPException(409) when a sweep already holds the tree, (400) on an
    unknown scope, (500) when the driver is missing. Returns {ok, job_id, scope,
    task_id}."""
    root = _root_for_scope(scope)
    if root is None:
        raise HTTPException(400, f"unknown scope: {scope}")
    script = _SCRIPTS_ROOT / "project_ralph.sh"
    if not script.exists():
        raise HTTPException(500, f"driver missing: {script}")

    dir_str = _sweep_dir_str(scope)
    if dir_str and _tree_busy(dir_str):
        raise HTTPException(409, f"a sweep is already running against {scope}; try again when it finishes")

    start_kwargs: dict = {"trigger": "manual", "status": "running", "task_id": task_id}
    if scope.startswith("project:"):
        slug = scope.split(":", 1)[1]
        start_kwargs["project_slug"] = slug
        cmd_tail = [slug]
    elif scope == "workshop":
        start_kwargs["project_slug"] = "workshop"
        cmd_tail = ["--dir", str(root)]
    elif scope.startswith("agent:"):
        slug = scope.split(":", 1)[1]
        start_kwargs["agent_id"] = f"agent:{slug}"
        cmd_tail = ["--dir", str(root), "--assignee", f"agent:{slug}"]
    elif scope.startswith("build:"):
        cmd_tail = ["--dir", str(root)]
    else:
        raise HTTPException(400, f"scope not runnable: {scope}")

    job = jobs.start(**start_kwargs)
    try:
        log_dir = root / ".runtime"
        log_dir.mkdir(parents=True, exist_ok=True)
        logf: object = open(log_dir / "ralph.log", "a")  # noqa: SIM115 (handed to child)
    except OSError:
        logf = subprocess.DEVNULL
    cmd = ["bash", str(script), *cmd_tail, "--force", "--job-id", job["id"], "--task-id", task_id]
    subprocess.Popen(
        cmd,
        cwd=str(_WORKSHOP_ROOT),
        stdout=logf,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return {"ok": True, "job_id": job["id"], "scope": scope, "task_id": task_id}


@router.post("/{scope}/{task_id}/run")
async def run_task(scope: str, task_id: str) -> dict:
    """Run exactly this task now. 409 if a sweep already holds the tree. Single
    operator: no confirmation gate, no allowlist."""
    root = _root_for_scope(scope)
    if root is None:
        raise HTTPException(400, f"unknown scope: {scope}")
    if tasks.get_task(root, task_id) is None:
        raise HTTPException(404, f"task not found: {task_id}")
    return launch_task_run(scope, task_id)
