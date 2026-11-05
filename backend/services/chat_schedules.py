"""Standing rules that send messages into a chat: a schedule.

A queued message with a send rule (`send_rules`) goes out once. A schedule is
the other shape: a message sent again and again, after each reply or every so
often, from a time and until a time or a number of sends, and it is a record
of its own that the operator sees and manages in the chat. Who set it is part
of the record: the operator, or an app (its token's name, a label such as
"Automation run #9", and a link to what set it).

Sending is `chat_schedule_sends`. Every send is an ordinary queue entry (`chat_queue.enqueue`) whose sender is
`schedule:<id>`, so the usage window, resource leases, a Stop's hold and
batching all apply, and the transcript and the model both read it as sent by
the schedule, never as typed by the operator.

Two drivers. Workshop sends the operator's own: at the end of a turn
(`after_reply`) and from the resident loop (`every`, `once`). An app sends its
own (`driver: app`): it decides at each turn end whether its next message goes
and asks for one with `send`, which refuses a schedule that is paused, done or
deleted. So the operator's Pause in the chat is final either way.

    .runtime/users/<owner>/chat/<thread_id>/schedules.json

A deleted schedule is kept, marked deleted, so the messages it sent still say
whose they were.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services import chat_log

FILE = "schedules.json"
REPEATS = ("after_reply", "every", "once")
STATUSES = ("active", "paused", "done", "deleted")
SENDER_PREFIX = "schedule:"
#: The shortest interval an `every` schedule may have.
MIN_EVERY_MIN = 1


def path(thread_id: str) -> Path:
    return chat_log.thread_dir(thread_id) / FILE


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def _at(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else None


def read(thread_id: str, *, deleted: bool = False) -> list[dict]:
    try:
        items = json.loads(path(thread_id).read_text())
    except (OSError, ValueError):
        return []
    items = [s for s in items if isinstance(s, dict) and s.get("id")]
    return items if deleted else [s for s in items if s.get("status") != "deleted"]


def _write(thread_id: str, items: list[dict]) -> None:
    p = path(thread_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1))
    tmp.replace(p)


def get(thread_id: str, sid: str) -> dict | None:
    return next((s for s in read(thread_id, deleted=True) if s["id"] == sid), None)


def _save(thread_id: str, sch: dict) -> dict:
    items = read(thread_id, deleted=True)
    sch["updated_at"] = _iso(_now())
    for i, s in enumerate(items):
        if s["id"] == sch["id"]:
            items[i] = sch
            break
    else:
        items.append(sch)
    _write(thread_id, items)
    return sch


def _shape(fields: dict, base: dict | None = None) -> dict:
    """The rule's fields checked, with a sentence the operator can act on."""
    out = dict(base or {})
    for k in ("text", "label", "repeat", "every_min", "start_at", "until_at", "max_sends"):
        if k in fields:
            out[k] = fields[k]
    out["text"] = str(out.get("text") or "").strip()
    if not out["text"]:
        raise ValueError("a schedule needs a message to send")
    if out.get("repeat") not in REPEATS:
        raise ValueError("repeat is after_reply, every or once")
    if out["repeat"] == "every":
        every = int(out.get("every_min") or 0)
        if every < MIN_EVERY_MIN:
            raise ValueError(f"every needs a number of minutes, at least {MIN_EVERY_MIN}")
        out["every_min"] = every
    else:
        out["every_min"] = None
    for k in ("start_at", "until_at"):
        if out.get(k) and _at(out[k]) is None:
            raise ValueError(f"{k} needs a date, a time and a timezone")
        out[k] = _iso(_at(out[k])) if out.get(k) else None
    if out["repeat"] == "once" and not out.get("start_at"):
        raise ValueError("a schedule that sends once needs a time")
    if out.get("max_sends") in ("", 0):
        out["max_sends"] = None
    if out.get("max_sends") is not None and int(out["max_sends"]) < 1:
        raise ValueError("max sends is at least 1, or none")
    out["label"] = str(out.get("label") or "").strip()[:80] or None
    out["next_at"] = _next(out) if out.get("status", "active") == "active" else None
    return out


def _next(sch: dict, after: datetime | None = None) -> str | None:
    """When an `every` or `once` schedule sends next; None for after_reply.
    With no start, an `every` schedule's first send is one interval away: it
    was made to repeat, not to send at once (10-25, a check's first send
    started a turn the moment it was saved)."""
    start = _at(sch.get("start_at"))
    if sch["repeat"] == "once":
        return sch.get("start_at") if not sch.get("sent") else None
    if sch["repeat"] == "every":
        last = after or _at(sch.get("last_sent_at"))
        step = timedelta(minutes=sch["every_min"])
        nxt = last + step if last else (start or _now() + step)
        return _iso(max(nxt, start) if start else nxt)
    return None


def create(thread_id: str, fields: dict, *, set_by: dict, driver: str = "workshop") -> dict:
    now = _iso(_now())
    sch = {"id": f"sch_{int(_now().timestamp() * 1000)}_{random.randint(0x1000, 0xffff):04x}",
           "status": "active", "why": None, "sent": 0, "total_sent": 0, "last_sent_at": None,
           "set_by": set_by, "driver": "app" if driver == "app" else "workshop",
           "created_at": now}
    return _save(thread_id, _shape(fields, sch))


def change(thread_id: str, sid: str, fields: dict) -> dict:
    """Edit its rule or set its status (active, paused, done or deleted)."""
    sch = get(thread_id, sid)
    if sch is None or sch.get("status") == "deleted":
        raise LookupError("no such schedule")
    status = fields.get("status")
    if status is not None and status not in STATUSES:
        raise ValueError("status is active, paused, done or deleted")
    if status:
        sch["status"], sch["why"] = status, fields.get("why")
    if {"text", "label", "repeat", "every_min", "start_at", "until_at", "max_sends"} & set(fields):
        sch = _shape(fields, sch)
    sch["next_at"] = _next(sch) if sch["status"] == "active" else None
    if sch["status"] == "active":
        _ended(sch, _now())
    return _save(thread_id, sch)


def rearm(thread_id: str, sid: str, fields: dict) -> dict:
    """Start a schedule over with the rule in `fields`: active, nothing sent
    in this run. What it sent before stays in the chat
    (`chat_schedule_sends.history`), and its message numbers carry on
    (`total_sent`), so a new send is never taken for one already delivered."""
    sch = get(thread_id, sid)
    if sch is None or sch.get("status") == "deleted":
        raise LookupError("no such schedule")
    sch.setdefault("total_sent", int(sch.get("sent") or 0))
    sch.update(status="active", why=None, sent=0, last_sent_at=None)
    sch = _shape(fields, sch)
    if _ended(sch, _now()):
        raise ValueError(f"it would end at once: {sch['why']}")
    return _save(thread_id, sch)


def _ended(sch: dict, now: datetime) -> bool:
    """Marks a schedule done when its end has come; True when it has."""
    until = _at(sch.get("until_at"))
    if until and now >= until:
        sch.update(status="done", why="reached its end time", next_at=None)
    elif sch.get("max_sends") and sch.get("sent", 0) >= int(sch["max_sends"]):
        sch.update(status="done", why=f"sent {sch['sent']} of {sch['max_sends']}", next_at=None)
    elif sch["repeat"] == "once" and sch.get("sent"):
        sch.update(status="done", why="sent", next_at=None)
    return sch["status"] == "done"


def threads() -> list[str]:
    """Threads holding a schedule file."""
    return [t for t in chat_log.list_thread_ids() if path(t).exists()]


def of_sender(thread_id: str, sender: str | None) -> dict | None:
    if not sender or not str(sender).startswith(SENDER_PREFIX):
        return None
    return get(thread_id, str(sender)[len(SENDER_PREFIX):])


def heading_name(thread_id: str, sender: str) -> str:
    """Who a schedule's message is from, for the model's heading."""
    sch = of_sender(thread_id, sender)
    if sch is None:
        return sender
    by = sch.get("set_by") or {}
    if by.get("kind") == "app":
        return f"{sch.get('label') or 'a schedule'} (set by {by.get('name') or 'an app'})"
    if by.get("kind") == "chat":
        return f"{sch.get('label') or 'a schedule'} you set for yourself in this chat"
    return f"{sch.get('label') or 'a schedule'} the operator set"


def for_self(thread_id: str, reason: str, first_at: datetime, every_min: float,
             until_at: datetime | None = None, times: int | None = None) -> dict:
    """A chat's own repeating wake-up (`wake_me` with every_minutes): a
    schedule on its thread, set by the chat, first sent at `first_at`."""
    label = " ".join(str(reason).split())[:60]
    return create(thread_id, {"text": reason, "label": label, "repeat": "every",
                              "every_min": int(round(every_min)), "start_at": _iso(first_at),
                              "until_at": _iso(until_at) if until_at else None,
                              "max_sends": times}, set_by={"kind": "chat"})
