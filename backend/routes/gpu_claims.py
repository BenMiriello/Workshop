"""GPU claims over HTTP, for processes on this machine (apps' own servers).

Loopback only and holds nothing personal. The rules are in
services/gpu_claims.py; an app reaches this at $WORKSHOP_API_URL/gpu/claims.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import gpu_claims

router = APIRouter(prefix="/gpu/claims", tags=["gpu"])


class ClaimBody(BaseModel):
    kind: str
    ttl_s: float = Field(default=15.0, gt=0)
    device: int = Field(default=0, ge=0)
    label: str = ""


@router.get("")
async def list_claims() -> dict:
    return {"claims": gpu_claims.snapshot()}


@router.put("/{name}")
async def put_claim(name: str, body: ClaimBody) -> dict:
    try:
        return gpu_claims.claim(name, body.kind, body.ttl_s, body.device, body.label)
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e


@router.delete("/{name}")
async def delete_claim(name: str) -> dict:
    return {"released": gpu_claims.release(name)}
