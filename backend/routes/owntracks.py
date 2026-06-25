"""Owntracks iOS HTTP-mode endpoint.

POST /location/owntracks
    body: Owntracks JSON (see services/owntracks.py)
    auth: HTTP Basic (creds in ~/.config/clawdbot/owntracks-auth.env)
    response: empty JSON array (Owntracks expects this even for "ok")

Public surface is on tailnet only via Tailscale Serve; no internet exposure.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response

from services import owntracks, auth_store, ownership

router = APIRouter(prefix="/location", tags=["location"])


@router.get("/owntracks")
async def owntracks_help() -> dict[str, Any]:
    """Browsers that GET this URL by accident get a clear hint instead of a
    'method not allowed' wall. The real endpoint is POST-only, called by
    Owntracks iOS in HTTP mode."""
    return {
        "ok": False,
        "hint": "POST-only endpoint. This URL is meant for Owntracks iOS to publish location pings.",
        "viewer": "https://debian.tail38e62e.ts.net/apps/location",
    }


@router.post("/owntracks")
async def post_owntracks(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
) -> list[Any]:
    if not owntracks.verify_basic_auth(authorization):
        # Tell Owntracks/Safari to prompt for credentials on 401.
        response.headers["WWW-Authenticate"] = 'Basic realm="workshop-owntracks"'
        raise HTTPException(status_code=401, detail="invalid owntracks auth")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected object payload")
    # Bind this ingest to the recorded location-data owner's partition.
    owner = auth_store.domain_owner("location")
    if owner is None:
        raise HTTPException(status_code=503, detail="location owner not provisioned")
    ownership.set_current_owner(owner)
    # Ingest; silently ignore non-location events (transitions, waypoints).
    owntracks.ingest(payload)
    # Owntracks expects a JSON array (commands/responses); empty = nothing to do.
    return []
