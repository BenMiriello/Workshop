"""WebSockets on the app runner's proxy path, mounted at /base-apps/.

The HTTP half of `/base-apps/run/<slug>/proxy/<service>/...` is
`base_apps.run_proxy`. A socket opened on the same path lands here and is relayed
to the same service, found the same way (`base_apps._proxy_target`), at the same
full path, so an app's live channel (its own socket API, a dev server's reload
socket) works wherever its pages do.

Mounted WITHOUT the router-level `require_operator` that guards `base_apps`,
because that dependency is typed for an HTTP `Request` and a WebSocket is not
one. The same gate runs inside the handler instead (`deps_user.socket_operator`),
after the Origin check every socket route here makes.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, WebSocket

from deps_user import socket_operator, socket_origin_ok
from routes import base_apps
from services import app_runner, ws_relay

router = APIRouter()

# Close codes for a socket refused before it is accepted. A browser sees any of
# them as a failed handshake; they are distinct for logs and tests.
_NO_SERVICE = 4404
_NOT_RUNNING = 4409
_UNREACHABLE = 1011


def _upstream_path(ws: WebSocket) -> str:
    """The path the app is served at, byte for byte as it arrived when that is
    the proxy path itself, so a trailing slash or an escape reaches the app."""
    raw = (ws.scope.get("raw_path") or b"").decode("latin-1")
    return raw if raw.startswith("/base-apps/") else ws.scope["path"]


def _forward_headers(ws: WebSocket, user_id: int | None) -> list[tuple[str, str]]:
    kept = [(k, v) for k, v in ws.headers.items()
            if k.lower() not in ws_relay.HANDSHAKE
            and k.lower() not in base_apps._HOP_BY_HOP
            and k.lower() != base_apps.USER_HEADER.lower()]
    return kept + list(base_apps.user_header(user_id).items())


@router.websocket("/run/{slug}/proxy/{service}/{path:path}")
async def run_proxy_ws(ws: WebSocket, slug: str, service: str, path: str) -> None:
    if not socket_origin_ok(ws):
        await ws.close(code=4403)
        return
    user, refused = await socket_operator(ws)
    if refused:
        await ws.close(code=refused)
        return

    target = _upstream_path(ws)
    if ws.url.query:
        target += f"?{ws.url.query}"
    headers = _forward_headers(ws, user["id"] if user else None)

    # The upgrade is a GET with no body, so it waits out a restart the way
    # run_proxy holds one, on the same budget and the same reading of status.
    wanted = app_runner.wants_running(slug)
    start = time.monotonic()
    pause = 0.25
    while True:
        status, port = await base_apps._proxy_target(ws, slug, service)
        waited = time.monotonic() - start
        if status == "running" and port:
            try:
                upstream = await ws_relay.dial(ws, f"ws://127.0.0.1:{port}{target}", headers)
                break
            except (OSError, TimeoutError):
                base_apps._proxy_targets(ws).pop((slug, service), None)
                if waited >= base_apps._HOLD_S:
                    await ws.close(code=_UNREACHABLE)
                    return
            except Exception:                           # noqa: BLE001
                # The service answered and refused the upgrade.
                await ws.close(code=_UNREACHABLE)
                return
        elif not (waited < base_apps._HOLD_S
                  and base_apps._coming_back(status, waited, wanted)):
            await ws.close(code=_NOT_RUNNING if port else _NO_SERVICE)
            return
        await asyncio.sleep(pause)
        pause = min(pause * 2, 2.0)

    await ws_relay.pump(ws, upstream)
