"""What other chats see: the `share` field's read/write surface.

CURATION, NOT CONFINEMENT. See `services/memory_policy`'s module docstring and
SEC-A2. Everything on this box runs as one uid, so nothing here restricts what a
process may read; it restricts what a SEARCH surfaces, so a chat's recall stays
about that chat's own work. No route here may be described as permission or
access control.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import memory_policy

router = APIRouter(prefix="/memory-policy", tags=["memory-policy"])


class DefaultsBody(BaseModel):
    defaults: dict[str, str]


class ScopesBody(BaseModel):
    # `None` clears a scope's override and falls back to the kind's default.
    scopes: dict[str, str | None]


class ItemBody(BaseModel):
    ref: str
    share: str | None = None


@router.get("")
def read_policy() -> dict:
    """The whole document. Absent file reads as the defaults and is not created
    by reading it."""
    return memory_policy.read()


@router.put("/defaults")
def put_defaults(body: DefaultsBody) -> dict:
    """Merge into the global per-kind defaults. This is where the operator
    changes the chat-transcript default, which ships as `private`."""
    try:
        return memory_policy.set_defaults(body.defaults)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/scopes")
def put_scopes(body: ScopesBody) -> dict:
    try:
        return memory_policy.set_scopes(body.scopes)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/item")
def put_item(body: ItemBody) -> dict:
    """Set or clear one item's override, keyed by its Phase 3 ref."""
    try:
        return memory_policy.set_item(body.ref, body.share)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
