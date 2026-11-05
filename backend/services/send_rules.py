"""When a queued message may go out: the rule the operator attached to it.

A message with no rule is sent as soon as the thread can take it, which is
what the queue has always done. A rule only ever makes a message wait LONGER,
never shorter, and it never interrupts anything: a message whose rule comes
true while its own thread is running goes out when that turn ends.

    {"kind": "after_turn"}                  queue behind the running turn
                                            instead of joining it
    {"kind": "at", "at": "<iso UTC>"}       at or after a time
    {"kind": "usage_reset", "at": ...}      when the subscription's usage
                                            window resets (`at` resolved when
                                            queued, if a reset time is known)
    {"kind": "after_thread", "thread_id"}   when another chat's turn finishes
    {"kind": "resources_free"}              when nothing else holds what this
                                            chat's model runs on (`resources`)
    {"kind": "hold"}                        until the operator sends it

The rule rides on the queue entry (`when`), so it survives what the queue
survives, and every device sees it.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

KINDS = ("after_turn", "at", "usage_reset", "after_thread", "resources_free",
         "hold")


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        return None
    return d.timestamp()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def normalize(when: dict | None, *, thread_id: str | None = None) -> dict | None:
    """The stored form of a rule from a request, or None for "send as usual".
    Raises ValueError with a sentence the operator can act on."""
    if not when:
        return None
    if not isinstance(when, dict):
        raise ValueError("a send rule is an object with a kind")
    kind = when.get("kind")
    if kind not in KINDS:
        raise ValueError(f"unknown send rule {kind!r}")
    if kind == "at":
        at = _epoch(when.get("at"))
        if at is None:
            raise ValueError("a scheduled time needs a date, a time and a timezone")
        return {"kind": "at", "at": _iso(at)}
    if kind == "usage_reset":
        from services import usage_limits
        resets = usage_limits.next_reset()
        return {"kind": "usage_reset", "at": _iso(resets) if resets else None}
    if kind == "after_thread":
        other = str(when.get("thread_id") or "").strip()
        if not other:
            raise ValueError("pick the chat this message waits for")
        if other == thread_id:
            raise ValueError("a chat cannot wait for itself")
        return {"kind": "after_thread", "thread_id": other}
    return {"kind": kind}


def is_ready(entry: dict, *, thread_id: str | None = None,
             now: float | None = None) -> bool:
    """May this entry go out now, as far as its own rule is concerned? The
    thread being free is the drain's question, not this one."""
    when = entry.get("when")
    if not isinstance(when, dict):
        return True
    now = now or time.time()
    kind = when.get("kind")
    if kind == "after_turn":
        return True
    if kind == "hold":
        return False
    if kind == "at":
        at = _epoch(when.get("at"))
        return at is not None and now >= at
    if kind == "usage_reset":
        from services import usage_limits
        at = _epoch(when.get("at"))
        return (at is None or now >= at) and usage_limits.blocked_until(now=now) is None
    if kind == "after_thread":
        from services import chat_store, thread_ops
        other = when.get("thread_id") or ""
        try:
            return not chat_store.active_turn_id(other) and not thread_ops.pending(other)
        except Exception:
            # The chat it waited for is gone; there is nothing left to wait on.
            return True
    if kind == "resources_free":
        from services import resources
        return resources.free_for_thread(thread_id) if thread_id else True
    return True


def needs_poll(entry: dict) -> bool:
    """Whether the resident loop has to keep re-asking about this entry: its
    rule can come true without anything happening to its own thread."""
    when = entry.get("when")
    return isinstance(when, dict) and when.get("kind") not in (None, "after_turn", "hold")
