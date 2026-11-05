"""Sending a schedule's messages (`chat_schedules` holds the records).

Each send is a queue entry from `schedule:<id>`, numbered by the schedule's
count so a retry is the same message. One waiting is enough: a schedule never
piles up messages behind a busy chat.
"""
from __future__ import annotations

from services import chat_log, chat_queue
from services.chat_schedules import (SENDER_PREFIX, _at, _ended, _iso, _next, _now, _save,
                                     get, read)


class Refused(ValueError):
    """A send the schedule's state does not allow, said plainly."""


def _pending(thread_id: str, sid: str) -> bool:
    return any(e.get("sender") == SENDER_PREFIX + sid for e in chat_queue.read(thread_id))


def _send(thread_id: str, sch: dict, text: str | None = None, *, counts: bool = True) -> dict:
    """Queue its next message. Numbered by every send it has made, so a
    re-armed run never reuses an id the chat already holds. `counts` False is
    a send outside its rule (Send again on an ended one): its run is unchanged."""
    now = _now()
    n = int(sch.get("total_sent", sch.get("sent")) or 0) + 1
    chat_queue.enqueue(thread_id, text=text or sch["text"], client_id=f"{sch['id']}-{n}",
                       ts=_iso(now), sender=SENDER_PREFIX + sch["id"])
    sch.update(total_sent=n, last_sent_at=_iso(now))
    if counts:
        sch["sent"] = int(sch.get("sent") or 0) + 1
        sch["next_at"] = _next(sch, now)
        _ended(sch, now)
    return _save(thread_id, sch)


def _may(sch: dict | None) -> str | None:
    """Why this schedule may not send now, or None."""
    if sch is None or sch.get("status") == "deleted":
        return "no such schedule"
    if sch["status"] == "paused":
        return "paused in the chat"
    if sch["status"] == "done" or _ended(sch, _now()):
        return f"done: {sch.get('why') or 'ended'}"
    start = _at(sch.get("start_at"))
    if start and _now() < start:
        return "not started yet"
    return None


def send(thread_id: str, sid: str, text: str | None = None) -> dict:
    """One send now: an app's own call at its turn end, or the operator's
    Send now. Refused when paused, done or deleted; one waiting is enough."""
    sch = get(thread_id, sid)
    why = _may(sch)
    if why:
        if sch is not None and sch["status"] == "done":
            _save(thread_id, sch)
        raise Refused(why)
    if _pending(thread_id, sid):
        return sch
    return _send(thread_id, sch, text)


def send_again(thread_id: str, sid: str) -> dict:
    """An ended schedule's message, once more, now. It stays ended: this is
    the operator repeating it, not the rule going on."""
    sch = get(thread_id, sid)
    if sch is None or sch.get("status") == "deleted":
        raise Refused("no such schedule")
    if sch["status"] != "done":
        raise Refused("it has not ended: use Send now")
    if sch["driver"] == "app":
        raise Refused("the app writes this schedule's messages")
    if _pending(thread_id, sid):
        return sch
    return _send(thread_id, sch, counts=False)


def history(thread_id: str, sid: str) -> list[dict]:
    """What it has sent, newest first: each message in the chat from this
    schedule, and the one waiting to go out, if any. Read from the transcript
    and the queue, the two places a send lands, so nothing is stored twice."""
    sender = SENDER_PREFIX + sid
    sent = [e for e in chat_log.read_events(thread_id)
            if e.get("kind") == "message" and (e.get("meta") or {}).get("sender") == sender]
    delivered = {e.get("client_id") for e in sent}
    waiting = [{"at": e.get("ts"), "state": "waiting"}
               for e in chat_queue.read(thread_id)
               if e.get("sender") == sender and e.get("client_id") not in delivered]
    return waiting + [{"at": e.get("ts"), "state": "sent"}
                      for e in reversed(sent)]


def on_turn_end(thread_id: str, *, stopped: bool = False) -> int:
    """A turn ended. The operator's Stop pauses every after-reply schedule
    here; otherwise each Workshop after-reply schedule sends its next."""
    sent = 0
    for sch in read(thread_id):
        if sch["status"] != "active" or sch["repeat"] != "after_reply":
            continue
        if stopped:
            sch.update(status="paused", why="you stopped the chat's turn", next_at=None)
            _save(thread_id, sch)
        elif sch["driver"] == "workshop" and not _may(sch) and not _pending(thread_id, sch["id"]):
            _send(thread_id, sch)
            sent += 1
    return sent


def fire_due(thread_ids: list[str]) -> list[str]:
    """Send each Workshop schedule whose time has come; the threads it sent on."""
    out, now = [], _now()
    for tid in thread_ids:
        for sch in read(tid):
            due = _at(sch.get("next_at"))
            if (sch["status"] == "active" and sch["driver"] == "workshop" and due
                    and now >= due and not _may(sch) and not _pending(tid, sch["id"])):
                _send(tid, sch)
                out.append(tid)
    return list(dict.fromkeys(out))
