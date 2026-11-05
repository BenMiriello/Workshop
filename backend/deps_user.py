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

import asyncio
from typing import Any, Optional

from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection

from services import auth_store, installation, ownership

SESSION_COOKIE = "workshop_session"
CSRF_HEADER = "x-workshop-client"
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _bearer(request: HTTPConnection) -> Optional[str]:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def _resolve(request: HTTPConnection) -> tuple[Optional[dict[str, Any]], Optional[str], Optional[str]]:
    """Return (user, method, scope). method in {cookie, device, service}; scope is
    set only for service tokens. (None, None, None) when unauthenticated.
    A resolved caller is also recorded for `request_auth`."""
    cookie = request.cookies.get(SESSION_COOKIE)
    user = auth_store.session_user(cookie)
    if user is not None:
        return _record(request, user, "cookie"), "cookie", None
    tok = _bearer(request)
    if tok:
        user = auth_store.device_user(tok)
        if user is not None:
            return _record(request, user, "device"), "device", None
        svc = auth_store.service_identity(tok)
        if svc is not None:
            _record(request, svc["user"], "service", svc)
            return svc["user"], "service", svc["scope"]
    return None, None, None


def _record(request: HTTPConnection, user: dict[str, Any], method: str,
            svc: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    request.state.auth = {
        "method": method,
        "scope": svc["scope"] if svc else None,
        "name": svc["name"] if svc else None,
        "user_id": user["id"],
    }
    return user


def request_auth(request: Request) -> dict[str, Any]:
    """How the current request authenticated, after one of the dependencies here
    ran: {"method", "scope", "name", "user_id"}. `method` is cookie, device or
    service; `scope` and `name` (the token's label) are set only for a service
    token, so a route can confine what a machine caller may change. All None
    when nothing resolved."""
    return getattr(request.state, "auth", None) or {
        "method": None, "scope": None, "name": None, "user_id": None,
    }


def user_from_request(request: Request) -> Optional[dict[str, Any]]:
    """Plain resolver (no raise, no side effects) used by the legacy service gate
    during the transition. Returns the user or None."""
    user, _, _ = _resolve(request)
    return user


async def optional_user(request: Request) -> Optional[dict[str, Any]]:
    user, method, _ = await asyncio.to_thread(_resolve, request)
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
    Pins the data owner for the request and enforces CSRF on cookie mutations.

    The lookup is SQLite on disk, so it runs on a worker thread: this runs for
    every authenticated request, and on the event loop a slow disk held all of
    them. The owner is pinned back here, in the request's own context."""
    user, method, _ = await asyncio.to_thread(_resolve, request)
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
        owner = await asyncio.to_thread(auth_store.domain_owner, domain)
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
        user, method, _ = await asyncio.to_thread(_resolve, request)
        if user is not None:
            _csrf_guard(request, method)
            ownership.set_current_owner(user["id"])
            return user
        tok = _bearer(request)
        svc = await asyncio.to_thread(auth_store.service_identity, tok) if tok else None
        if svc is not None and svc["scope"] == scope:
            _record(request, svc["user"], "service", svc)
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
        svc = await asyncio.to_thread(auth_store.service_identity, tok) if tok else None
        if svc is None or svc["scope"] != scope:
            raise HTTPException(status_code=403, detail=f"service scope {scope!r} required")
        _record(request, svc["user"], "service", svc)
        ownership.set_current_owner(svc["user"]["id"])
        return svc["user"]

    return _dep


# ---- WebSockets -------------------------------------------------------------
# The dependencies above are typed for an HTTP `Request`, and FastAPI does not
# hand a WebSocket to a parameter of that type, so a socket route authenticates
# inside its handler with these instead. Same resolution, same fail-closed rule.
#
# WebSockets are not covered by the same-origin policy, so a cross-site page can
# open one and it will carry cookies. The Origin check is what closes that: it
# enforces on a socket the boundary the CSRF header enforces on HTTP routes.

_DEV_ORIGINS = {
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:5174", "http://127.0.0.1:5174",
}


def socket_origin_ok(ws: HTTPConnection) -> bool:
    """Is this socket coming from a page we serve?

    THE HOST TO COMPARE AGAINST IS THE ONE THE BROWSER TYPED, NOT OURS.
    Nearly every real connection arrives through the public edge, which
    terminates the browser's socket and dials this process on loopback.
    Comparing the browser's Origin against THIS process's Host therefore
    compares the public hostname against `127.0.0.1:<port>` and rejects every
    genuine connection with a 403, which is exactly what happened to voice:
    a spinner that never resolved while the log filled with 403s.

    The edge forwards the original host as `x-forwarded-host` and does its own
    same-origin check before dialing (it is the boundary the browser actually
    talks to). This is the standard reverse-proxy arrangement, and it adds no
    trust: an attacker who could set `x-forwarded-host` is already inside the
    loopback interface, where they could talk to this port directly anyway.
    """
    origin = ws.headers.get("origin")
    if not origin:
        return True                      # a native client sends none
    if origin in _DEV_ORIGINS:
        return True
    if origin.rstrip("/") in installation.declared_origins():
        return True
    host = ws.headers.get("x-forwarded-host") or ws.headers.get("host", "")
    return bool(host) and origin.split("//", 1)[-1] == host


async def socket_operator(ws: HTTPConnection) -> tuple[Optional[dict[str, Any]], int]:
    """`require_operator` for a socket: (user, 0) with the owner pinned, or
    (None, close code) -- 4401 for no valid credential, 4403 for not an
    operator."""
    user, _, _ = await asyncio.to_thread(_resolve, ws)
    if user is None:
        return None, 4401
    if not user.get("is_operator"):
        return None, 4403
    ownership.set_current_owner(user["id"])
    return user, 0


def socket_user(ws: HTTPConnection) -> Optional[dict]:
    """The user behind a socket: its session cookie, else a device bearer
    token. A socket cannot take the HTTP dependencies, so its handler asks."""
    user = auth_store.session_user(ws.cookies.get(SESSION_COOKIE))
    if user is not None:
        return user
    auth = ws.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth_store.device_user(auth[7:].strip())
    return None
