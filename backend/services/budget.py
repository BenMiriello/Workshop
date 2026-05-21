"""Per-agent call budget — enforces metadata.max_calls_per_hour.

Reads agent.metadata.max_calls_per_hour from Letta (default 60). Counts
records in .runtime/agent-runs-completed.jsonl with matching agent_id whose
ts is within the trailing hour. Returns (used, limit, remaining, over).

The cron consumer (Layer 4) hits GET /agents/{id}/budget before firing
claude -p — if `over: true`, it skips and writes status=throttled to the
completed log so the budget is self-stabilizing.

Tokens are not tracked because claude -p doesn't surface a token count to
the cron script — call_count is the realistic proxy.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

COMPLETED_PATH = Path(os.environ.get(
    "WORKSHOP_COMPLETED_RUNS",
    "/home/clawd-a/workshop/.runtime/agent-runs-completed.jsonl",
))
DEFAULT_LIMIT = 60
WINDOW_S = 3600


def recent_calls(agent_id: str, window_s: int = WINDOW_S) -> int:
    if not COMPLETED_PATH.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_s)
    count = 0
    for line in COMPLETED_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("agent_id") != agent_id:
            continue
        ts = rec.get("ts") or rec.get("ended_at")
        if not ts:
            continue
        try:
            rec_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if rec_dt >= cutoff:
            count += 1
    return count


def budget_for(agent_metadata: dict, agent_id: str) -> dict:
    limit = int(agent_metadata.get("max_calls_per_hour", DEFAULT_LIMIT) or DEFAULT_LIMIT)
    used = recent_calls(agent_id)
    remaining = max(0, limit - used)
    return {
        "agent_id": agent_id,
        "window_s": WINDOW_S,
        "limit": limit,
        "used": used,
        "remaining": remaining,
        "over": used >= limit,
    }
