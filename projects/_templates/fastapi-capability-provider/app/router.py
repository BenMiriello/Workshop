"""HTTP surface for the {{title}} capability provider.

This module declares the capabilities this service PROVIDES to the rest of the
Workshop (see PROVIDES) and exposes them as real FastAPI routes. Add a new
capability by adding a route here and listing its name in PROVIDES; when this
project is registered as a Workshop app, mirror PROVIDES into the app manifest's
`provides` list (backend/apps.json) so the platform can resolve grants.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Capabilities this service exposes to the platform, in the "<app>.<verb>" shape
# used across the Workshop (e.g. finance.read, location.read).
PROVIDES: list[str] = ["{{slug}}.read"]

router = APIRouter(prefix="/{{slug}}", tags=["{{slug}}"])

# In-memory seed so the endpoints return real data out of the box. Replace this
# with the actual data source for {{purpose}} (a database, an upstream API, etc.).
_ITEMS: list[dict] = [
    {"id": "example", "label": "Example item"},
]


class ItemIn(BaseModel):
    id: str
    label: str


@router.get("/capabilities")
def capabilities() -> dict:
    """List the capabilities this provider exposes."""
    return {"provides": PROVIDES}


@router.get("/items")
def list_items() -> dict:
    """Return the current items."""
    return {"items": _ITEMS}


@router.get("/items/{item_id}")
def get_item(item_id: str) -> dict:
    """Return a single item by id, or 404 if it does not exist."""
    for item in _ITEMS:
        if item["id"] == item_id:
            return item
    raise HTTPException(404, f"item not found: {item_id}")


@router.post("/items")
def add_item(body: ItemIn) -> dict:
    """Add an item. Rejects a duplicate id with 409."""
    if any(it["id"] == body.id for it in _ITEMS):
        raise HTTPException(409, f"item already exists: {body.id}")
    item = {"id": body.id, "label": body.label}
    _ITEMS.append(item)
    return item
