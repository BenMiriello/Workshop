"""Push registration + status.

Mounts at /push/*. The device-token registry is live (free); actual remote
sending is the deferred paid feature (see services/push.py).

    POST /push/register   — register this device's APNs token (body: RegisterBody)
    DELETE /push/register — remove a token
    GET  /push/status     — registry + whether APNs sending is configured
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import push

router = APIRouter(prefix="/push", tags=["push"])


class RegisterBody(BaseModel):
    token: str
    platform: str = "ios"



class Registered(BaseModel):
    """`count` is the registry size after the write, so a re-register of a token
    already present reports the same number rather than an increment."""

    ok: bool
    count: int


class Unregistered(BaseModel):
    """`removed` is 0 when the token was not present, which makes unregister
    idempotent without an error."""

    ok: bool
    removed: int


class PushStatus(BaseModel):
    """The device-token registry is live and free; actual remote SENDING is the
    deferred paid feature. `configured` is what separates the two, so a client
    can register a device honestly while nothing can yet be delivered to it."""

    configured: bool
    tokens: int
    topic: str | None = None
    host: str | None = None


@router.post("/register", response_model=Registered, response_model_exclude_unset=True)
async def register(body: RegisterBody) -> dict[str, Any]:
    try:
        return push.register_token(body.token, body.platform)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/register", response_model=Unregistered, response_model_exclude_unset=True)
async def unregister(body: RegisterBody) -> dict[str, Any]:
    return push.remove_token(body.token)


@router.get("/status", response_model=PushStatus, response_model_exclude_unset=True)
async def status() -> dict[str, Any]:
    return push.status()
