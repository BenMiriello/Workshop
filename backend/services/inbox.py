"""The agent inbox: things that need the operator, typed by what they need.

Prior art is LangChain's Agent Inbox, and the load-bearing idea taken from it is
that items are NOT typed by entity. A finished run, a blocked agent and a ticket
awaiting approval are not three kinds of thing to be shown in three places —
they are one kind of thing (something wants you) with three different verbs:

    notify    FYI. The agent acted, or could not, and you should know.
              Dismissible, and safe to ignore.
    question  The agent is stuck and is asking rather than guessing.
              Wants an answer.
    review    The agent proposes something and wants approve / reject.

This is what dissolves the "do we mix agents and jobs in one stack?" problem:
nothing has to decide whether an entry is an agent or a job, because the entry
is neither. It is a request for attention with a verb on it.

Ambient agents act on an event stream rather than on prompts, so they cannot
reach the operator through a chat transcript — a transcript belongs to one
conversation, and an agent may raise something while the operator is nowhere
near that chat. The inbox is global for exactly that reason, and it filters per
chat rather than being stored per chat (operator, 2026-07-31).

Delivery is scope-bounded by `mentions.may_deliver_to`: an item surfaces in its
own scope's chats and in broader ones, never sideways into an unrelated branch,
so a top-level chat is not pinged by every agent beneath it.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services import ownership

KINDS = ("notify", "question", "review")

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
    }
    items.append(item)
    _save(items)
    return item


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


def list_items(
    *,
    chat_scope: str | None = None,
    chat_id: str | None = None,
    state: str = "open",
    limit: int = 100,
) -> list[dict]:
    """Open items, optionally narrowed to one chat.

    `chat_scope=None` is the global inbox (every scope). Passing a scope applies
    `surfaces_in_chat`, which is what "global with a per-chat filter" means: one
    store, one truth, and the chat decides how much of it to show — rather than
    per-chat stores that would each hold a different partial picture and drift.

    Questions and reviews sort above notifications: a notification is something
    the operator may ignore, and letting a wall of them bury one blocked agent is
    the exact failure this surface exists to prevent.
    """
    items = [i for i in _load() if i.get("state") == state]
    if chat_scope:
        items = [i for i in items if surfaces_in_chat(i, chat_scope, chat_id)]
    rank = {"question": 0, "review": 1, "notify": 2}
    items.sort(key=lambda i: (rank.get(i.get("kind"), 3), i.get("created_at") or ""))
    return items[:limit]


def counts(chat_scope: str | None = None, chat_id: str | None = None) -> dict[str, int]:
    """Per-kind open counts, for a badge that says WHAT is waiting, not just how
    much. "3 waiting" and "1 question, 2 notifications" are different messages."""
    out = {k: 0 for k in KINDS}
    for i in list_items(chat_scope=chat_scope, chat_id=chat_id, limit=1000):
        out[i.get("kind", "notify")] = out.get(i.get("kind", "notify"), 0) + 1
    return out


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
