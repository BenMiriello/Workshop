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


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def require_loopback(request: Request) -> None:
    """Allow only loopback callers. For internal infra routers that hold no
    personal data and are never reachable from the public origin."""
    peer = request.client.host if request.client else ""
    if _is_loopback(peer):
        return
    raise HTTPException(status_code=403, detail="internal endpoint")
