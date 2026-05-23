"""Location API.

Mounts at /location/* — the frontends post browser-geolocation pings here, and
the commute layer + LLM helpers read latest() to answer location-aware queries.

Endpoints:
    POST   /location/update      — store one position (body: LocationBody)
    GET    /location/latest      — fetch the most-recent position (or null)
    GET    /location/history     — fetch the last N positions (newest-first)
    DELETE /location/history     — erase all stored positions (operator privacy toggle)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services import location

router = APIRouter(prefix="/location", tags=["location"])


class LocationBody(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lng: float = Field(..., ge=-180.0, le=180.0)
    accuracy_m: float | None = None
    altitude_m: float | None = None
    heading_deg: float | None = None
    speed_mps: float | None = None
    client_ts: str | None = None
    source: str | None = None  # "workshop" | "commute-app" | "manual" | ...


@router.post("/update")
async def post_update(body: LocationBody) -> dict[str, Any]:
    try:
        rec = location.record(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "record": rec}


@router.get("/latest")
async def get_latest() -> dict[str, Any]:
    rec = location.latest()
    return {"latest": rec}


@router.get("/history")
async def get_history(
    limit: int = Query(25, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    page = location.history(limit=limit, offset=offset)
    # Legacy callers expect {"history": [...]}; new pagination metadata is
    # additive so old clients keep working.
    return {
        "history": page["records"],
        "total": page["total"],
        "has_more": page["has_more"],
        "offset": offset,
        "limit": limit,
    }


@router.delete("/history")
async def delete_history() -> dict[str, Any]:
    return {"ok": True, **location.clear()}
