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

from services import jobs, tasks, task_scope

router = APIRouter(prefix="/jobs", tags=["jobs"])

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
    return {"jobs": records, "count": len(records)}


@router.get("/{job_id}")
async def get_job(job_id: str) -> dict:
    rec = jobs.get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    return rec


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
