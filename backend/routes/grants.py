"""Grants + Stores API (Phase 4) — the real grants router.

The authz surface split out of base_apps.py. Serves both the existing app-level
grants document (cross-app + guest access) and the Phase-4 resource-scoped grants
(per-invocation capability tokens over Stores), plus the Store catalog the broker
picks from.

  GET  /grants          app-level doc + resource grants
  PUT  /grants          replace the app-level doc (validated)
  POST /grants/mint      mint a scoped grant over a Store, returns the token
  POST /grants/revoke    revoke a scoped grant by token/id
  GET  /stores?type=     the declared Store catalog, optionally filtered by type

base_apps.py keeps a thin `/base-apps/grants` alias (GET/PUT) until Phase 5, so
the existing frontend caller keeps working while pages repoint here.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from services import permissions, stores

router = APIRouter(tags=["grants"])


# --------------------------------------------------------------------------- #
# App-level grants document (unchanged shape; mirrors /base-apps/grants).
# --------------------------------------------------------------------------- #
@router.get("/grants")
async def get_grants():
    """The operator-owned grants document plus the resource-scoped grants:
    {"grants": [...], "guests": [...], "resource_grants": [...]}. The first two
    keys are the existing app-level layer; `resource_grants` is the Phase-4 layer
    (managed via /grants/mint and /grants/revoke, not PUT)."""
    doc = permissions.load_grants()
    return {**doc, "resource_grants": permissions.load_resource_grants()}


@router.put("/grants")
async def put_grants(doc: dict = Body(...)):
    """Replace the full app-level grants document ({"grants", "guests"}). Validates
    the top-level shape (400 on a bad shape) and atomically saves. Resource grants
    are NOT touched by this endpoint."""
    try:
        return permissions.save_grants(doc)
    except ValueError as e:
        raise HTTPException(400, str(e))


# --------------------------------------------------------------------------- #
# Resource-scoped grants (the broker mints; providers check).
# --------------------------------------------------------------------------- #
class MintIn(BaseModel):
    from_app: str = ""
    to: str
    store_uri: str
    verbs: list[str]
    caveats: dict | None = None
    subtree: bool = False


@router.post("/grants/mint")
async def mint_grant(body: MintIn):
    """Mint a scoped capability token over a Store (or a subtree of Store URIs).
    Returns the grant record; its `id` is the token a provider checks per call.
    400 if the store is unknown or no valid verb is given."""
    if not body.subtree and stores.get(body.store_uri) is None:
        raise HTTPException(400, f"unknown store: {body.store_uri}")
    try:
        return permissions.mint_grant(
            from_app=body.from_app,
            to=body.to,
            store_uri=body.store_uri,
            verbs=body.verbs,
            caveats=body.caveats,
            subtree=body.subtree,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


class RevokeIn(BaseModel):
    token: str | None = None
    id: str | None = None


@router.post("/grants/revoke")
async def revoke_grant(body: RevokeIn):
    """Revoke a scoped grant by token/id. {revoked: bool}."""
    token = body.token or body.id
    if not token:
        raise HTTPException(400, "token or id required")
    return {"revoked": permissions.revoke(token)}


# --------------------------------------------------------------------------- #
# Store catalog (the broker's menu).
# --------------------------------------------------------------------------- #
@router.get("/stores")
async def list_stores(type: str | None = None):
    """The declared Store catalog, optionally filtered by `type`. Registers from
    every source on each read so a newly-installed app's stores show up.
    {"stores": [...], "types": [...]}."""
    stores.register_all()
    return {
        "stores": [s.to_dict() for s in stores.list(type=type)],
        "types": stores.types(),
    }
