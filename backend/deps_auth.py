"""Loopback guard for INTERNAL INFRASTRUCTURE routers only.

After the auth redesign (docs/auth-redesign/), all PERSONAL DATA and PLATFORM
control is gated by IDENTITY (deps_user: require_user / require_domain_owner /
require_operator / require_service) with NO network trust. The old `require_token`
gate — which trusted loopback OR the Tailscale CGNAT range OR an operator JWT, and
FAILED OPEN when no token was configured — is removed.

What remains here is a deliberately narrow guard for genuinely internal
machine-to-machine infrastructure endpoints that carry NO personal data and are
NOT exposed through the public proxy (relay/services/events/auto_pr): they accept
calls only from the loopback interface. There is no CGNAT trust, no fail-open, and
no path by which this guard grants access to any personal-data router.
"""
from __future__ import annotations

import ipaddress

from fastapi import HTTPException, Request

# The host's own internal networks (loopback + private RFC1918 + the Docker
# bridge). These are NEVER the public internet: the only public path into this
# box is the Cloudflare tunnel, which terminates on loopback and whose public
# app proxies ONLY the personal+auth surface — never these internal routers.
_PRIVATE = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
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
    """Allow only loopback callers. For internal infra routers (relay/services/
    events/auto_pr) that hold no personal data and are never reachable from the
    public origin — backend self-calls reach these from 127.0.0.1."""
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
