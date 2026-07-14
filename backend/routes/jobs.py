"""Jobs API — one feed for every bounded execution.

`GET /jobs`      list/filter by agent_id, project_slug, status, limit
`GET /jobs/{id}` single Job detail

Backed by services.jobs (append-only `.runtime/jobs.jsonl`). This is the new
top-level activity surface; `/agents/runs` (routes/agent_runs.py) is a thin
backward-compatible alias over the same data until Phase 5.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services import jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])


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
