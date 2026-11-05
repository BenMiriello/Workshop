"""`/local-llm`: the configured local model server, behind the thinking setting.

A turn on a local model sends its requests here instead of straight to the
server (`LocalHttpProvider.cli_env`), so each Messages request can have its
thinking set from the chat's effort (`services/local_thinking`), and then its
shape (`services/local_request_shape`): tool results in their own turns, and a
compaction's room for its summary, on the compaction model when one is set
for the chat's app (`X-Workshop-App`) or for every chat
(`local_model_provider.compaction_model_for`) and its window fits. Everything
else is passed through as it is,
streamed responses included, so the CLI cannot tell this from the server,
except that a refusal of a prompt too long for the model's window is put in
the Anthropic API's words, which the CLI compacts on
(`services/context_overflow`). Loopback only: the CLI is a child of this
backend.

A stream the server cuts off (a reload of its config unloads the model under
the answer) is ended here, not raised through: the response closes where the
bytes stopped, so the CLI reads a stream without its `message_stop`, closes the
response as cut short and retries it streaming, which a turn counts as a failed
request (`model_failures`). No error event is added: the CLI answers one by
sending the request again without streaming, so a long local answer would come
back with no thinking shown and nothing moving until it was whole.
"""
from __future__ import annotations

import logging

import anyio
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from services import (context_overflow, local_model_provider,
                      local_request_shape, local_thinking)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/local-llm", tags=["local-llm"],
                   include_in_schema=False)

# Hop-by-hop, or recomputed on the way out.
_DROP = {"host", "content-length", "connection", "accept-encoding",
         "transfer-encoding"}
# A model server on this host that has not taken a connection in this long is
# not coming. Reads have no limit: a reply runs as long as generation takes.
CONNECT_S = 5.0
APP_HEADER = "x-workshop-app"
_END = b"message_stop"


def _window(model: str) -> int:
    """`model`'s window as the server last reported it, 0 when it did not."""
    return next((m.capabilities.context_tokens
                 for m in local_model_provider.probe().models if m.id == model), 0)


def _compaction_model(headers) -> str | None:
    app = (headers.get(APP_HEADER) or "").strip()
    scope = f"app:{app}" if app else None
    return local_model_provider.compaction_model_for(scope)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"type": "error", "error": {"type": "api_error", "message": message}},
        status_code=status)


@router.api_route("/{path:path}", methods=["GET", "POST"])
async def relay(path: str, request: Request):
    origin = local_model_provider.base_url()
    if origin is None:
        return _error(503, "No local model server is configured.")
    body = await request.body()
    messages = path.split("?")[0] == "v1/messages"
    if request.method == "POST" and messages:
        memory = request.app.state.__dict__.setdefault(
            "local_efforts", local_thinking.new_memory())
        body = local_thinking.rewrite(body, request.headers, memory)
        klass = request.headers.get(local_thinking.CLASS_HEADER)
        compaction_to = (_compaction_model(request.headers)
                         if klass == "compaction" else None)
        body = await anyio.to_thread.run_sync(
            local_request_shape.shape, body, klass, _window, compaction_to)
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _DROP}
    http = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=CONNECT_S, read=None))
    try:
        up = await http.send(
            http.build_request(request.method, f"{origin}/{path}",
                               params=request.query_params, headers=headers,
                               content=body),
            stream=True)
    except httpx.TransportError as exc:
        await http.aclose()
        return _error(503, f"Could not reach {origin}: {exc.__class__.__name__}.")

    if request.method == "POST" and messages and up.status_code == 400:
        try:
            raw = await up.aread()
        finally:
            await up.aclose()
            await http.aclose()
        said = context_overflow.anthropic_error(up.status_code, raw)
        if said is not None:
            return JSONResponse(said, status_code=400)
        return Response(raw, status_code=400,
                        media_type=up.headers.get("content-type"))

    sse = "text/event-stream" in up.headers.get("content-type", "")

    async def stream():
        sent, whole, tail = 0, False, b""
        try:
            async for chunk in up.aiter_raw():
                sent += len(chunk)
                whole = whole or _END in tail + chunk
                tail = chunk[-len(_END):]
                yield chunk
        except httpx.TransportError as exc:
            log.warning("local-llm: %s cut its stream for %s after %d bytes (%s)",
                        origin, path, sent, exc.__class__.__name__)
            return
        finally:
            with anyio.CancelScope(shield=True):
                await up.aclose()
                await http.aclose()
        if sse and messages and not whole:
            log.warning("local-llm: %s ended its stream for %s after %d bytes "
                        "without message_stop", origin, path, sent)

    kept = {k: v for k, v in up.headers.items()
            if k.lower() not in _DROP and k.lower() != "content-encoding"}
    return StreamingResponse(stream(), status_code=up.status_code, headers=kept)
