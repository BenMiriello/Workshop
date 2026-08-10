"""Operator to task-store backlog bridge (future `!todo` seam).

Files a canonical task into the workshop root task store (`.tasks.json`) via the
same dedup path chat spawn-task and project task-creation use, so a backlog item
dedupes against open tasks instead of piling up unparseable prose. A future
Discord `!todo <text>` command can hit this endpoint.

The `/backlog` route stays mounted as the seam. There are no other callers, so
the request/response shape is whatever the task store returns.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services import paths, task_dedup

router = APIRouter(prefix="/backlog", tags=["backlog"])

WORKSHOP_ROOT = paths.workshop_root()


class BacklogItem(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    source: str = "discord"  # discord | manual | webhook | etc.


@router.post("")
async def add_to_backlog(item: BacklogItem):
    """File the item as a canonical task, deduped against open tasks in the
    workshop root scope. Returns the dedup decision plus the task record."""
    result = task_dedup.dedup_add(
        WORKSHOP_ROOT,
        text=item.text,
        source={"kind": "operator", "ref": f"backlog:{item.source}"},
    )
    return {
        "ok": True,
        "task": result["task"],
        "decision": result["decision"],
        "matched_id": result.get("matched_id"),
        "cosine": result.get("cosine"),
        "message": result["message"],
    }
