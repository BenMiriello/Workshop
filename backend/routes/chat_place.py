"""Where a chat is working (services/chat_place).

    GET /chat-place/{thread}   [{app, app_label, label, title, path, at}], newest first
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import chat_log, chat_place

router = APIRouter(tags=["page-link"])


class ChatPlace(BaseModel):
    """One app's place for a chat: the app, a short name, the full title, the
    Workshop page that shows it, and when the chat last worked there."""
    app: str
    app_label: str
    label: str
    title: str = ""
    path: str
    at: float | None = None


@router.get("/chat-place/{thread_id}", response_model=list[ChatPlace])
async def chat_places(thread_id: str) -> list[ChatPlace]:
    if not chat_log.exists(thread_id):
        raise HTTPException(404, "thread not found")
    return [ChatPlace.model_validate(p) for p in await chat_place.places(thread_id)]
