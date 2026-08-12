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
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services import ownership

log = logging.getLogger(__name__)

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

    That distinction is the whole rule. `run_inbox.sync` re-raises every blocked
    task on every inbox read, roughly every twenty seconds, so "any re-raise
    wakes it" would make snooze a no-op for the only live producer: the row
    would come back on the next poll, which is the exact "I dealt with that and
    it came straight back" failure dedupe exists to prevent. A verbatim re-raise
    is the reconcile pass saying nothing new; different content is the entity
    having moved, which is worth the operator's attention now.

    Content is also written back, so the row states the current question rather
    than the one first asked, and so the NEXT reconcile pass is verbatim again
    and a re-snooze holds.
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

    Questions and reviews sort above notifications: a notification is something
    the operator may ignore, and letting a wall of them bury one blocked agent is
    the exact failure this surface exists to prevent.

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
    dedupe key, so nothing re-raises a duplicate behind it and the block it
    describes is still a block. It just stops asking for a while.

    An item whose entity moves on while it sleeps is not left to surprise the
    operator later: the reconcilers run on every inbox read, they see the woken
    item on the first read after it wakes, and they close it there if its work is
    done before the row ever renders.
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


# ---------------------------------------------------------------------------
# Producer: an autonomous run that ended with nobody watching
# ---------------------------------------------------------------------------

# `services.jobs` records how a run STARTED, and that is what decides whether
# anyone was watching it end. `manual` is the operator pressing run: he is
# looking at it, and telling him about it is noise. The other three are
# self-starting, which is the same mapping the UI's agency axis uses
# (`agencyFor`: cron/event/loop are the autonomous tier).
AUTONOMOUS_TRIGGERS = ("cron", "event", "loop")

_TERMINAL_VERB = {"done": "finished", "failed": "failed", "cancelled": "was cancelled"}


def _run_sweep_path() -> Path:
    return _dir() / "run_sweep.json"


def _run_watermark() -> datetime | None:
    try:
        data = json.loads(_run_sweep_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return _parse_iso((data or {}).get("since")) if isinstance(data, dict) else None


def _set_run_watermark(when: datetime) -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    _run_sweep_path().write_text(
        json.dumps({"since": when.astimezone(timezone.utc).isoformat()}) + "\n")


def _worth_reporting(job: dict) -> bool:
    """Did this run END IN SOMETHING? A sweep tick that found no work and exited
    clean is not an event; it is the system idling.

    This box's own log is the argument: of 2336 terminal autonomous runs, 1880
    finished having touched nothing. Raising a notification for each would put
    1880 rows in front of one blocked agent, which is the failure `list_items`
    already sorts against. A run that FAILED always reports (something broke with
    nobody watching, which is the whole point of the producer), and a run that
    finished reports when it moved at least one ticket.
    """
    if job.get("status") != "done":
        return True
    return bool(job.get("tasks_done") or job.get("tasks_failed"))


def _run_scope(job: dict) -> str:
    slug = str(job.get("project_slug") or "").strip()
    return f"project:{slug}" if slug else "workshop"


def _run_title(job: dict) -> str:
    verb = _TERMINAL_VERB.get(str(job.get("status")), str(job.get("status") or "ended"))
    where = str(job.get("project_slug") or "").strip()
    return f"Run {verb} in {where}" if where else f"Run {verb}"


def _run_body(job: dict) -> str:
    lines = []
    done, failed = job.get("tasks_done") or 0, job.get("tasks_failed") or 0
    if done or failed:
        lines.append(f"{done} ticket(s) done, {failed} failed.")
    ev = job.get("evidence")
    if isinstance(ev, dict):
        for key in ("halted", "reviewer", "gate", "summary"):
            val = ev.get(key)
            if val:
                lines.append(str(val).strip().splitlines()[0][:200])
                break
    elif isinstance(ev, str) and ev.strip():
        lines.append(ev.strip().splitlines()[0][:200])
    lines.append(f"Started by {job.get('trigger') or 'a trigger'}, nobody watching.")
    return "\n".join(lines)


def sweep_finished_runs(now: datetime | None = None) -> dict:
    """Raise a `notify` for every autonomous run that reached a terminal state
    since the last pass. Returns a small tally for logging.

    WHY A SWEEP AND NOT A HOOK IN `jobs.finish`. The same reason `run_inbox`
    reconciles on read: runs are closed by shell workers through the stdlib-only
    `services.jobs` CLI shim, in another process, against a per-owner inbox store
    that process has nobody pinned to. Reading the append-only job log is
    idempotent, works whichever process wrote the terminal line, and self-heals
    if a pass is ever missed.

    THE WATERMARK IS SET, NOT REPLAYED, ON THE FIRST PASS. This log holds months
    of history; importing it wholesale would bury the surface at the moment it
    starts working. The first pass records where it started and reports nothing.

    Never raises into the caller: the inbox listing must not fail because the
    job log is unreadable.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since = _run_watermark()
    if since is None:
        _set_run_watermark(now)
        return {"raised": 0, "watermark": "initialized"}

    raised = 0
    try:
        from services import jobs
        records = jobs.read(limit=1000)
    except Exception as e:
        log.warning("inbox: cannot read the job log: %s", e)
        return {"raised": 0, "watermark": "held"}

    high = since
    for job in records:
        if job.get("trigger") not in AUTONOMOUS_TRIGGERS:
            continue
        if job.get("status") not in ("done", "failed", "cancelled"):
            continue
        ended = _parse_iso(job.get("ended_at"))
        if ended is None or ended <= since or ended > now:
            continue
        if ended > high:
            high = ended
        if not _worth_reporting(job):
            continue
        try:
            raise_item(
                kind="notify",
                agent=str(job.get("agent_id") or "").strip() or "autonomous run",
                scope=_run_scope(job),
                title=_run_title(job),
                body=_run_body(job),
                ticket_id=str(job.get("task_id") or "") or None,
                run_id=str(job.get("id") or "") or None,
                dedupe_key=f"run:{job.get('id')}",
            )
            raised += 1
        except Exception as e:
            log.warning("inbox: cannot raise for run %s: %s", job.get("id"), e)

    # Only when it moved: this runs on every inbox read, and an idle box would
    # otherwise rewrite the same stamp every twenty seconds forever.
    if high > since:
        _set_run_watermark(high)
    return {"raised": raised, "watermark": high.isoformat()}


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
