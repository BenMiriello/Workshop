"""Relay one WebSocket to another, both ways, until either end closes.

httpx cannot carry an Upgrade, so every hop a browser's socket crosses here
terminates it and dials the next hop itself: the public edge dials the backend,
and the app runner's proxy dials an app's service. Both use this, so a socket
arrives at the far end the way the browser opened it: the offered subprotocols
are offered upstream and the chosen one is accepted back, text and binary frames
keep their type, and a close carries its code and reason across in either
direction.
"""
from __future__ import annotations

import asyncio
from typing import Any

import websockets
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

# uvicorn's own inbound frame cap (`--ws-max-size`), so a frame that may arrive
# from the browser may also arrive from upstream, and a wedged peer cannot grow
# this process without bound.
MAX_FRAME = 16 * 1024 * 1024

# The headers that negotiate THIS hop's handshake. The dialer writes its own.
HANDSHAKE = frozenset({
    "host", "connection", "upgrade", "content-length", "transfer-encoding",
    "sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions",
    "sec-websocket-protocol", "sec-websocket-accept",
})


def offered_subprotocols(ws: WebSocket) -> list[str]:
    raw = ws.headers.get("sec-websocket-protocol", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def wire_code(code: int | None) -> int:
    """A close code that may be sent on the wire. 1005 (none given), 1006 (no
    close frame) and 1015 (TLS) only describe what one end observed, and RFC
    6455 7.4.1 forbids sending them, so they are told as normal or as an error."""
    if code is None or code == 1005:
        return 1000
    if code in (1006, 1015):
        return 1011
    return code


async def dial(ws: WebSocket, url: str, headers: list[tuple[str, str]], *,
               open_timeout: float = 10) -> Any:
    """Open the upstream socket for `ws`, offering what the browser offered.
    Raises whatever the dial raises; the caller decides what a refusal means."""
    return await websockets.connect(
        url,
        additional_headers=headers,
        subprotocols=offered_subprotocols(ws) or None,
        open_timeout=open_timeout,
        ping_interval=20,
        max_size=MAX_FRAME,
    )


async def _close(ws: WebSocket, code: int, reason: str = "") -> None:
    try:
        await ws.close(code=code, reason=reason)
    except (RuntimeError, OSError, WebSocketDisconnect):
        pass                                            # already closed


async def pump(ws: WebSocket, upstream: Any) -> None:
    """Accept `ws` with the subprotocol upstream chose, then carry frames both
    ways. Whichever end closes first, the other is closed with its code."""
    await ws.accept(subprotocol=upstream.subprotocol)

    async def to_upstream() -> None:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                await upstream.close(wire_code(msg.get("code")), msg.get("reason") or "")
                return
            if (data := msg.get("bytes")) is not None:
                await upstream.send(data)
            elif (text := msg.get("text")) is not None:
                await upstream.send(text)

    async def to_client() -> None:
        async for msg in upstream:
            if isinstance(msg, bytes):
                await ws.send_bytes(msg)
            else:
                await ws.send_text(msg)

    tasks = {asyncio.create_task(to_upstream()), asyncio.create_task(to_client())}
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for result in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(result, BaseException) and not isinstance(
                    result, (ConnectionClosed, WebSocketDisconnect, OSError,
                             RuntimeError, asyncio.CancelledError)):
                raise result
        await upstream.close()
        await _close(ws, wire_code(upstream.close_code), upstream.close_reason or "")
