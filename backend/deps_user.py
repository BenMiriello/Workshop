"""User-identity dependencies for the product-auth layer.

This sits ABOVE the service-trust gate in `deps_auth` (loopback / tailnet /
service-token). That gate answers "may this machine talk to the API at all";
this one answers "WHO is the logged-in user", by verifying the Bearer access
JWT minted in `routes/auth`.

`require_user` / `optional_user` are FastAPI dependencies for product routes.
`user_from_request` is the plain helper `deps_auth` calls to let the operator's
own JWT satisfy the service gate when reaching the single-owner routers off-host.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, Request

from services import jwt_tokens


def _bearer(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def user_from_request(request: Request) -> Optional[dict[str, Any]]:
    """Resolve the caller's identity from a valid access token, or None."""
    token = _bearer(request)
    if not token:
        return None
    try:
        claims = jwt_tokens.decode(token, "access")
    except jwt_tokens.TokenError:
        return None
    return jwt_tokens.claims_to_identity(claims)


async def optional_user(request: Request) -> Optional[dict[str, Any]]:
    return user_from_request(request)


async def require_user(request: Request) -> dict[str, Any]:
    user = user_from_request(request)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


async def require_operator(request: Request) -> dict[str, Any]:
    user = await require_user(request)
    if not user.get("is_operator"):
        raise HTTPException(status_code=403, detail="operator only")
    return user
