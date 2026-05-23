"""Health Auto Export ingest + viewer routes.

POST /health/ingest        body: HAE JSON (data.metrics, data.workouts)
                            auth: HTTP Basic (~/.config/clawdbot/health-auth.env)
                            response: {"ok": true, "stored": true, "metrics_seen": int}

GET  /health/latest         per-metric most-recent value index
GET  /health/history?limit&offset    paginated raw ingest records
DELETE /health/history      wipe all stored data (operator privacy toggle)
GET  /health/owntracks-helper  (no — this is just owntracks; reserved name guarded against)
GET  /health                 GET helper for accidental browser visits to /ingest
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response

from services import health

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/ingest")
async def ingest_help() -> dict[str, Any]:
    return {
        "ok": False,
        "hint": "POST-only endpoint. Health Auto Export iOS publishes here.",
        "viewer": "https://debian.tail38e62e.ts.net:8443/settings",
    }


@router.post("/ingest")
async def post_ingest(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not health.verify_basic_auth(authorization):
        response.headers["WWW-Authenticate"] = 'Basic realm="workshop-health"'
        raise HTTPException(status_code=401, detail="invalid health auth")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected object payload")
    result = health.ingest(payload)
    return {"ok": True, **result}


@router.get("/latest")
async def get_latest() -> dict[str, Any]:
    return {"metrics": health.latest()}


@router.get("/history")
async def get_history(
    limit: int = Query(25, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    page = health.history(limit=limit, offset=offset)
    return {
        "history": page["records"],
        "total": page["total"],
        "has_more": page["has_more"],
        "offset": offset,
        "limit": limit,
    }


@router.delete("/history")
async def delete_history() -> dict[str, Any]:
    return {"ok": True, **health.clear()}
