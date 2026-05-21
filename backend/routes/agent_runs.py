"""Agent activity feed.

Surfaces recent cron-agent runs to the Workshop UI. The cron_agent.sh script
will (per Phase 5a TODO) append structured records to
`~/workshop/.runtime/agent-runs.jsonl`. This endpoint tails that file and
returns the most recent runs.

Until 5a lands, this endpoint returns whatever exists or an empty list —
it never fails, so the frontend Apps page can render an empty activity feed
on cold systems without a 500.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter


router = APIRouter(prefix="/agents", tags=["agents"])

RUNS_LOG = Path(os.environ.get(
    "WORKSHOP_AGENT_RUNS_LOG",
    "/home/clawd-a/workshop/.runtime/agent-runs.jsonl",
))


@router.get("/runs")
async def list_runs(limit: int = 50, agent: str | None = None):
    """Most recent agent runs (cron, event-triggered, manual)."""
    limit = max(1, min(limit, 500))
    if not RUNS_LOG.exists():
        return {"runs": [], "count": 0, "log_path": str(RUNS_LOG), "note": "no runs logged yet"}

    out = []
    for line in reversed(RUNS_LOG.read_text().splitlines()):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if agent and rec.get("agent_id") != agent:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return {"runs": out, "count": len(out), "log_path": str(RUNS_LOG)}
