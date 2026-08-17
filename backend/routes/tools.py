"""HTTP dispatch for the host-side tool implementations.

One endpoint per tool: POST /tools/{tool_name} with a JSON body of kwargs,
returning {"result": <string>} or {"error": <string>}. The implementations live
in services/letta_tools/ (workshop.py, local.py, cloud.py, notify.py,
health.py), which is a misleading directory name kept only because renaming it
is a separate change.

HISTORY, because this module's shape only makes sense with it. The split
existed because Letta ran tool functions inside its own Docker container with
no access to the host filesystem or to host localhost services, but which could
reach the host backend at host.docker.internal:8000. So implementations stayed
here and Letta registered thin HTTP proxies that POSTed in. Letta was removed in
the 2026-07-15 Stage 9 teardown and the proxy module (sandbox_wrappers.py) was
deleted on 2026-08-16; nothing calls in over HTTP any more.

STATUS, measured 2026-08-16 and re-measured 2026-08-17: this surface answers
live (`GET /tools/_list` returns 10 names, down from 11 with `cloud_run_ralph`
deleted) and has ZERO callers anywhere in backend/, frontend/src, scripts/ or
mobile/. The implementations themselves are ordinary code that is still worth
keeping. Whether the HTTP surface survives is thread T5 of
docs/platform-redesign/coherence-overhaul-2026-08-16.md; it is left in place
rather than removed mid-overhaul because cloud.py's callable surface is
referenced by an open security ticket (SEC-A4) that should settle first.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


router = APIRouter(prefix="/tools", tags=["letta-tools"])

log = logging.getLogger("workshop.tools")


# Import the actual implementations — these are the real functions doing the work.
from services.letta_tools import workshop as t_workshop
from services.letta_tools import local as t_local
from services.letta_tools import cloud as t_cloud
from services.letta_tools import notify as t_notify
from services.letta_tools import health as t_health


# Single dispatch table — name → callable. Keep this in sync with letta_tools.TOOL_REGISTRY.
DISPATCH: dict[str, Any] = {
    "wkshp_read_todo":        t_workshop.wkshp_read_todo,
    "wkshp_propose_task":     t_workshop.wkshp_propose_task,
    "wkshp_write_log":        t_workshop.wkshp_write_log,
    "wkshp_mark_blocked":     t_workshop.wkshp_mark_blocked,
    "local_classify":         t_local.local_classify,
    "local_route_decision":   t_local.local_route_decision,
    "local_summarize_short":  t_local.local_summarize_short,
    "cloud_run_claude":       t_cloud.cloud_run_claude,
    # "cloud_run_ralph" is deleted (2026-08-17): a second way to drive a ticket
    # that skipped the claim, lease, receipt, acceptance and budget that
    # scripts/project_ralph.sh takes. See services/letta_tools/cloud.py.
    "notify_operator":        t_notify.notify_operator,
    "system_health":          t_health.system_health,
}


class ToolCall(BaseModel):
    kwargs: dict[str, Any] = {}


@router.post("/{tool_name}")
def call_tool(tool_name: str, body: ToolCall | None = None) -> dict:
    func = DISPATCH.get(tool_name)
    if func is None:
        raise HTTPException(status_code=404, detail=f"unknown tool: {tool_name}")
    kwargs = body.kwargs if body else {}
    try:
        result = func(**kwargs)
    except TypeError as e:
        # Bad argument shape — surface clearly so the agent can self-correct.
        return {"error": f"bad arguments: {e}"}
    except Exception as e:
        log.warning("tool %s raised: %s", tool_name, e)
        return {"error": f"{type(e).__name__}: {e}"}
    return {"result": result if isinstance(result, str) else str(result)}


@router.get("/_list")
def list_tools() -> dict:
    return {"tools": sorted(DISPATCH.keys())}
