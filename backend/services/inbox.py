"""The agent inbox: what an AMBIENT AGENT has to say, and nothing else.

Ambient agents act on an event stream rather than on prompts, so they cannot
reach the operator through a chat transcript — a transcript belongs to one
conversation, and an agent may raise something while the operator is nowhere
near that chat. The inbox is global for exactly that reason, and it filters per
chat rather than being stored per chat (operator, 2026-07-31).

Delivery is scope-bounded by `mentions.may_deliver_to`: an item surfaces in its
own scope's chats and in broader ones, never sideways into an unrelated branch,
so a top-level chat is not pinged by every agent beneath it.

WHAT THIS STORE NO LONGER HOLDS, and why the module shrank (2026-08-22,
docs/platform-redesign/one-fact-one-place-2026-08-22.md §2 and §3.1). It used to
hold a COPY of every waiting ticket and a notification per finished autonomous
run. Measured on the live record the day it was removed: 756 open items, of
which 744 were "a run ended with nobody watching" that nobody has ever actioned,
and every one of the 9 that genuinely wanted the operator was a projection of a
ticket keyed `task:<scope>:<id>`. Because a copy of a ticket's status goes stale
the instant the ticket moves, the whole store had to be rebuilt on EVERY read to
be trusted, which is a query with a cache-invalidation problem bolted to it
rather than a view.

So the ticket is now read as the ticket (`services/chat_work`, "Needs you"),
the run is read on the run and on its ticket, and what is left here is the one
thing that has no entity behind it: an agent with something to say.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services import ownership

log = logging.getLogger(__name__)

# The three verbs an item can carry. Items are NOT typed by entity, which is the
# idea taken from LangChain's Agent Inbox and it is why nothing has to decide
# whether an entry is an agent or a job:
#
#     notify    FYI. The agent acted, or could not, and you should know.
#               Dismissible, and safe to ignore.
#     question  The agent is stuck and is asking rather than guessing.
#               Wants an answer.
#     review    The agent proposes something and wants approve / reject.
#
# `notify` SURVIVES, and here is the decision and its bound (2026-08-22). It was
# up for deletion because 746 of the 756 open items were notifications and 744 of
# those were one dead producer announcing finished runs. That producer is gone.
# The kind stays because the two items left standing after it went are real and
# have no other home: an ambient agent that consolidated some notes, a builder
# that has something to report, neither of which is a ticket and neither of which
# belongs to a conversation.
#
# The bound, which is what stops it regrowing into 744 again: a `notify` is
# raised BY AN AMBIENT AGENT ABOUT ITSELF, never derived from another entity's
# state. If a fact lives on a run, a ticket, a deployment or a chat, it is read
# from there. And a `notify` NEVER reaches a chat transcript: an FYI does not get
# to interrupt a conversation. It is panel-only, it sorts below every question,
# and the inline decision block cannot render one.
KINDS = ("notify", "question", "review")

# Questions and reviews sort above notifications. A notification is something the
# operator may ignore, and letting a wall of them bury one blocked agent is the
# exact failure this surface exists to prevent.
_KIND_RANK = {"question": 0, "review": 1, "notify": 2}

# THE KINDS THAT WANT THE OPERATOR. A `notify` does not: it is an agent saying
# what it did ("Run finished", "Consolidated 4 notes this pass"), and there is
# nothing to answer, approve or unblock. It belongs in a feed, not in a count of
# what is waiting on someone.
#
# This is the line between "here is what happened" and "you are the reason this
# has stopped", and both the dashboard's Needs-you card and the chat's badge
# read it, so the two cannot mean different things by the same word. The chat's
# work PANEL still lists notifications -- they are worth seeing in the place they
# were raised; they just do not count as waiting.
WANTS_YOU_KINDS = ("question", "review")


def wants_you(item: dict) -> bool:
    return item.get("kind") in WANTS_YOU_KINDS

# Terminal states. An item is `open` until the operator deals with it; how it
# closed is kept because "I answered that" and "I dismissed that" are different
# facts, and the second is a signal that the agent is producing noise.
STATES = ("open", "answered", "dismissed")


def _dir() -> Path:
    return ownership.user_runtime_dir() / "inbox"


def _path() -> Path:
    return _dir() / "items.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: Any) -> datetime | None:
    """A timestamp from any writer, as an aware UTC datetime.

    Timestamps reach this module from three writers with three formats (this
    module's second-precision UTC, `services.jobs`' microsecond UTC, and an
    operator-supplied `until`), so they are compared as datetimes rather than as
    strings: `2026-08-12T10:00:00+00:00` and `2026-08-12T10:00:00.5+00:00` do
    not order correctly lexicographically.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load() -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(items: list[dict]) -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    p = _path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, indent=2) + "\n")
    tmp.replace(p)


def raise_item(
    *,
    kind: str,
    agent: str,
    scope: str,
    title: str,
    body: str = "",
    options: list[str] | None = None,
    chat_id: str | None = None,
    ticket_id: str | None = None,
    run_id: str | None = None,
    dedupe_key: str | None = None,
) -> dict:
    """Raise an item. Returns the existing open item when `dedupe_key` matches.

    Dedupe is not an optimization. An agent that runs on a trigger will re-raise
    the same block on every pass until the operator resolves it, and an inbox
    that grows a duplicate every fifteen minutes is one the operator stops
    reading — at which point the whole surface has failed at its only job.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    items = _load()
    if dedupe_key:
        for it in items:
            if it.get("dedupe_key") == dedupe_key and it.get("state") == "open":
                if _refresh_on_reraise(it, title=title, body=body,
                                       options=options or []):
                    _save(items)
                return it
    item = {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "state": "open",
        "agent": agent,
        "scope": scope or "workshop",
        "title": title,
        "body": body,
        "options": options or [],
        "chat_id": chat_id,
        "ticket_id": ticket_id,
        "run_id": run_id,
        "dedupe_key": dedupe_key,
        "created_at": _now(),
        "resolved_at": None,
        "answer": None,
        # Deferred until this instant. See `snooze_item`.
        "snoozed_until": None,
    }
    items.append(item)
    _save(items)
    return item


def _refresh_on_reraise(item: dict, *, title: str, body: str,
                        options: list[str]) -> bool:
    """Bring a deduped item up to date with what the producer just said, and
    wake it if it was snoozed. Returns True when the item changed.

    THE AUTO-UNSNOOZE RULE: a snoozed item wakes early when the thing it is
    about CHANGES. Not when it is merely re-raised.

    That distinction is the whole rule. An ambient agent re-raises on every one
    of its passes, so "any re-raise wakes it" would make snooze a no-op: the row
    would come back within a tick, which is the exact "I dealt with that and it
    came straight back" failure dedupe exists to prevent. A verbatim re-raise is
    the agent saying nothing new; different content is the thing having moved,
    which is worth the operator's attention now.

    Content is also written back, so the row states the current question rather
    than the one first asked, and so the NEXT pass is verbatim again and a
    re-snooze holds.
    """
    changed = False
    for field, value in (("title", title), ("body", body), ("options", options)):
        if item.get(field) != value:
            item[field] = value
            changed = True
    if changed and item.get("snoozed_until"):
        item["snoozed_until"] = None
    return changed


def surfaces_in_chat(item: dict, chat_scope: str, chat_id: str | None) -> bool:
    """Should this item appear at the top of THIS chat?

    Stricter than `may_deliver_to`, and the difference is the operator's own
    caveat: they want an agent to be able to reach a broader chat, but "you
    don't want just any old chat up at the top level getting pings from all
    agents underneath it — that would be annoying." Both halves are true, so
    they are served by different surfaces rather than by one compromise.

    Inline in a chat, an item must be POINTED at it: either it names this chat
    (it came from work started here), or its scope is exactly this chat's scope.
    Everything else stays in the global inbox, where it is still one click away
    and still gets counted. Nothing is hidden; broad chats just are not a
    firehose.
    """
    if not may_reach(chat_scope, item):
        return False
    if item.get("chat_id"):
        return item["chat_id"] == chat_id
    return (item.get("scope") or "workshop") == chat_scope


def may_reach(chat_scope: str, item: dict) -> bool:
    """The scope half of delivery: does this item flow toward this chat at all?"""
    from services import mentions
    return mentions.may_deliver_to(chat_scope, item.get("scope") or "workshop")


def is_snoozed(item: dict, now: datetime | None = None) -> bool:
    """Is this item deferred past `now`? An unparseable stamp is not a snooze:
    a corrupt field must never be able to hide a blocked agent forever."""
    until = _parse_iso(item.get("snoozed_until"))
    if until is None:
        return False
    return until > (now or datetime.now(timezone.utc))


def list_items(
    *,
    chat_scope: str | None = None,
    chat_id: str | None = None,
    state: str = "open",
    limit: int = 100,
    include_snoozed: bool = False,
) -> list[dict]:
    """Open items, optionally narrowed to one chat.

    `chat_scope=None` is the global inbox (every scope). Passing a scope applies
    `surfaces_in_chat`, which is what "global with a per-chat filter" means: one
    store, one truth, and the chat decides how much of it to show — rather than
    per-chat stores that would each hold a different partial picture and drift.

    Ordering is `sort_items`, shared with the chat's work read so a stored notice
    and a ticket-derived row interleave by the same rule.

    SNOOZED ITEMS ARE FILTERED HERE, not in the panel. `counts()` is built by
    calling this function and the chat badge reads `counts()`, so a filter in the
    client would leave the badge counting rows the panel refuses to show, which
    is a badge that can never be cleared.
    """
    items = [i for i in _load() if i.get("state") == state]
    if not include_snoozed:
        now = datetime.now(timezone.utc)
        items = [i for i in items if not is_snoozed(i, now)]
    if chat_scope:
        items = [i for i in items if surfaces_in_chat(i, chat_scope, chat_id)]
    return sort_items(items)[:limit]


def sort_items(items: list[dict]) -> list[dict]:
    """Waiting order: by verb, then oldest first.

    A function rather than a line inside `list_items` because the chat's work
    read merges ticket-derived rows into this listing, and two surfaces sorting
    the same rows differently is how a panel and the row a card links to stop
    agreeing about which question is first.
    """
    return sorted(
        items,
        key=lambda i: (_KIND_RANK.get(i.get("kind"), 3), i.get("created_at") or ""),
    )


def counts(chat_scope: str | None = None, chat_id: str | None = None) -> dict[str, int]:
    """Per-kind open counts, for a badge that says WHAT is waiting, not just how
    much. "3 waiting" and "1 question, 2 notifications" are different messages."""
    out = {k: 0 for k in KINDS}
    for i in list_items(chat_scope=chat_scope, chat_id=chat_id, limit=1000):
        out[i.get("kind", "notify")] = out.get(i.get("kind", "notify"), 0) + 1
    return out


# The presets the panel offers. Anything else arrives as an explicit timestamp.
SNOOZE_PRESETS = ("hour", "tomorrow")


def snooze_until(preset: str, now: datetime | None = None) -> datetime:
    """Resolve a preset to an instant. `tomorrow` is tomorrow MORNING, local:
    "until tomorrow" means the start of the next working day, not this time
    tomorrow, and the operator reads this inbox in his own timezone."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if preset == "hour":
        return now + timedelta(hours=1)
    if preset == "tomorrow":
        local = (now.astimezone() + timedelta(days=1)).replace(
            hour=8, minute=0, second=0, microsecond=0)
        return local.astimezone(timezone.utc)
    raise ValueError(f"preset must be one of {SNOOZE_PRESETS}, got {preset!r}")


def snooze_item(item_id: str, until: datetime | str | None) -> dict | None:
    """Defer an item until `until`, or wake it now when `until` is None.

    Snooze is deferral, never closure: the item keeps its open state and its
    dedupe key, so nothing re-raises a duplicate behind it and whatever it
    describes is still true. It just stops asking for a while.
    """
    stamp: str | None
    if until is None:
        stamp = None
    else:
        dt = until if isinstance(until, datetime) else _parse_iso(until)
        if dt is None:
            raise ValueError(f"until must be an ISO timestamp, got {until!r}")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        stamp = dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    items = _load()
    for it in items:
        if it.get("id") == item_id:
            it["snoozed_until"] = stamp
            _save(items)
            return it
    return None


def resolve_item(item_id: str, *, answer: str | None = None,
                 dismissed: bool = False) -> dict | None:
    """Close an item. `answer` records what the operator said, which is the
    material the memory layer learns preferences from later (plan section 7.3)."""
    items = _load()
    for it in items:
        if it.get("id") == item_id:
            it["state"] = "dismissed" if dismissed else "answered"
            it["answer"] = answer
            it["resolved_at"] = _now()
            _save(items)
            return it
    return None


def get_item(item_id: str) -> dict[str, Any] | None:
    for it in _load():
        if it.get("id") == item_id:
            return it
    return None
