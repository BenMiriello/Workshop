"""The workshop tool server over HTTP, for every turn in every scope.

`mcp/workshop_query_server.py` holds the tools. Over stdio it was a child of
the turn's `claude` process, launched from the backend's own interpreter and
source, which a turn confined to an app's folder cannot see, so app chats had
no workshop tools at all. Served from here it is reached over loopback from
inside any sandbox.

Stateless by design: no session id, and every request carries the signed
claims of the turn it belongs to (`services/turn_claims.py`). A turn keeps its
tools across a backend restart because nothing about it is held here between
calls (measured against the CLI 2026-11-04: a server restarted between two
calls of one turn answered the second with no new handshake).

The tools are synchronous and some are slow (search, command tools, calls back
into the page beside a chat), so each message runs on a small pool of its own,
never on the event loop and never in the pool every sync route shares.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from services import turn_claims

router = APIRouter(tags=["mcp-workshop"])

_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mcp-workshop")


def _claims_with_root(claims: dict[str, str]) -> dict[str, str]:
    """The claims plus the host directory of the claimed scope, resolved here at
    call time rather than trusted from the token."""
    out = dict(claims)
    scope = claims.get("WORKSHOP_QUERY_SCOPE") or ""
    if scope:
        try:
            from services import task_scope
            root = task_scope.root_for_context(scope)
        except Exception:
            root = None
        if root:
            out["WORKSHOP_QUERY_ROOT"] = str(root)
    return out


def _handle_all(claims: dict[str, str], messages: list) -> list[dict]:
    from mcp import turn_context, workshop_query_server as server

    out: list[dict] = []
    with turn_context.bound(claims):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            try:
                resp = server._handle(msg)
            except Exception as e:  # noqa: BLE001 - one bad call answers as an error
                resp = ({"jsonrpc": "2.0", "id": msg.get("id"),
                         "error": {"code": -32603, "message": str(e)}}
                        if msg.get("id") is not None else None)
            if resp is not None:
                out.append(resp)
    return out


@router.post("/mcp/workshop")
async def call(request: Request) -> Response:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    claims = turn_claims.verify(token)
    if claims is None:
        return JSONResponse({"detail": "this call carries no valid turn claims"}, status_code=401)
    try:
        body = json.loads(await request.body() or b"null")
    except json.JSONDecodeError:
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "parse error"}}, status_code=400)
    batch = isinstance(body, list)
    messages = body if batch else [body]
    ctx = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    replies = await loop.run_in_executor(
        _POOL, ctx.run, _handle_all, _claims_with_root(claims), messages)
    if not replies:
        return Response(status_code=202)
    return JSONResponse(replies if batch else replies[0])


@router.get("/mcp/workshop")
async def no_stream() -> Response:
    return Response(status_code=405)
