"""What an operator's answer to a waiting ticket MEANS, in one place.

THE DEFECT THIS CLOSES. The word `approve` named two opposite outcomes in two
routers. `routes/jobs.py` mapped it to `in_review -> done` ("accept the worker's
completion"); `routes/tasks_all.review_task` mapped it to "release the block, the
task is `todo` and runs again". The chat's Tickets panel posted free text to the
second one regardless of what the ticket was waiting for, so on 2026-08-22 the
operator's answer of "Approve" to a FINISHED ticket un-accepted it: status went
back to `todo`, an attempt was refunded, and the word "Approve" was written into
the ticket body as an instruction to the next attempt. The work was already
committed, so the queue was free to redo it.

`entity-model-2026-07-18.md` had already ratified the lifecycle
(`in_review -> done` on approval), so that was a divergence from the model of
record, not an open question.

THE RULE. A verb is named for its OUTCOME, and one act has one name. Which verb
an untyped answer becomes is chosen from the ticket's STATUS, never assumed,
because the two waiting states ask different questions:

    in_review   the work is finished, is it right?   -> accept | send_back
    blocked     the worker is stuck                  -> answer

WHAT THIS MODULE DOES NOT OWN. Minting a grant and recording a rule are review
side effects with their own services, and they stay in `routes/tasks_all`. They
call `answer` here for the release itself, so there is still exactly one place
that decides what releasing does.

See `docs/platform-redesign/deciding-on-work-from-a-chat-2026-08-22.md`.
"""
from __future__ import annotations

# Named for the outcome. `approve` is deliberately absent: it is the word that
# broke, and no verb here may be readable two ways.
ACCEPT = "accept"
SEND_BACK = "send_back"
ANSWER = "answer"
CANCEL = "cancel"
DISMISS = "dismiss"

VERBS = (ACCEPT, SEND_BACK, ANSWER, CANCEL, DISMISS)

# The status each verb answers. None = any status (a ticket can be cancelled or
# a question dismissed from wherever it is).
ANSWERS_STATUS: dict[str, str | None] = {
    ACCEPT: "in_review",
    SEND_BACK: "in_review",
    ANSWER: "blocked",
    CANCEL: None,
    DISMISS: None,
}

# Terminal states. A verb whose outcome is already true is a no-op, not a
# conflict: the operator's intent is realized either way, and 409-ing on it is
# how a stale surface gets stuck forever (the same reasoning `routes/jobs.py`
# already applied to its own drift).
TERMINAL = ("done", "cancelled")

# Only these carry an instruction to a FUTURE attempt, so only these are written
# into the ticket body. An acceptance is a fact about the past; putting it in the
# body leaves the next reader an answer with no question.
WRITES_TO_BODY = (SEND_BACK, ANSWER)


def verb_for(status: str | None, *, approved: bool) -> str:
    """The verb a free-text answer becomes, given what the ticket is waiting for.

    `approved` is the caller's reading of the words (see `reads_as_approval`).
    It is consulted ONLY for a finished ticket, because it is the only state
    where "yes" and "no" are both meaningful answers: a stuck worker asked a
    question, and "yes" to a question is an answer, not an acceptance.
    """
    if status == "in_review":
        return ACCEPT if approved else SEND_BACK
    return ANSWER


# The words that mean yes, kept small on purpose. Guessing at prose is how an
# acceptance becomes a rework request, so the real fix is that both surfaces
# OFFER the two choices as buttons (`run_inbox` sets them as options) and this
# set only has to cover the operator typing the obvious word instead of tapping.
_APPROVAL_WORDS = {
    "accept", "accepted", "approve", "approved", "approve it", "lgtm",
    "looks good", "ship it", "yes", "y", "ok", "okay", "good", "done",
}


# The bare refusals. Tapping "Send back" posts the button's own label, and
# recording that label as the rework instruction would leave the next attempt an
# answer with no content — the same noise an acceptance used to leave.
_SEND_BACK_WORDS = {
    "send back", "send-back", "sendback", "reject", "rejected", "decline",
    "declined", "no", "n", "redo", "rework",
}


def _bare(answer: str | None) -> str:
    return str(answer or "").strip().lower().rstrip(".!, ")


def reads_as_approval(answer: str | None) -> bool:
    """Does this answer plainly mean yes? Punctuation-tolerant, nothing clever."""
    return _bare(answer) in _APPROVAL_WORDS


def is_bare_verb(answer: str | None) -> bool:
    """Is the whole answer just the name of the verb, carrying nothing else?

    True for a tapped button. Such an answer belongs in the evidence trail as the
    decision and NOT in the ticket body, where it would read as an instruction.
    """
    text = _bare(answer)
    return bool(text) and (text in _APPROVAL_WORDS or text in _SEND_BACK_WORDS)
