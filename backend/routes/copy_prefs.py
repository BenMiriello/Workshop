"""`/copies/prefs`: what happens to a copy made in the Workshop, per user, for
Settings > Copy and paste and any app that offers the same choice
(services/copy_prefs.py)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from services import copy_prefs

router = APIRouter(prefix="/copies", tags=["copies"])

Destination = Literal["system", "workshop"]


class CopyPrefs(BaseModel):
    save: bool
    destination: Destination


class CopyPrefsChange(BaseModel):
    save: bool | None = None
    destination: Destination | None = None


@router.get("/prefs", response_model=CopyPrefs)
def get_prefs() -> CopyPrefs:
    return CopyPrefs(**copy_prefs.current())


@router.put("/prefs", response_model=CopyPrefs)
def put_prefs(body: CopyPrefsChange) -> CopyPrefs:
    return CopyPrefs(**copy_prefs.update(body.save, body.destination))
