"""Inbox routes: what agents need from the operator.

Global store, per-chat filter. The chat Work panel reaches this router at
`POST /api/inbox/{id}/snooze` and `POST /api/inbox/{id}/resolve` — those are
the two live surfaces. The whole-inbox read (`GET /inbox`) and the raw raise
endpoint (`POST /inbox`, which mirrored `services.inbox.raise_item` over
HTTP) had zero callers — every producer that raises an item (`run_inbox`,
`services.inbox` itself) does so as a direct Python call — and were deleted
(coherence-overhaul-2026-08-16 T5).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from typing import Any

from pydantic import BaseModel, ConfigDict

from services import inbox

log = logging.getLogger(__name__)
# Bare prefix, NOT "/api/inbox". The edge proxy strips the leading `/api`
# before it reaches this app, so every router here mounts at its bare path
# (`/tasks`, `/notes`, `/chat`) and the frontend calls `/api/<same>`. Prefixing
# with `/api` here yields `/api/api/...` in effect: the client's request arrives
# as `/inbox`, matches nothing, and 404s while the route list looks correct.
router = APIRouter(prefix="/inbox", tags=["inbox"])


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




class InboxItem(BaseModel):
    """An item waiting on the operator. `kind` is question, review or notify.

    A SNOOZED item stays OPEN and keeps its dedupe key: it just stops surfacing
    until `snoozed_until`, and wakes early if the thing it is about changes.
    That is why snoozing does not touch `state`.

    `review` appears only on a resolve that also acted on a ticket, and says
    what happened to the WORK -- so the caller can state it rather than infer it
    from a closed row."""

    model_config = ConfigDict(extra="allow")

    id: str
    kind: str
    state: str
    agent: str | None = None
    scope: str
    title: str
    body: str
    options: list[str]
    chat_id: str | None = None
    ticket_id: str | None = None
    run_id: str | None = None
    dedupe_key: str | None = None
    created_at: str | None = None
    resolved_at: str | None = None
    answer: str | None = None
    snoozed_until: str | None = None
    review: dict[str, Any] | None = None


@router.post("/{item_id}/snooze", response_model=InboxItem)
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


@router.post("/{item_id}/resolve", response_model=InboxItem)
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
    if reviewed is not None:
        # What actually happened to the work, so the caller can say so rather
        # than inferring it from a closed row.
        it = {**it, "review": reviewed}
    return it
