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


@router.post("/register")
async def register(body: RegisterBody) -> dict[str, Any]:
    try:
        return push.register_token(body.token, body.platform)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/register")
async def unregister(body: RegisterBody) -> dict[str, Any]:
    return push.remove_token(body.token)


@router.get("/status")
async def status() -> dict[str, Any]:
    return push.status()
