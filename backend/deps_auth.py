"""Service-auth gate for the sensitive Workshop backend routers.

Trust model (single-user box, no per-user logins):
  * Callers on the LOOPBACK interface are trusted. That covers every legitimate
    caller of this API today — the backend's own internal HTTP self-calls, the
    cron agent, the masini bridge, and the operator's browser (which only ever
    reaches the API through the Vite proxy or Tailscale Serve, both of which
    connect to 127.0.0.1).
  * Anything OFF-host (e.g. a Docker container reaching the host over the bridge)
    must present the shared service token in `Authorization: Bearer <token>`.

This blocks a compromised container from reading /finance or driving the agent,
without adding a login the single operator would have to carry. The load-bearing
control for the LAN is binding the Vite dev server to localhost; this gate is
defence-in-depth at the API itself.

If WORKSHOP_API_TOKEN is unset, the gate fails OPEN (logs a warning) rather than
bricking the agent on a misconfigured deploy. The nightly audit asserts the token
is configured, so an accidental fail-open is caught.
"""
from __future__ import annotations

import ipaddress
import logging
import os

from fastapi import HTTPException, Request

log = logging.getLogger("workshop.auth")

_TOKEN_FILE = "/home/clawd-a/.config/clawdbot/workshop-api.env"


def _load_token() -> str:
    # Prefer the env var; fall back to the 0600 token file so the gate stays
    # closed no matter how the backend was started (systemd OR ad-hoc nohup).
    tok = os.environ.get("WORKSHOP_API_TOKEN", "").strip()
    if tok:
        return tok
    try:
        with open(_TOKEN_FILE) as fh:
            for line in fh:
                if line.startswith("WORKSHOP_API_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


_TOKEN = _load_token()
if not _TOKEN:
    log.warning("WORKSHOP_API_TOKEN unset and token file unreadable — sensitive routers fail OPEN for off-host callers")


# Tailscale CGNAT range — every device on the operator's tailnet (his Mac,
# phone, this host) gets a 100.64.0.0/10 address. Those are the operator's own
# machines reaching the API over WireGuard, so they're trusted like loopback.
# Reaching the API this way is only possible through Tailscale Serve, which
# connects to the backend from 127.0.0.1 and sets X-Forwarded-For to the
# client's tailnet IP; uvicorn (proxy-headers, forwarded-allow-ips=127.0.0.1)
# then rewrites request.client.host to that 100.x address. A compromised
# off-host container can't spoof this: its TCP peer is 172.x/192.168.x, not
# 127.0.0.1, so uvicorn ignores any X-Forwarded-For it sends and it still
# needs the token.
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _is_trusted_peer(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip in _TAILSCALE_CGNAT


async def require_token(request: Request) -> None:
    peer = request.client.host if request.client else ""
    if _is_trusted_peer(peer):
        return
    # The operator's own logged-in session (product-auth JWT) is a trusted caller
    # for the single-owner routers — this is how the operator reaches finance /
    # location etc. off-tailnet from the mobile app. A non-operator user JWT does
    # NOT satisfy this gate; their data is partitioned per-user elsewhere.
    if _operator_jwt_present(request):
        return
    if not _TOKEN:
        return
    if request.headers.get("authorization", "") == f"Bearer {_TOKEN}":
        return
    raise HTTPException(status_code=401, detail="service token required")


def _operator_jwt_present(request: Request) -> bool:
    # Imported lazily to avoid an import cycle (deps_user -> services.jwt_tokens).
    from deps_user import user_from_request

    user = user_from_request(request)
    return bool(user and user.get("is_operator"))
