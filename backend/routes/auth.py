"""Auth endpoints: register / login / logout / me / password + device tokens.

Authentication is an opaque, server-side session carried in an HttpOnly, Secure,
SameSite=Strict cookie (browser / WebView). Native background producers mint a
revocable device token instead. There is no JWT and no localStorage token: the
cookie is invisible to JS (XSS-safe) and fully revocable server-side.
"""
from __future__ import annotations

import os
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from deps_user import SESSION_COOKIE, current_user
from services import auth_store

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(value: str) -> str:
    value = value.strip()
    if not _EMAIL_RE.match(value):
        raise ValueError("a valid email is required")
    return value


def _registration_open() -> bool:
    # Fail-CLOSED: registration is open ONLY when explicitly enabled. A deploy that
    # forgets the env var must not expose internet-facing signup (this app is public
    # at the public origin). Changed from default-open 2026-07-18
    # (security-audit-2026-07-18 M4).
    return os.environ.get("WORKSHOP_REGISTRATION_OPEN", "0").strip().lower() in {
        "1", "true", "yes",
    }


def _cookie_secure() -> bool:
    # Secure by default (production is https end-to-end via Cloudflare). Set
    # WORKSHOP_COOKIE_SECURE=0 ONLY for the local http dev proxy, where a Secure
    # cookie would be dropped by the browser.
    return os.environ.get("WORKSHOP_COOKIE_SECURE", "1").strip().lower() not in {
        "0", "false", "no",
    }


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=auth_store.SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


class RegisterBody(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=256)
    name: str | None = Field(default=None, max_length=120)

    _check_email = field_validator("email")(_valid_email)


class LoginBody(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=256)

    _check_email = field_validator("email")(_valid_email)


class LogoutBody(BaseModel):
    all_devices: bool = False


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class DeviceTokenBody(BaseModel):
    label: str | None = Field(default=None, max_length=120)


# ---- response models (ADR 0023 clause 6) -----------------------------------
# Read off services/auth_store.py (`public_user` and `list_devices`). FastAPI
# FILTERS a return value through the declared model and drops any key it does
# not name, which on THIS router is a useful direction to fail in: `public_user`
# already exists to strip anything sensitive, and a declared model is a second
# statement of the same rule that the schema can be read against.
#
# No response here ever carries a password hash, a session token or a device
# token hash. The one raw secret that does cross the wire is the device token,
# returned ONCE at mint time, and it has its own model saying so.


class PublicUser(BaseModel):
    """A user as the wire sees them. `auth_store.public_user` builds exactly
    this and exists to strip everything else."""

    id: int
    email: str
    name: str | None = None
    is_operator: bool


class Session(BaseModel):
    """Register, login and change-password all answer with the signed-in user.
    The session itself is NOT in the body: it is an opaque server-side session
    in an HttpOnly, Secure, SameSite=Strict cookie, invisible to JS and fully
    revocable server-side. There is no JWT and no localStorage token."""

    user: PublicUser


class LogoutResult(BaseModel):
    """`scope` is "session" for this device or "all" when every session for the
    account was revoked."""

    ok: bool
    scope: str


class DeviceTokenIssued(BaseModel):
    """The RAW TOKEN, returned exactly once. It is not recoverable afterwards --
    only its hash is stored -- so the caller stores it in the iOS Keychain at
    this moment or not at all."""

    device_token: str
    label: str


class DeviceRow(BaseModel):
    """A minted device token, identified by a 12-character prefix of its hash.
    The token itself is never returned again."""

    id: str
    label: str | None = None
    created_at: str | None = None
    last_seen: str | None = None


class DeviceList(BaseModel):
    """Revoked tokens are excluded, so `total` is the live count rather than
    everything ever minted."""

    devices: list[DeviceRow]
    total: int


class DeviceRevoked(BaseModel):
    """`removed` is how many rows the prefix matched, so revoking an
    already-revoked device answers 0 rather than erroring."""

    ok: bool
    removed: int


def _login_response(response: Response, user: dict, request: Request) -> dict:
    raw = auth_store.create_session(user["id"], user_agent=request.headers.get("user-agent"))
    _set_session_cookie(response, raw)
    return {"user": auth_store.public_user(user)}


@router.post("/register", response_model=Session)
async def register(body: RegisterBody, request: Request, response: Response) -> dict:
    if not _registration_open():
        raise HTTPException(status_code=403, detail="registration is closed")
    try:
        user = auth_store.create_user(str(body.email), body.password, body.name)
    except auth_store.EmailTaken:
        raise HTTPException(status_code=409, detail="that email already has an account")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _login_response(response, user, request)


@router.post("/login", response_model=Session)
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    user = auth_store.verify_credentials(str(body.email), body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    return _login_response(response, user, request)


@router.post("/logout", response_model=LogoutResult)
async def logout(body: LogoutBody, request: Request, response: Response,
                 user: dict = Depends(current_user)) -> dict:
    if body.all_devices:
        auth_store.revoke_all_for_user(user["id"])
        _clear_session_cookie(response)
        return {"ok": True, "scope": "all"}
    auth_store.revoke_session(request.cookies.get(SESSION_COOKIE))
    _clear_session_cookie(response)
    return {"ok": True, "scope": "session"}


@router.post("/password", response_model=Session)
async def change_password(body: ChangePasswordBody, request: Request, response: Response,
                          user: dict = Depends(current_user)) -> dict:
    full = auth_store.get_by_id(user["id"])
    if full is None:
        raise HTTPException(status_code=401, detail="account no longer exists")
    if auth_store.verify_credentials(full["email"], body.current_password) is None:
        raise HTTPException(status_code=401, detail="current password is incorrect")
    try:
        auth_store.update_password(user["id"], body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    # Revoke every existing session + device token, then mint a fresh session so
    # the client that just changed the password stays signed in here.
    auth_store.revoke_all_for_user(user["id"])
    return _login_response(response, full, request)


@router.get("/me", response_model=PublicUser)
async def me(user: dict = Depends(current_user)) -> dict:
    full = auth_store.get_by_id(user["id"])
    if full is None:
        raise HTTPException(status_code=401, detail="account no longer exists")
    return auth_store.public_user(full)


# ---- device tokens (native background producers) ----


@router.post("/device-token", response_model=DeviceTokenIssued)
async def issue_device_token(body: DeviceTokenBody, user: dict = Depends(current_user)) -> dict:
    """Mint a revocable device token for this account's native background tasks
    (location/audio), which run outside the WebView and cannot use the cookie.
    The raw token is returned ONCE; store it in the iOS Keychain."""
    raw = auth_store.create_device_token(user["id"], body.label)
    return {"device_token": raw, "label": body.label or "device"}


@router.get("/devices", response_model=DeviceList)
async def list_devices(
    limit: int = 10, offset: int = 0, user: dict = Depends(current_user)
) -> dict:
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    return auth_store.list_devices(user["id"], limit=limit, offset=offset)


@router.delete("/devices/{device_id}", response_model=DeviceRevoked)
async def revoke_device(device_id: str, user: dict = Depends(current_user)) -> dict:
    removed = auth_store.revoke_device_by_prefix(user["id"], device_id)
    return {"ok": True, "removed": removed}
