"""Agent activity feed — backward-compatible alias over the Job store.

`GET /agents/runs` used to tail `.runtime/agent-runs.jsonl`. As of the Phase 1
platform redesign, every bounded execution is a Job (services.jobs). This route
now re-serves Job records in the OLD run shape so pre-redesign callers keep
working. New surfaces should read `GET /jobs` directly.

It MUST stay mounted before `agents.router` in main.py: `/agents/runs` only wins
over `GET /agents/{agent_id}` because of that ordering. Do not delete or reorder
until the alias is retired (Phase 5).
"""
from __future__ import annotations

from fastapi import APIRouter

from services import jobs

router = APIRouter(prefix="/agents", tags=["agents"])


def _to_run_shape(job: dict) -> dict:
    """Map a Job record to the legacy run shape the old feed exposed."""
    started = job.get("started_at")
    ended = job.get("ended_at")
    duration_s = None
    if started and ended:
        try:
            from datetime import datetime
            s = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            e = datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
            duration_s = round((e - s).total_seconds())
        except (ValueError, TypeError):
            duration_s = None
    return {
        "ts": started,
        "agent_id": job.get("agent_id"),
        "task": job.get("trigger"),
        "status": job.get("status"),
        "exit_code": job.get("exit_code"),
        "duration_s": duration_s,
        "job_id": job.get("id"),
        "project_slug": job.get("project_slug"),
    }


@router.get("/runs")
async def list_runs(limit: int = 50, agent: str | None = None):
    """Most recent Jobs, served in the legacy run shape."""
    records = jobs.read(agent_id=agent, limit=limit)
    runs = [_to_run_shape(r) for r in records]
    return {"runs": runs, "count": len(runs), "source": "jobs.jsonl"}
