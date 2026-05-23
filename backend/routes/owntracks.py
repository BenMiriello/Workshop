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

from services import owntracks

router = APIRouter(prefix="/location", tags=["location"])


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
    # Ingest; silently ignore non-location events (transitions, waypoints).
    owntracks.ingest(payload)
    # Owntracks expects a JSON array (commands/responses); empty = nothing to do.
    return []
