"""Identity dependencies — the single, fail-closed authentication path.

Every protected request resolves to a `current_user` here, by exactly one of:
  1. a valid session cookie (browser / WebView), or
  2. a valid device-token bearer (native background producers), or
  3. a valid service-token bearer bound to a scope (machine callers).
…and otherwise 401. There is NO network/loopback/CGNAT trust anywhere in this
path: reaching the box grants nothing.

Resolving a user also pins the data-owner contextvar (services/ownership.py) so
the per-owner stores scope to this user automatically, and — on cookie auth —
enforces a CSRF header on mutating requests (a cross-site form cannot set it;
SameSite=Strict already blocks the cookie cross-site, this is the standard
double-check). Bearer callers carry no ambient credential and are not CSRF-able,
so the header is not required for them.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, Request

from services import auth_store, ownership

SESSION_COOKIE = "workshop_session"
CSRF_HEADER = "x-workshop-client"
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _bearer(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def _resolve(request: Request) -> tuple[Optional[dict[str, Any]], Optional[str], Optional[str]]:
    """Return (user, method, scope). method in {cookie, device, service}; scope is
    set only for service tokens. (None, None, None) when unauthenticated."""
    cookie = request.cookies.get(SESSION_COOKIE)
    user = auth_store.session_user(cookie)
    if user is not None:
        return user, "cookie", None
    tok = _bearer(request)
    if tok:
        user = auth_store.device_user(tok)
        if user is not None:
            return user, "device", None
        svc = auth_store.service_identity(tok)
        if svc is not None:
            return svc["user"], "service", svc["scope"]
    return None, None, None


def user_from_request(request: Request) -> Optional[dict[str, Any]]:
    """Plain resolver (no raise, no side effects) used by the legacy service gate
    during the transition. Returns the user or None."""
    user, _, _ = _resolve(request)
    return user


async def optional_user(request: Request) -> Optional[dict[str, Any]]:
    user, method, _ = _resolve(request)
    if user is not None:
        ownership.set_current_owner(user["id"])
        _csrf_guard(request, method)
    return user


def _csrf_guard(request: Request, method: Optional[str]) -> None:
    if method == "cookie" and request.method in _MUTATING:
        if request.headers.get(CSRF_HEADER) is None:
            raise HTTPException(status_code=403, detail="missing client header")


async def current_user(request: Request) -> dict[str, Any]:
    """The one authentication dependency. Fail-closed: 401 if no valid credential.
    Pins the data owner for the request and enforces CSRF on cookie mutations."""
    user, method, _ = _resolve(request)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    _csrf_guard(request, method)
    ownership.set_current_owner(user["id"])
    return user


# Back-compat alias: existing routes import require_user.
require_user = current_user


async def require_operator(request: Request) -> dict[str, Any]:
    """Admin gate for PLATFORM/infra routers only. is_operator never grants
    access to another user's PERSONAL data — that is owned, not role-gated."""
    user = await current_user(request)
    if not user.get("is_operator"):
        raise HTTPException(status_code=403, detail="operator only")
    return user


def require_domain_owner(domain: str):
    """Dependency factory for single-dataset domains (an app's personal data):
    require an authenticated user who is the recorded DATA owner of `domain`.
    Anyone else — including another is_operator account — gets 403. The owner is
    set by the bootstrap migration (auth_store.domain_owners), never by role or
    email. Pins the owner contextvar so the domain's per-owner reads resolve."""

    async def _dep(request: Request) -> dict[str, Any]:
        user = await current_user(request)
        owner = auth_store.domain_owner(domain)
        if owner is None or user["id"] != owner:
            raise HTTPException(status_code=403, detail=f"not the {domain} owner")
        ownership.set_current_owner(user["id"])
        return user

    return _dep


def require_user_or_service(scope: str):
    """Dependency factory: pass if there is a valid user (cookie/device) OR a
    service token of `scope`. Used for low-sensitivity routers that mix
    authenticated reads with a machine ingest path (e.g. a sensor app whose
    detections are already public, but no access is anonymous). Pins the owner either way."""

    async def _dep(request: Request) -> dict[str, Any]:
        user, method, _ = _resolve(request)
        if user is not None:
            _csrf_guard(request, method)
            ownership.set_current_owner(user["id"])
            return user
        tok = _bearer(request)
        svc = auth_store.service_identity(tok) if tok else None
        if svc is not None and svc["scope"] == scope:
            ownership.set_current_owner(svc["user"]["id"])
            return svc["user"]
        raise HTTPException(status_code=401, detail="authentication required")

    return _dep


def require_service(scope: str):
    """Dependency factory: require a service token whose scope matches `scope`
    (machine callers — cron, ingest webhooks, the Letta sandbox). Pins the
    token's bound owner. No cookie/device token satisfies this."""

    async def _dep(request: Request) -> dict[str, Any]:
        tok = _bearer(request)
        svc = auth_store.service_identity(tok) if tok else None
        if svc is None or svc["scope"] != scope:
            raise HTTPException(status_code=403, detail=f"service scope {scope!r} required")
        ownership.set_current_owner(svc["user"]["id"])
        return svc["user"]

    return _dep
