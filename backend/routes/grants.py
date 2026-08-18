"""Grants + Stores API (Phase 4) — the real grants router.

The authz surface split out of base_apps.py. Serves both the existing app-level
grants document (cross-app + guest access) and the Phase-4 resource-scoped grants
(per-invocation capability tokens over Stores). The Store catalog and the read/
write door moved to routes/stores.py, which owns everything /stores.

  GET  /grants          app-level doc + resource grants
  PUT  /grants          replace the app-level doc (validated)
  POST /grants/mint      mint a scoped grant over a Store, returns the token
  POST /grants/revoke    revoke a scoped grant by token/id

There is no `/base-apps/grants` alias — base_apps.py declares no grants route
(it only exposes app manifests; see its own comment that nothing there
enforces scope or grants). This module is the only HTTP surface for grants.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from services import permissions, stores

router = APIRouter(tags=["grants"])


# ---- response models (ADR 0023 clause 6) -----------------------------------
# Read off services/permissions.py (`_coerce_doc`, `load_resource_grants`,
# `mint_grant`). FastAPI FILTERS a return value through the declared model and
# drops any key it does not name.
#
# TWO LAYERS, ONE DOCUMENT. `grants` and `guests` are the app-level layer and
# are replaced wholesale by PUT. `resource_grants` is the Phase-4 capability
# layer and is managed ONLY through mint and revoke -- a PUT never touches it,
# which is why the PUT response does not carry it.


class AppGrant(BaseModel):
    """One app-to-app capability. `mode` is `read` or `rw`, and coerces to
    `read` for anything else rather than rejecting the row."""

    from_: str = Field(alias="from")
    to: str
    capability: str
    mode: str

    model_config = ConfigDict(populate_by_name=True)


class Guest(BaseModel):
    id: str
    app: str
    capabilities: list[str]


class ResourceGrant(BaseModel):
    """A scoped capability token over a Store, or over a subtree of Store URIs.
    ITS `id` IS THE TOKEN a provider checks per call -- there is no separate
    secret, which is why minting returns the record rather than a bare string."""

    id: str
    from_: str = Field(alias="from")
    to: str
    store_uri: str
    subtree: bool
    verbs: list[str]
    caveats: dict[str, Any]
    created_at: str

    model_config = ConfigDict(populate_by_name=True)


class GrantsDocument(BaseModel):
    grants: list[AppGrant]
    guests: list[Guest]
    resource_grants: list[ResourceGrant]


class AppGrantsDocument(BaseModel):
    """What PUT answers with: the canonical app-level document as saved. No
    `resource_grants`, because this endpoint does not touch them."""

    grants: list[AppGrant]
    guests: list[Guest]


class GrantRevoked(BaseModel):
    """False when the token matched nothing, so revoking twice is not an
    error."""

    revoked: bool



# --------------------------------------------------------------------------- #
# App-level grants document (unchanged shape; mirrors /base-apps/grants).
# --------------------------------------------------------------------------- #
@router.get("/grants", response_model=GrantsDocument)
async def get_grants():
    """The operator-owned grants document plus the resource-scoped grants:
    {"grants": [...], "guests": [...], "resource_grants": [...]}. The first two
    keys are the existing app-level layer; `resource_grants` is the Phase-4 layer
    (managed via /grants/mint and /grants/revoke, not PUT)."""
    doc = permissions.load_grants()
    return {**doc, "resource_grants": permissions.load_resource_grants()}


@router.put("/grants", response_model=AppGrantsDocument)
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


@router.post("/grants/mint", response_model=ResourceGrant)
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


@router.post("/grants/revoke", response_model=GrantRevoked)
async def revoke_grant(body: RevokeIn):
    """Revoke a scoped grant by token/id. {revoked: bool}."""
    token = body.token or body.id
    if not token:
        raise HTTPException(400, "token or id required")
    return {"revoked": permissions.revoke(token)}
