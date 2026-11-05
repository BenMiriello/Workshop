"""The dashboard's saved app order. Logic in services/dashboard_order.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from deps_user import current_user
from services import dashboard_order

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class Layout(BaseModel):
    order: list[str]


class LayoutBody(BaseModel):
    order: list[str] = Field(max_length=500)


@router.get("/layout", response_model=Layout)
def get_layout(user: dict = Depends(current_user)) -> dict:
    return {"order": dashboard_order.order(user["id"])}


@router.put("/layout", response_model=Layout)
def put_layout(body: LayoutBody, user: dict = Depends(current_user)) -> dict:
    return {"order": dashboard_order.set_order(user["id"], body.order)}
