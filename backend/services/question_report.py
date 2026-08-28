"""When a ticket stops to ask something, tell the chat that asked for the work.

THE GAP THIS CLOSES. A worker that hits an ambiguity writes an `awaiting` record
and the ticket goes `blocked`. That record renders on the Work page, which is the
right place for someone already looking at the queue — and the wrong place for
the person who asked for the work in a conversation and has no reason to be
watching a queue. The audit behind
`docs/platform-redesign/ticket-model-and-external-trackers-2026-08-07.md` found
zero `awaiting` records across 25 live stores; a question nobody is shown is
indistinguishable from a question never asked.

So a raised question goes to BOTH surfaces: the queue, and the originating chat.
`source.ref` already records which conversation filed the ticket, and it was
already resolved for the ticket's "origin" link, so nothing new has to be stored.

This mirrors `services/run_report` deliberately — same owner resolution, same
never-raise contract, same reason (the transcript is what the operator reads, and
the live CLI session is not injected into mid-turn). The two differ only in what
they say and when.
"""
from __future__ import annotations

import logging

log = logging.getLogger("workshop.question_report")

# Asking is a courtesy path, exactly like reporting. Nothing here may raise into
# the caller: a worker that correctly stopped to ask must never fail because
# telling someone about it went wrong.


def _compose(task: dict, request: dict, scope: str | None) -> str:
    """The message body. Short, and it leads with the question."""
    ask = str(request.get("ask") or request.get("question") or "").strip()
    # THE TITLE IS THE LINK. It used to be bold text with a separate closing
    # paragraph carrying the ref, which meant the one useful noun on screen was
    # not clickable and the ref was a second mention of the same ticket.
    ref = f"workshop:ticket/{task.get('id')}"
    label = str(task.get("title") or "A ticket").strip()
    lines = [f"[{label}]({ref}) needs an answer.", "", f"> {ask}"]

    why = str(request.get("why") or "").strip()
    if why:
        lines += ["", why]

    options = [str(o).strip() for o in (request.get("options") or []) if str(o).strip()]
    if options:
        lines += ["", "Options:"] + [f"- {o}" for o in options]

    default = str(request.get("default") or "").strip()
    if default:
        lines += ["", f"Its own pick, if you'd rather not decide: {default}"]

    unblocks = str(request.get("unblocks_if") or "").strip()
    if unblocks:
        lines += ["", f"Unblocks if: {unblocks}"]

    # NO "WHERE TO ANSWER" LINE. There used to be one, and it has now been wrong
    # in two different ways on the same day.
    #
    # It first read "Answer here, or on the ticket: /tickets/<scope>/<id>":
    # nothing routes a chat reply back to a ticket's `awaiting` record, so
    # "answer here" was false, and a bare path is not a link at all. Rewritten to
    # point at the Tickets panel with a managed ref, it became false again the
    # moment `TicketDecision` shipped, because the buttons are now rendered
    # directly beneath this message. Telling the reader to go to another surface
    # while the control sits under the sentence is worse than saying nothing.
    #
    # The rule this now follows: this message is the permanent record of WHAT was
    # asked, and the block below it is the control. Neither restates the other.
    # See docs/platform-redesign/one-fact-one-place-2026-08-22.md.
    return "\n".join(lines)


def post_for_task(task: dict, request: dict, *, scope: str | None = None) -> bool:
    """Post one question into the chat that filed this ticket.

    Returns True if a chat was told. False is the normal case for a ticket nobody
    started from a conversation.
    """
    try:
        src = task.get("source") or {}
        if src.get("kind") != "chat":
            return False
        thread_id = str(src.get("ref") or "").strip()
        if not thread_id:
            return False
        ask = str(request.get("ask") or request.get("question") or "").strip()
        if not ask:
            return False  # nothing to ask; not a question worth interrupting for

        from services import chat_store, ownership, run_report

        owner = run_report.owner_of_thread(thread_id)
        if owner is None:
            return False

        previous = ownership.owner_or_none()
        try:
            ownership.set_current_owner(owner)
            chat_store.append_message(
                thread_id, "assistant", _compose(task, request, scope),
                # AUTONOMOUS: a sweep hit a block and had to ask. Marked so
                # the thread shows who is speaking before the words are read.
                meta={"kind": "ticket_question", "autonomous": True,
                      "sender": "platform",
                      "ticket": task.get("id"),
                      "scope": scope, "block_kind": request.get("kind")},
            )
            return True
        finally:
            if previous is None:
                ownership.clear_current_owner()
            else:
                ownership.set_current_owner(previous)
    except Exception as e:
        log.debug("question report for task %s skipped: %s", task.get("id"), e)
        return False
