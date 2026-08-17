"""Health Auto Export ingest + viewer routes.

POST /health/ingest        body: HAE JSON (data.metrics, data.workouts)
                            auth: HTTP Basic (~/.config/clawdbot/health-auth.env)
                            response: {"stored": true, "metrics_seen": int}

GET  /health/latest         per-metric most-recent value index, keyed by name
GET  /health/history?limit&offset    a page of raw ingest records
DELETE /health/history      wipe all stored data (operator privacy toggle)
GET  /health/owntracks-helper  (no — this is just owntracks; reserved name guarded against)
GET  /health                 GET helper for accidental browser visits to /ingest

Exemplar conversion for ADR 0023 (`docs/decisions/0023-the-http-contract.md`):
picked alongside `location.py` because `history()` already returned the only
correct pagination metadata in the codebase (`{records, total, has_more}`), so
this is the smallest honest diff onto the ADR's shape.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel

from deps_user import require_user
from services import health, auth_store, ownership
from wsplatform.http import DEFAULT_PAGE, MAX_PAGE, Page, PageMeta


def _viewer_url(path: str) -> str:
    """Where a human would go to see this, on THIS deployment.

    WORKSHOP_CANONICAL_ORIGIN when the install has one; otherwise a relative
    path, which the caller's own origin resolves correctly and which cannot
    point a stranger at somebody else's box.
    """
    origin = os.environ.get("WORKSHOP_CANONICAL_ORIGIN", "").rstrip("/")
    return f"{origin}{path}" if origin else path


router = APIRouter(prefix="/health", tags=["health"])


class IngestHelp(BaseModel):
    hint: str
    viewer: str


class IngestResult(BaseModel):
    stored: bool
    metrics_seen: int


class MetricLatest(BaseModel):
    """One metric's most-recent reading. `qty` is `Any`: Health Auto Export's
    own payload shape varies by metric (a bare number for most, a structured
    object for some, e.g. blood pressure's systolic/diastolic pair), and this
    route stores whatever it was handed rather than picking a schema HAE
    itself does not commit to."""

    ts: str | None = None
    date: str | None = None
    qty: Any = None
    units: str | None = None


class HistoryRecord(BaseModel):
    ts: str
    payload: dict[str, Any]


def _pin_ingest_owner() -> None:
    """Health ingest (Basic auth) writes to the recorded health-data owner's
    partition. Fail closed if bootstrap hasn't assigned one."""
    owner = auth_store.domain_owner("health")
    if owner is None:
        raise HTTPException(status_code=503, detail="health owner not provisioned")
    ownership.set_current_owner(owner)


@router.get("/ingest", response_model=IngestHelp)
async def ingest_help() -> IngestHelp:
    return IngestHelp(
        hint="POST-only endpoint. Health Auto Export iOS publishes here.",
        viewer=_viewer_url("/settings"),
    )


@router.post("/ingest", response_model=IngestResult)
async def post_ingest(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
) -> IngestResult:
    if not health.verify_basic_auth(authorization):
        response.headers["WWW-Authenticate"] = 'Basic realm="workshop-health"'
        raise HTTPException(status_code=401, detail="invalid health auth")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected object payload")
    _pin_ingest_owner()
    result = health.ingest(payload)
    return IngestResult(**result)


@router.get("/latest", response_model=dict[str, MetricLatest])
async def get_latest(user: dict = Depends(require_user)) -> dict[str, MetricLatest]:
    return health.latest()


@router.get("/history", response_model=Page[HistoryRecord])
async def get_history(
    user: dict = Depends(require_user),
    limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
) -> Page[HistoryRecord]:
    # Offset survives here on purpose (ADR 0023 clause 2: "offset survives
    # only where it already is") -- this route was already offset-paginated
    # and nothing has migrated it to a cursor yet. `page.offset` carries it
    # forward inside the one allowed envelope instead of as a third
    # top-level key.
    page = health.history(limit=limit, offset=offset)
    return Page[HistoryRecord](
        items=[HistoryRecord(**r) for r in page["records"]],
        page=PageMeta(limit=limit, total=page["total"], offset=offset),
    )


@router.delete("/history", status_code=204, response_model=None)
async def delete_history(user: dict = Depends(require_user)) -> None:
    health.clear()
