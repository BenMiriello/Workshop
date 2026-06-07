"""HTTP dispatch for Letta tool functions.

The Letta sandbox runs tool functions inside its own Docker container, which
does NOT have access to:
  - Host filesystem (/home/clawd-a/workshop/...)
  - Host localhost services (127.0.0.1:11434 Ollama, etc.)

The sandbox CAN reach the host backend at host.docker.internal:8000. So the
pattern is: keep tool *implementations* on the host backend (this module),
and make the Letta-registered tool *functions* thin HTTP proxies that POST
here.

This module exposes one endpoint per tool: POST /tools/{tool_name} with a
JSON body of kwargs. Returns {"result": <string>} or {"error": <string>}.

All implementations live in services/letta_tools/. The Letta-registered tool
functions (also in services/letta_tools/) are HTTP wrappers that hit these
endpoints — see services/letta_tools/sandbox_wrappers.py.
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
    "cloud_run_ralph":        t_cloud.cloud_run_ralph,
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
