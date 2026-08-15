"""Inbox routes: what agents need from the operator.

Global store, per-chat filter. `GET /api/inbox` with no scope is the whole
inbox; with `chat_id` it is what that chat should show inline.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import inbox, chat_store, knowledge, memory_promotion

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


class SnoozeIn(BaseModel):
    """Either an explicit instant or one of the panel's presets. `until: null`
    with no preset wakes the item now, so the control is its own undo."""
    until: str | None = None
    preset: str | None = None


# The answers that mean yes. The frontend used to post "approved" against a
# backend that compared against "approve", so approving a memory promotion
# closed the row and wrote nothing. Both literals are accepted now, because a
# one-word protocol between two files is not worth a second outage, and the
# comparison lives here so a backend test can pin it.
_APPROVALS = {"approve", "approved"}


def _is_approval(answer: str | None) -> bool:
    return str(answer or "").strip().lower() in _APPROVALS


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
    # The producers that keep an open read honest live in `inbox.reconcile`, so
    # the chat's aggregated work read runs exactly the same sequence rather than
    # a second copy of it that can drift.
    if state == "open":
        inbox.reconcile()
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


@router.post("/{item_id}/snooze")
async def snooze_inbox(item_id: str, body: SnoozeIn):
    """Defer an item. It stays open and keeps its dedupe key; it just stops
    surfacing until then, and wakes early if the thing it is about changes
    (services/inbox._refresh_on_reraise)."""
    try:
        until = (inbox.snooze_until(body.preset) if body.preset else body.until)
        it = inbox.snooze_item(item_id, until)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if it is None:
        raise HTTPException(404, "item not found")
    return it


async def _answer_the_task(item: dict, *, answer: str | None, dismissed: bool) -> dict | None:
    """A task-derived item answers the TASK, not just the row on screen.

    Closing the inbox item was the whole of "resolve" until now, and the inbox
    item is a projection: the block lives on the task, and only
    `POST /tasks/{scope}/{id}/review` clears it, mints what the block proposed,
    and writes the answer into the ticket body the next attempt reads. So
    answering here reached nothing — the task stayed `awaiting`, run_inbox raised
    the same question on its next pass twenty seconds later, and "Approve" on a
    permission proposal granted nothing at all.

    The verbs map straight across:
      Approve (on a block that proposed a grant) -> grant-and-continue
      any other answer                           -> approve, with the answer as
                                                    the note, which is what
                                                    lands in the ticket body
      dismiss (the X)                            -> dismiss: the block stays,
                                                    it just stops asking

    Returns the review result, or None when this is not a task item.
    """
    key = item.get("dedupe_key") or ""
    if not key.startswith("task:"):
        return None
    scope = str(item.get("scope") or "").strip()
    task_id = str(item.get("ticket_id") or "").strip()
    if not scope or not task_id:
        return None

    from routes.tasks_all import TaskReviewIn, review_task

    ans = str(answer or "").strip()
    if dismissed:
        action, note = "dismiss", ""
    elif _is_approval(ans) and any(
        str(o).strip().lower() == "approve" for o in (item.get("options") or [])
    ):
        # run_inbox offers Approve/Decline ONLY when the block carried a
        # structured proposal, so an Approve here is an approval of that
        # proposal and has to mint it. Defaults come from the block itself.
        action, note = "grant-and-continue", ""
    else:
        action, note = "approve", ans
    try:
        return await review_task(scope, task_id, TaskReviewIn(action=action, note=note or None))
    except HTTPException:
        if dismissed:
            # The block is already gone (answered elsewhere, or the task moved
            # on). Dismissing a stale row must still clear it from the inbox.
            return None
        raise


@router.post("/{item_id}/resolve")
async def resolve_inbox(item_id: str, body: ResolveIn):
    """Answer or dismiss. Both close the item; which one is recorded, because
    "I answered" and "I dismissed" are different facts and a run of dismissals
    is the signal that an agent is producing noise."""
    # Read the item BEFORE resolving: resolving is what makes an approval
    # actionable, and afterwards its dedupe key is no longer open to match on.
    before = inbox.get_item(item_id)
    # THE TASK FIRST. If the review is refused, the item stays open: an inbox row
    # that closed on a write that did not happen is the failure this is fixing.
    reviewed = None
    if before:
        reviewed = await _answer_the_task(
            before, answer=body.answer, dismissed=body.dismissed)
    it = inbox.resolve_item(item_id, answer=body.answer, dismissed=body.dismissed)
    if it is None:
        raise HTTPException(404, "item not found")
    # An approved promotion is the one answer that performs a write. Everything
    # else is just a recorded decision.
    key = (before or {}).get("dedupe_key") or ""
    if not body.dismissed and _is_approval(body.answer) and key.startswith("promote:"):
        try:
            done = memory_promotion.approve(key)
            it = {**it, "promoted": done}
        except Exception as e:
            log.warning("inbox: promotion apply failed for %s: %s", key, e)
            it = {**it, "promoted": False}
    if reviewed is not None:
        # What actually happened to the work, so the caller can say so rather
        # than inferring it from a closed row.
        it = {**it, "review": reviewed}
    return it
