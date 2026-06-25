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
    return os.environ.get("WORKSHOP_REGISTRATION_OPEN", "1").strip().lower() not in {
        "0", "false", "no",
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


def _login_response(response: Response, user: dict, request: Request) -> dict:
    raw = auth_store.create_session(user["id"], user_agent=request.headers.get("user-agent"))
    _set_session_cookie(response, raw)
    return {"user": auth_store.public_user(user)}


@router.post("/register")
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


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    user = auth_store.verify_credentials(str(body.email), body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    return _login_response(response, user, request)


@router.post("/logout")
async def logout(body: LogoutBody, request: Request, response: Response,
                 user: dict = Depends(current_user)) -> dict:
    if body.all_devices:
        auth_store.revoke_all_for_user(user["id"])
        _clear_session_cookie(response)
        return {"ok": True, "scope": "all"}
    auth_store.revoke_session(request.cookies.get(SESSION_COOKIE))
    _clear_session_cookie(response)
    return {"ok": True, "scope": "session"}


@router.post("/password")
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


@router.get("/me")
async def me(user: dict = Depends(current_user)) -> dict:
    full = auth_store.get_by_id(user["id"])
    if full is None:
        raise HTTPException(status_code=401, detail="account no longer exists")
    return auth_store.public_user(full)


# ---- device tokens (native background producers) ----


@router.post("/device-token")
async def issue_device_token(body: DeviceTokenBody, user: dict = Depends(current_user)) -> dict:
    """Mint a revocable device token for this account's native background tasks
    (location/audio), which run outside the WebView and cannot use the cookie.
    The raw token is returned ONCE; store it in the iOS Keychain."""
    raw = auth_store.create_device_token(user["id"], body.label)
    return {"device_token": raw, "label": body.label or "device"}


@router.get("/devices")
async def list_devices(user: dict = Depends(current_user)) -> dict:
    return {"devices": auth_store.list_devices(user["id"])}


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: str, user: dict = Depends(current_user)) -> dict:
    removed = auth_store.revoke_device_by_prefix(user["id"], device_id)
    return {"ok": True, "removed": removed}
