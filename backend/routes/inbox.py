"""Inbox routes: answering whatever is waiting on the operator.

Global store, per-chat filter. The chat Work panel reaches this router at
`POST /api/inbox/{id}/snooze` and `POST /api/inbox/{id}/resolve` — those are
the two live surfaces. The whole-inbox read (`GET /inbox`) and the raw raise
endpoint (`POST /inbox`, which mirrored `services.inbox.raise_item` over
HTTP) had zero callers (every producer that raises an item does so as a
direct Python call) and were deleted (coherence-overhaul-2026-08-16 T5).

TWO KINDS OF ID REACH `resolve`, and only one of them names a stored row.
A waiting ticket is not copied into the inbox any more: it is read off the
ticket by `services/chat_work`, which gives its row the derived id
`task:<scope>:<id>`. So an answer to one of those is answered on the TICKET and
there is nothing to close afterwards. Everything else is a stored item raised by
an ambient agent, and closing the row is the whole of the act.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from typing import Any

from pydantic import BaseModel, ConfigDict

from routes.tasks_all import TaskReviewIn, _require_root, review_task
from services import chat_work, inbox, task_decision, tasks

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

    Either a stored row raised by an ambient agent, or a waiting TICKET read as
    one (`services/chat_work.needs_you_id`). The shape is the same because the
    panel renders one column, and only the id says which it was.

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


@router.post("/{item_id}/snooze", response_model=InboxItem, response_model_exclude_unset=True)
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


async def _decide_the_ticket(item_id: str, scope: str, task_id: str, *,
                             answer: str | None, dismissed: bool) -> dict:
    """Answer a WAITING TICKET, whose row the client read straight off it.

    The row is derived (`services/chat_work.needs_you_id`), so there is nothing
    to close when this returns: the ticket moves, and the next read of the work
    payload simply does not derive a row for it any more. That is the whole point
    of the derivation -- the copy that used to be closed here is what went stale
    between the answer and the next reconcile.

    WHICH QUESTION IS THIS. A finished ticket and a stuck one ask different
    things, and the answer to one is the wrong verb for the other. This used to
    send every answer to `approve`, which on a FINISHED ticket meant "release the
    block" -- so the operator's "Approve" un-accepted committed work, put it back
    in the queue and refunded an attempt (2026-08-22). The ticket's own status
    decides; nothing is assumed.

      Approve, on a block carrying a structured proposal -> mint it and continue
      any answer to a finished ticket -> accept, or send back with the note
      any answer to a stuck one       -> release it, the note is the answer
      dismiss (the X)                 -> the block stays, it just stops asking
    """
    root = _require_root(scope)
    task = tasks.get_task(root, task_id)
    if task is None:
        # A ticket that is gone cannot be answered, but a stale row must still be
        # dismissable: the operator is clearing something off a surface, and
        # refusing that leaves it there with no way to be rid of it.
        if dismissed:
            return _closed_row(item_id, scope, None, task_id,
                               answer=answer, dismissed=True)
        raise HTTPException(404, f"ticket not found: {task_id}")

    ans = str(answer or "").strip()
    status = task.get("status")
    proposal = (task.get("awaiting") or {}).get("proposal")

    if dismissed:
        action, note = "dismiss", ""
    elif proposal and _is_approval(ans):
        # The block proposed a grant, so an Approve here IS the approval of that
        # proposal and has to mint it. Defaults come from the block itself.
        action, note = "grant-and-continue", ""
    elif status == "in_review":
        # The work is finished. Taking it and sending it back are both real
        # answers, so the words decide which -- and both surfaces offer them as
        # buttons (`chat_work.needs_you_options`) so the words rarely have to.
        action = "accept" if task_decision.reads_as_approval(ans) else "send-back"
        # A tapped button posts its own label. That is the decision, not a note:
        # writing it into the ticket body would leave the next attempt an answer
        # with no content, which is the noise this whole change removes.
        note = "" if task_decision.is_bare_verb(ans) else ans
    else:
        action, note = "approve", ans

    reviewed: dict | None
    try:
        reviewed = await review_task(scope, task_id,
                                     TaskReviewIn(action=action, note=note or None))
    except HTTPException:
        if not dismissed:
            raise
        # The block is already gone (answered elsewhere, or the ticket moved on).
        # Dismissing a stale row must still succeed; there is nothing left to do.
        reviewed = None
    row = _closed_row(item_id, scope, task, task_id, answer=answer, dismissed=dismissed)
    if reviewed is not None:
        # What actually happened to the WORK, so the caller can state it rather
        # than infer it from a closed row.
        row["review"] = reviewed
    return row


def _closed_row(item_id: str, scope: str, task: dict | None, task_id: str, *,
                answer: str | None, dismissed: bool) -> dict:
    """The row the client just closed, echoed back in the shape it read.

    Built here rather than fetched, because a derived row is not stored anywhere
    and the ticket it came from has, by this point, deliberately stopped being
    one."""
    title = ""
    if task is not None:
        title = str(task.get("title") or task.get("text") or "").split("\n")[0][:120]
    return {
        "id": item_id,
        "kind": "question",
        "state": "dismissed" if dismissed else "answered",
        "scope": scope,
        "title": title,
        "body": "",
        "options": [],
        "ticket_id": task_id,
        "answer": answer,
        "resolved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


@router.post("/{item_id}/resolve", response_model=InboxItem, response_model_exclude_unset=True)
async def resolve_inbox(item_id: str, body: ResolveIn):
    """Answer or dismiss. Both close the item; which one is recorded, because
    "I answered" and "I dismissed" are different facts and a run of dismissals
    is the signal that an agent is producing noise."""
    derived = chat_work.parse_needs_you_id(item_id)
    if derived is not None:
        # A waiting ticket. The answer goes to the ticket and stops there: no
        # row exists to close behind it, so no row can be left claiming a write
        # that did not happen.
        return await _decide_the_ticket(item_id, *derived,
                                        answer=body.answer, dismissed=body.dismissed)
    it = inbox.resolve_item(item_id, answer=body.answer, dismissed=body.dismissed)
    if it is None:
        raise HTTPException(404, "item not found")
    return it
