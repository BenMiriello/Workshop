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

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from deps_user import require_user
from services import health, auth_store, ownership

def _viewer_url(path: str) -> str:
    """Where a human would go to see this, on THIS deployment.

    WORKSHOP_CANONICAL_ORIGIN when the install has one; otherwise a relative
    path, which the caller's own origin resolves correctly and which cannot
    point a stranger at somebody else's box.
    """
    origin = os.environ.get("WORKSHOP_CANONICAL_ORIGIN", "").rstrip("/")
    return f"{origin}{path}" if origin else path


router = APIRouter(prefix="/health", tags=["health"])


def _pin_ingest_owner() -> None:
    """Health ingest (Basic auth) writes to the recorded health-data owner's
    partition. Fail closed if bootstrap hasn't assigned one."""
    owner = auth_store.domain_owner("health")
    if owner is None:
        raise HTTPException(status_code=503, detail="health owner not provisioned")
    ownership.set_current_owner(owner)


@router.get("/ingest")
async def ingest_help() -> dict[str, Any]:
    return {
        "ok": False,
        "hint": "POST-only endpoint. Health Auto Export iOS publishes here.",
        "viewer": _viewer_url("/settings"),
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
    _pin_ingest_owner()
    result = health.ingest(payload)
    return {"ok": True, **result}


@router.get("/latest")
async def get_latest(user: dict = Depends(require_user)) -> dict[str, Any]:
    return {"metrics": health.latest()}


@router.get("/history")
async def get_history(
    user: dict = Depends(require_user),
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
async def delete_history(user: dict = Depends(require_user)) -> dict[str, Any]:
    return {"ok": True, **health.clear()}
