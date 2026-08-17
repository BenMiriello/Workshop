"""Location API.

Mounts at /location/* — the frontends post browser-geolocation pings here, and
the commute layer + LLM helpers read latest() to answer location-aware queries.

Endpoints:
    POST   /location/update      — store one position (body: LocationBody)
    GET    /location/latest      — fetch the most-recent position (or null)
    GET    /location/history     — fetch a page of positions (newest-first)
    DELETE /location/history     — erase all stored positions (operator privacy toggle)

Exemplar conversion for ADR 0023 (`docs/decisions/0023-the-http-contract.md`):
picked because `history()` already returned the only correct pagination
metadata in the codebase (`{records, total, has_more}`), so this is the
smallest honest diff onto the ADR's shape.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services import location
from wsplatform.http import DEFAULT_PAGE, Page, PageMeta

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


class LocationRecord(BaseModel):
    """One stored position ping, exactly as `services.location` persists it."""

    ts: str
    lat: float
    lng: float
    accuracy_m: float | None = None
    altitude_m: float | None = None
    heading_deg: float | None = None
    speed_mps: float | None = None
    client_ts: str | None = None
    source: str | None = None


@router.post("/update", response_model=LocationRecord, status_code=201)
async def post_update(body: LocationBody) -> LocationRecord:
    try:
        rec = location.record(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LocationRecord(**rec)


@router.get("/latest", response_model=LocationRecord | None)
async def get_latest() -> LocationRecord | None:
    rec = location.latest()
    return LocationRecord(**rec) if rec else None


@router.get("/history", response_model=Page[LocationRecord])
async def get_history(
    # le=5000, not MAX_PAGE (200): the map's breadcrumb track fetches up to
    # MAP_TRACK_LIMIT=2000 points in one call (frontend/src/pages/apps/
    # Location.tsx) so it can draw the whole recent trail without paging the
    # map through 10+ requests. This is the ADR 0023 clause 2 carve-out ("a
    # route with a genuinely different ceiling declares its own `le=` and
    # says why"), not an oversight.
    limit: int = Query(DEFAULT_PAGE, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> Page[LocationRecord]:
    # Offset survives here on purpose (ADR 0023 clause 2: "offset survives
    # only where it already is"): the table view's "Load more" and the map's
    # single wide fetch both address by offset today, and there is no cursor
    # ordering concept to migrate them to for free. `page.offset` carries it
    # forward inside the one allowed envelope instead of as a third
    # top-level key.
    page = location.history(limit=limit, offset=offset)
    return Page[LocationRecord](
        items=[LocationRecord(**r) for r in page["records"]],
        page=PageMeta(limit=limit, total=page["total"], offset=offset),
    )


@router.delete("/history", status_code=204, response_model=None)
async def delete_history() -> None:
    location.clear()
