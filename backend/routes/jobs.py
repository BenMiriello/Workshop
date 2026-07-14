"""Jobs API — one feed for every bounded execution.

`GET /jobs`      list/filter by agent_id, project_slug, status, limit
`GET /jobs/{id}` single Job detail

Backed by services.jobs (append-only `.runtime/jobs.jsonl`). This is the new
top-level activity surface; `/agents/runs` (routes/agent_runs.py) is a thin
backward-compatible alias over the same data until Phase 5.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import jobs, tasks, task_scope, agents_registry

router = APIRouter(prefix="/jobs", tags=["jobs"])

# --- Ticket-shaping: enrich raw Job records into readable tickets -----------
# A stored Job is machine-shaped: agent_id is a raw Letta UUID, trigger is
# jargon ("loop"), evidence is a freeform dump, and failures hide inside it with
# no exit_code. We compute display fields at read time so every job reads like a
# ticket (a name, a title, an operator-language trigger, a real error) without
# migrating the append-only log.

_TRIGGER_LABELS = {
    "loop": "Autonomous",
    "cron": "Scheduled",
    "event": "Event",
    "manual": "Run now",
}


def _name_map() -> dict[str, str]:
    """agent-id/slug/letta-id -> human name, built once per request."""
    m: dict[str, str] = {}
    try:
        for a in agents_registry.list_agents(include_retired=True):
            name = a.get("name") or a.get("slug")
            if not name:
                continue
            for key in (a.get("slug"), a.get("id"), a.get("letta_agent_id")):
                if key:
                    m[key] = name
    except Exception:
        pass
    return m


def _display_agent(agent_id: str | None, names: dict[str, str]) -> str | None:
    """Resolve a job's agent_id to a human name; degrade gracefully so the
    operator never sees a bare UUID."""
    if not agent_id:
        return None
    if agent_id in names:
        return names[agent_id]
    if agent_id.startswith("agent:"):
        slug = agent_id.split(":", 1)[1]
        return names.get(slug, slug)
    if agent_id.startswith("agent-"):
        # Raw Letta UUID with no registry definition (legacy heartbeat agent).
        return f"Letta {agent_id[6:14]}"
    return agent_id


def _friendly_error(err: str | None) -> str:
    if not err or not err.strip():
        return "Failed (no detail recorded)."
    s = err.strip()
    if s.startswith("ollama_failed:"):
        detail = s[len("ollama_failed:"):].strip()
        base = "Local model server (Ollama) was unreachable."
        return f"{base} {detail}" if detail else base
    return s


def _title(job: dict, agent_disp: str | None) -> str:
    trig = job.get("trigger")
    label = _TRIGGER_LABELS.get(trig, (trig or "Run").capitalize())
    binding = job.get("project_slug") or agent_disp or "unbound"
    if trig == "loop" and not job.get("project_slug"):
        return f"Heartbeat · {binding}"
    return f"{label} · {binding}"


def _enrich(job: dict, names: dict[str, str]) -> dict:
    j = dict(job)
    trig = job.get("trigger")
    j["trigger_label"] = _TRIGGER_LABELS.get(trig, (trig or "Run").capitalize())
    agent_disp = _display_agent(job.get("agent_id"), names)
    if agent_disp:
        j["agent_name"] = agent_disp
    ev = job.get("evidence")
    if job.get("status") == "failed":
        j["error"] = _friendly_error(ev if isinstance(ev, str) else None)
    elif isinstance(ev, str) and ev.strip():
        # A short freeform evidence string doubles as the job's one-line summary.
        j["summary"] = ev.strip()[:240]
    j["title"] = _title(job, agent_disp)
    return j

# Operator responses to an awaiting_input job, expressed as a task transition.
# Acting changes the scope store's mtime, so the dispatcher's skip-unchanged
# guard releases the scope on the next tick and the sweep resumes on its own.
_ACTIONS = {
    "approve": ("done", "in_review"),    # accept the worker's completion
    "reject": ("todo", "in_review"),     # send an in_review task back for rework
    "unblock": ("todo", "blocked"),      # blocker resolved: return to the queue
    "cancel": ("cancelled", None),       # drop the task (any state)
}


@router.get("")
async def list_jobs(
    agent_id: str | None = None,
    project_slug: str | None = None,
    status: str | None = None,
    task_id: str | None = None,
    parent_id: str | None = None,
    limit: int = 50,
) -> dict:
    records = jobs.read(
        agent_id=agent_id,
        project_slug=project_slug,
        status=status,
        task_id=task_id,
        parent_id=parent_id,
        limit=limit,
    )
    names = _name_map()
    enriched = [_enrich(r, names) for r in records]
    return {"jobs": enriched, "count": len(enriched)}


@router.get("/{job_id}")
async def get_job(job_id: str) -> dict:
    rec = jobs.get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    return _enrich(rec, _name_map())


class JobTaskAction(BaseModel):
    action: str  # approve | reject | unblock | cancel
    note: str | None = None


@router.post("/{job_id}/tasks/{task_id}/action")
async def act_on_job_task(job_id: str, task_id: str, body: JobTaskAction) -> dict:
    """Resolve one awaiting_input job's bound task: approve / reject / unblock /
    cancel. Applies the status transition on the job's scope store (project or
    agent), records the operator note as evidence, and clears the awaiting
    payload. The dispatcher re-runs the scope next tick because the store moved."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"job not found: {job_id}")
    if body.action not in _ACTIONS:
        raise HTTPException(400, f"unknown action: {body.action}")
    root = task_scope.root_for_job(job)
    if root is None:
        raise HTTPException(400, "this job has no resolvable task scope")
    new_status, require_from = _ACTIONS[body.action]
    task = tasks.get_task(root, task_id)
    if task is None:
        raise HTTPException(404, f"task not found in scope: {task_id}")
    if require_from and task.get("status") != require_from:
        raise HTTPException(
            409, f"task is {task.get('status')}, not {require_from}; cannot {body.action}")
    # Status change auto-clears the awaiting payload (update() drops it on exit
    # from a waiting state). The operator note rides along as evidence.
    updated = tasks.update(
        root, task_id, status=new_status,
        evidence={"kind": "operator", "ref": body.note} if body.note else None)

    # If the operator has now answered every task this job was waiting on, the
    # job is no longer awaiting input: flip it to done so it drops off Home's
    # attention strip. (The next dispatcher tick re-runs the scope on its own,
    # since the store moved; a fresh sweep opens its own Job.)
    job_cleared = False
    if job.get("status") == "awaiting_input":
        ev = job.get("evidence")
        waited_ids = [e.get("id") for e in ev if isinstance(e, dict) and e.get("id")] if isinstance(ev, list) else []
        still_waiting = [
            tid for tid in waited_ids
            if (t := tasks.get_task(root, tid)) and t.get("status") in ("blocked", "in_review")
        ]
        if not still_waiting:
            jobs.finish(job_id, status="done")
            job_cleared = True
    return {"ok": True, "action": body.action, "task": updated, "job_cleared": job_cleared}


@router.post("/{job_id}/dismiss")
async def dismiss_job(job_id: str) -> dict:
    """The always-available escape hatch: clear a job off the attention feed
    regardless of its binding or task state. Needed because a malformed
    awaiting_input job (unbound sweep, phantom or question-less task) has no
    resolvable scope, so the per-task actions cannot act on it, and the operator
    must never be stuck with a permanent, unresolvable warning. Finishes the job
    `cancelled`; it does NOT touch any task store (there may be none to touch)."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"job not found: {job_id}")
    jobs.finish(job_id, status="cancelled")
    return {"ok": True, "dismissed": True}
