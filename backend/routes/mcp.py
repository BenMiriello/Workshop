"""Settings -> MCP: every server this machine has, and what it is costing.

The same two readings the context modal joins, asked without a chat in hand:

  `claude mcp list`  -> which servers exist, and whether they connect
  `claude -p /context` -> which tools loaded, and what they cost in tokens

Neither is complete on its own and the union is the only honest list, for the
reason written up in `services/mcp_health` and `frontend/.../mcpHealth.ts`: a
server that fails to connect loads no tools, so it costs nothing, so it is
invisible in the cost reading exactly when it most needs looking at.

WHY THE COST READING HAS NO SESSION. `/context` takes `--resume <session>` to
report one thread's usage; here there is no thread. Run without it, the CLI
reports a FRESH session in this directory — which is precisely the right number
for a settings screen: what MCP costs every new turn, before any conversation
has been had. Both readings are the same side queries the chat panel already
makes: no model call, no transcript, nothing claimed.

WHAT IS NOT SENT TO THE BROWSER. `mcp_registry` resolves real launch specs, and
a spec can hold credentials — `~/.claude.json` on this host has a
`GOOGLE_CLIENT_SECRET` in one. Only `spec_available`, `scope` and `source` cross
the wire. The spec itself never leaves the backend; it exists to be handed to
the CLI, not to be displayed.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from services import context_report, mcp_health, mcp_registry

log = logging.getLogger("workshop.routes.mcp")

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _cost_index(report: dict | None) -> dict[str, dict]:
    """`/context`'s MCP section, keyed for joining to the health rows.

    Keyed on the normalised name for the same reason the frontend normalises:
    the two CLI surfaces spell one server two ways (`claude_ai_Gmail` versus
    `claude.ai Gmail`), and a join that misses renders a connected server as
    costing nothing.
    """
    out: dict[str, dict] = {}
    for entry in (report or {}).get("mcp") or []:
        key = _norm(str(entry.get("server") or ""))
        if key:
            out[key] = entry
    return out


def _norm(name: str) -> str:
    """Lowercase, and every run of non-alphanumerics collapsed to one
    separator. Mirrors `normalizeServerName` in the frontend module."""
    out: list[str] = []
    for ch in name.lower():
        out.append(ch if ch.isalnum() else "_")
    return "_".join(p for p in "".join(out).split("_") if p)




class McpServerRow(BaseModel):
    """One server, joined from the two readings. WHAT IS NOT HERE MATTERS:
    `mcp_registry` resolves real launch specs and a spec can hold credentials
    (`~/.claude.json` on this host has a GOOGLE_CLIENT_SECRET in one). Only
    `spec_available`, `scope` and `source` cross the wire; the spec itself never
    leaves the backend.

    `tokens: null` and `tokens: 0` are DIFFERENT CLAIMS and the difference is
    the whole point of the column. Null means the cost probe never saw this row
    and the UI prints an em-dash; zero means it saw it contributing nothing."""

    model_config = ConfigDict(extra="allow")

    name: str
    spec_available: bool | None = None
    scope: str | None = None
    source: str | None = None
    shadowed: bool | None = None
    tokens: int | None = None
    tools: list[dict[str, Any]] | None = None


class McpServers(BaseModel):
    """The union of `claude mcp list` and a sessionless `/context` reading.
    Neither is complete alone: a server that fails to connect loads no tools, so
    it costs nothing, so it is invisible in the cost reading exactly when it
    most needs looking at.

    `cost_available: false` means the cost half could not run; the health half
    still stands, and the two carry separate `checked_at` stamps because they
    are separately cached and can be of different ages."""

    available: bool
    reason: str | None = None
    servers: list[McpServerRow]
    counts: dict[str, Any] | None = None
    checked_at: float | None = None
    cached: bool | None = None
    cost_available: bool
    cost_checked_at: float | None = None
    cwd: str


@router.get("/servers", response_model=McpServers)
async def list_servers(refresh: bool = False):
    """Every configured MCP server: where it comes from, whether it connects,
    what it exposes and what it costs.

    Both probes run together and neither may sink the other: a wedged
    `/context` must still leave the health list renderable, and vice versa. The
    cost half therefore reports its own `cost_available` rather than failing the
    request — a screen that says "we could not price these" is useful; a 500 is
    not.
    """
    cwd = str(context_report.WORKSHOP_ROOT)

    health_task = asyncio.create_task(mcp_health.cached_mcp_health(cwd, force=bool(refresh)))
    cost_task = asyncio.create_task(_cost(cwd, force=bool(refresh)))
    (health, checked_at, cached), (report, cost_at) = await asyncio.gather(health_task, cost_task)

    if not health.get("available"):
        return {**health, "servers": [], "checked_at": checked_at, "cached": cached,
                "cost_available": report is not None, "cost_checked_at": cost_at,
                "cwd": cwd}

    specs = mcp_registry.resolve_specs(cwd)
    rows = mcp_registry.annotate(health.get("servers") or [], specs)
    costs = _cost_index(report)
    for row in rows:
        entry = costs.get(_norm(row.get("name", "")))
        # `tokens: 0` and "we have no cost reading" are different claims, and
        # the difference is the whole point of the column. A row the cost probe
        # never saw says so with a null, and the UI prints an em-dash; a row it
        # saw contributing nothing prints a real zero.
        row["tokens"] = entry.get("tokens") if entry else None
        row["tools"] = entry.get("tools") or [] if entry else []
    return {
        "available": True,
        "servers": rows,
        "counts": health.get("counts") or {},
        "checked_at": checked_at,
        "cached": cached,
        "cost_available": report is not None,
        "cost_checked_at": cost_at,
        "cwd": cwd,
    }


async def _cost(cwd: str, *, force: bool) -> tuple[dict | None, float]:
    """The sessionless `/context` reading, or (None, when) if it could not run.

    Cached under the empty session id, which is a real cache key here rather
    than a placeholder: "a fresh session in this directory" is one specific
    reading and every caller of this route wants the same one.
    """
    try:
        report, at, _cached = await context_report.cached_context_report("", cwd, force=force)
        return report, at
    except Exception:  # a side query must never take the screen down with it
        log.warning("mcp servers: the cost reading failed", exc_info=True)
        import time
        return None, time.time()
