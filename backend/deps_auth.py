"""Loopback guard for INTERNAL INFRASTRUCTURE routers only.

After the auth redesign (docs/auth-redesign/), all PERSONAL DATA and PLATFORM
control is gated by IDENTITY (deps_user: require_user / require_domain_owner /
require_operator / require_service) with NO network trust. The old `require_token`
gate — which trusted loopback OR the Tailscale CGNAT range OR an operator JWT, and
FAILED OPEN when no token was configured — is removed.

What remains here is a deliberately narrow guard for genuinely internal
machine-to-machine infrastructure endpoints that carry NO personal data and are
NOT exposed through the public proxy (relay, services, events): they accept
calls only from the loopback interface. There is no CGNAT trust, no fail-open, and
no path by which this guard grants access to any personal-data router.
"""
from __future__ import annotations

import ipaddress

from fastapi import HTTPException, Request

# The genuinely-internal networks a machine caller may reach these infra routers
# from: loopback (backend self-calls) + the Docker bridge (the Letta tool sandbox,
# which runs in a container on 172.17.x and calls /tools off-loopback). These are
# NEVER the public internet.
#
# We deliberately do NOT trust the whole of RFC1918. The backend binds 0.0.0.0:8000
# (reachable from the physical/home LAN, e.g. 192.168.1.x, and via the tailnet), so
# trusting 10.0.0.0/8 + 192.168.0.0/16 would let any LAN device hit /tools —
# including cloud_run_claude, which runs an agentic `claude -p` with Bash access =
# host code execution. Narrowed to loopback + Docker bridge on 2026-07-18
# (security-audit-2026-07-18 H2). If a new machine caller genuinely needs off-bridge
# access, give it a scoped service token (require_service) rather than widening this.
_PRIVATE = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
]


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_internal(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _PRIVATE)


async def require_loopback(request: Request) -> None:
    """Allow only loopback callers. For internal infra routers (relay, services,
    events) that hold no personal data and are never reachable from the public
    origin: backend self-calls reach these from 127.0.0.1. The auto_pr router
    this also named was deleted on 2026-08-17 (T5, zero callers)."""
    peer = request.client.host if request.client else ""
    if _is_loopback(peer):
        return
    raise HTTPException(status_code=403, detail="internal endpoint")


async def require_internal_caller(request: Request) -> None:
    """Allow loopback OR the host's private/Docker-bridge networks. For the Letta
    tool sandbox, which calls /tools from the Docker bridge (off-loopback) and
    holds no personal data. Never reachable from the public origin."""
    peer = request.client.host if request.client else ""
    if _is_internal(peer):
        return
    raise HTTPException(status_code=403, detail="internal endpoint")
