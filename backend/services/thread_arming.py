"""Which threads work unasked, and on what.

THE ONE QUESTION THIS ANSWERS. `armed` is the only field that decides whether a
thread advances without a person typing into it
(`docs/platform-redesign/threads-branches-and-pages-2026-08-29.md` section 1).
`role` names the definition a thread runs under and decides nothing about when
it runs. The two are independent, which is the whole point of them being two
fields, and this module is where that independence is enforced rather than
merely asserted:

  * an armed thread with NO role advances -- it continues on its own context,
    which is what "nudge it along" means, and it is the ordinary case;
  * a thread with a role and `armed = off` never advances, because a definition
    is not a trigger.

This replaces the roster it deletes. Before this, "does anything run unattended"
was answered by walking four markdown files under `agents/` for a `triggers:`
block, which is why the four declared agents had run once between them since
2026-07-18 while 176 background job directories and the resident queue runner
did the actual unattended work. The threads are the rows that exist; this reads
them.

WAKE REASONS, and why they are not the same vocabulary as `armed`. `armed` is a
setting on a thread ("what may advance me"); a wake reason is something that
just happened ("a message landed", "the clock ticked"). One armed value admits
exactly one reason today, but they are different kinds of thing and collapsing
them would make `on-ready-ticket` mean both "I accept ticket wakes" and "a
ticket is ready", which are not the same claim and are asked by different code.
"""
from __future__ import annotations

from services.chat_store import ARMED_MODES, DEFAULT_ARMED, _clamp_armed

# Something that has just happened and could advance a thread.
WAKE_REASONS = ("message", "schedule", "ready-ticket")

# Which armed setting each wake reason satisfies. `off` appears in no row, which
# is what makes it the safe default rather than a value with a hidden meaning.
_ARMED_FOR_REASON: dict[str, str] = {
    "message": "on-message",
    "schedule": "schedule",
    "ready-ticket": "on-ready-ticket",
}


def armed_of(thread: dict | None) -> str:
    """This thread's armed setting, clamped. A thread from before the field
    existed, or one carrying a value the enum no longer admits, reads as `off`:
    an unrecognised value must never make a thread work unasked."""
    return _clamp_armed((thread or {}).get("armed"))


def is_armed(thread: dict | None) -> bool:
    """Does anything at all advance this thread without a person."""
    return armed_of(thread) != DEFAULT_ARMED


def should_advance(thread: dict | None, reason: str) -> bool:
    """Should `reason` advance this thread.

    Reads `armed` and NOTHING ELSE. In particular it does not read `role`: a
    thread with a role and no arming is a thread that has been given a job
    description and no shift, and it stays where it is.
    """
    if reason not in _ARMED_FOR_REASON:
        return False
    return armed_of(thread) == _ARMED_FOR_REASON[reason]


def advancing(threads: list[dict], reason: str) -> list[dict]:
    """The subset of `threads` that `reason` advances, in the order given."""
    return [t for t in threads if should_advance(t, reason)]


__all__ = ["ARMED_MODES", "WAKE_REASONS", "advancing", "armed_of", "is_armed",
           "should_advance"]
