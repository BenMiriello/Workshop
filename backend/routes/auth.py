"""Product-auth endpoints: register / login / refresh / logout / me.

Public entry points (not behind the service-token gate) so a user can sign in
from off-host. Issues a short-lived access JWT plus a long-lived, revocable
refresh JWT; refresh rotates (old token revoked, new pair minted) so a leaked
refresh token has a bounded blast radius.
"""
from __future__ import annotations

import os
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from deps_user import require_user
from services import auth_store, jwt_tokens

router = APIRouter(prefix="/auth", tags=["auth"])

# Pragmatic email shape check. We deliberately avoid the email-validator dep so
# the backend ships with no extra install; full RFC validation isn't worth a
# compiled/DNS dependency for a login form.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(value: str) -> str:
    value = value.strip()
    if not _EMAIL_RE.match(value):
        raise ValueError("a valid email is required")
    return value


def _registration_open() -> bool:
    return os.environ.get("WORKSHOP_REGISTRATION_OPEN", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


class RegisterBody(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=256)
    name: str | None = Field(default=None, max_length=120)

    _check_email = field_validator("email")(_valid_email)


class LoginBody(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=256)

    _check_email = field_validator("email")(_valid_email)


class RefreshBody(BaseModel):
    refresh_token: str


class LogoutBody(BaseModel):
    refresh_token: str | None = None
    all_devices: bool = False


def _issue(user: dict) -> dict:
    access = jwt_tokens.make_access(user)
    refresh, jti, expires_at = jwt_tokens.make_refresh(user)
    auth_store.store_refresh(jti, user["id"], expires_at)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": jwt_tokens.access_ttl_seconds(),
        "user": auth_store.public_user(user),
    }


@router.post("/register")
async def register(body: RegisterBody) -> dict:
    if not _registration_open():
        raise HTTPException(status_code=403, detail="registration is closed")
    try:
        user = auth_store.create_user(str(body.email), body.password, body.name)
    except auth_store.EmailTaken:
        raise HTTPException(status_code=409, detail="that email already has an account")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _issue(user)


@router.post("/login")
async def login(body: LoginBody) -> dict:
    user = auth_store.verify_credentials(str(body.email), body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    return _issue(user)


@router.post("/refresh")
async def refresh(body: RefreshBody) -> dict:
    try:
        claims = jwt_tokens.decode(body.refresh_token, "refresh")
    except jwt_tokens.TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    jti = claims.get("jti", "")
    if not auth_store.refresh_is_valid(jti):
        raise HTTPException(status_code=401, detail="refresh token revoked or expired")
    user = auth_store.get_by_id(int(claims["sub"]))
    if user is None:
        raise HTTPException(status_code=401, detail="account no longer exists")
    # Rotate: the presented refresh token can't be replayed after this.
    auth_store.revoke_refresh(jti)
    return _issue(user)


@router.post("/logout")
async def logout(body: LogoutBody, user: dict = Depends(require_user)) -> dict:
    if body.all_devices:
        auth_store.revoke_all_for_user(user["id"])
        return {"ok": True, "scope": "all"}
    if body.refresh_token:
        try:
            claims = jwt_tokens.decode(body.refresh_token, "refresh")
            auth_store.revoke_refresh(claims.get("jti", ""))
        except jwt_tokens.TokenError:
            pass  # already invalid — nothing to revoke
    return {"ok": True, "scope": "session"}


@router.get("/me")
async def me(user: dict = Depends(require_user)) -> dict:
    full = auth_store.get_by_id(user["id"])
    if full is None:
        raise HTTPException(status_code=401, detail="account no longer exists")
    return auth_store.public_user(full)
