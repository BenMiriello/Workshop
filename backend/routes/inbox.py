"""Inbox routes: what agents need from the operator.

Global store, per-chat filter. `GET /api/inbox` with no scope is the whole
inbox; with `chat_id` it is what that chat should show inline.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import inbox, chat_store, knowledge

log = logging.getLogger(__name__)
# Bare prefix, NOT "/api/inbox". The edge proxy strips the leading `/api`
# before it reaches this app, so every router here mounts at its bare path
# (`/tasks`, `/notes`, `/chat`) and the frontend calls `/api/<same>`. Prefixing
# with `/api` here yields `/api/api/...` in effect: the client's request arrives
# as `/inbox`, matches nothing, and 404s while the route list looks correct.
router = APIRouter(prefix="/inbox", tags=["inbox"])


class RaiseIn(BaseModel):
    kind: str
    agent: str
    scope: str = "workshop"
    title: str
    body: str = ""
    options: list[str] | None = None
    chat_id: str | None = None
    ticket_id: str | None = None
    run_id: str | None = None
    dedupe_key: str | None = None


class ResolveIn(BaseModel):
    answer: str | None = None
    dismissed: bool = False


def _scope_for_chat(chat_id: str | None) -> tuple[str | None, str | None]:
    """(scope, chat_id) for a chat, or (None, None) for the global inbox."""
    if not chat_id:
        return None, None
    t = chat_store.get_thread(chat_id)
    if t is None:
        raise HTTPException(404, "chat not found")
    return knowledge.scope_for_context(t.get("context_key") or "general"), chat_id


@router.get("")
async def list_inbox(chat_id: str | None = None, state: str = "open"):
    """Open items. Without `chat_id` this is the global inbox; with one it is
    filtered to what that chat should surface inline (see inbox.surfaces_in_chat
    — a broad chat is deliberately not a firehose, and what it does not show
    inline is still here globally)."""
    scope, cid = _scope_for_chat(chat_id)
    items = inbox.list_items(chat_scope=scope, chat_id=cid, state=state)
    return {
        "items": items,
        "counts": inbox.counts(chat_scope=scope, chat_id=cid),
        # The global total travels with every response so a chat can say "and 4
        # more elsewhere" without a second request. A filtered view that cannot
        # tell you what it filtered out is how things get missed.
        "global_counts": inbox.counts(),
        "scope": scope,
    }


@router.post("")
async def raise_inbox(body: RaiseIn):
    """Raise an item. Idempotent on `dedupe_key` while an item is still open, so
    a triggered agent re-raising the same block every pass does not bury the
    inbox in copies of one problem."""
    try:
        return inbox.raise_item(**body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{item_id}/resolve")
async def resolve_inbox(item_id: str, body: ResolveIn):
    """Answer or dismiss. Both close the item; which one is recorded, because
    "I answered" and "I dismissed" are different facts and a run of dismissals
    is the signal that an agent is producing noise."""
    it = inbox.resolve_item(item_id, answer=body.answer, dismissed=body.dismissed)
    if it is None:
        raise HTTPException(404, "item not found")
    return it
