"""A chat's own alarm clock: wake this thread later, carrying a reason.

WHAT WAS MISSING. An assistant in a chat could start work that outlives the turn
(`spawn_background`) and could watch one thing while the turn ran (Bash in the
background, the Monitor tool), but it had no way to be woken at a TIME. Nothing
it could call from inside a turn survived the turn: the turn's process group is
retired the moment the turn ends (`build_streaming._retire_turn_proc`), so an
in-session scheduler loses every job it holds at exactly the moment the job was
supposed to start mattering. "I'll check back in ten minutes" was a sentence the
platform could not make true, and a narrow data chat could not even background a
command.

THIS IS NOT A SECOND SCHEDULER. It is a due time on the wake path a chat already
has. `services/chat_drain` holds the only mechanism that can start a turn on a
thread with nobody watching: a continuation seed on disk, plus a resident loop in
the long-lived backend process that notices the seed and starts the turn once the
thread is idle. A finished background run already uses it. A wake-up is the same
seed, written later — so all this module owns is "what is due, and when", and the
firing, the idle checks, the restart survival and the turn itself are the
existing machinery, unchanged.

WHY IT SURVIVES. The armed record is a file in the thread's own directory,
written by the MCP server child that the turn's retirement kills. Killing that
process cannot unwrite the file, and nothing else is holding the alarm: the
backend that fires it is a different process with a different lifetime, and it
re-reads the store on every pass and again at startup. So a wake-up survives the
turn ending, the turn being stopped, and the backend being restarted in between.
An overdue one fires late rather than not at all, which is the correct failure
for an alarm.

    .runtime/users/<owner>/chat/<thread_id>/wakeups.json

beside `queue.json` (messages the operator sent) and `continuation.json` (the one
pending wake reason). Three files, three different things, deliberately not
merged: the queue is rendered to the operator as their own words, the
continuation holds at most one reason and is consumed immediately, and this holds
many, each with a future time, and is consumed only when its time comes.

Record shape:

    {
      "id":       "wake_<epoch_ms>_<rand>",
      "due_at":   "<iso8601, UTC>",
      "reason":   "what to do when it fires",
      "armed_at": "<iso8601, UTC>"
    }
"""
from __future__ import annotations

import json
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services import chat_log

log = logging.getLogger(__name__)

WAKEUPS_FILE = "wakeups.json"

# `HH:MM` on a 24-hour clock: the shorthand a person and a model both reach for
# when they mean "later today, or tomorrow if that time is behind us".
_CLOCK_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def path(thread_id: str) -> Path:
    return chat_log.thread_dir(thread_id) / WAKEUPS_FILE


def _new_id() -> str:
    return (f"wake_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
            f"_{random.randint(0x1000, 0xffff):04x}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def read(thread_id: str) -> list[dict]:
    """Everything armed on this thread, soonest first.

    Never raises. A store that cannot be parsed would otherwise block every
    future wake-up on this thread, and the records are already lost by then.
    """
    p = path(thread_id)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text() or "{}")
    except Exception as e:  # noqa: BLE001
        log.warning("wakeups for %s are unreadable (%s); starting empty", thread_id, e)
        return []
    items = data.get("wakeups") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    clean = [w for w in items
             if isinstance(w, dict) and w.get("id") and w.get("due_at")]
    return sorted(clean, key=lambda w: str(w.get("due_at")))


def _write(thread_id: str, items: list[dict]) -> None:
    p = path(thread_id)
    if not items:
        # Nothing armed. Removing the file keeps "does this thread have a
        # wake-up" answerable by a stat rather than a parse, which is what the
        # firing pass asks of every thread on every tick.
        try:
            p.unlink(missing_ok=True)
        except OSError as e:  # pragma: no cover - defensive
            log.debug("could not remove the wakeup store for %s: %s", thread_id, e)
        return
    chat_log.thread_dir(thread_id).mkdir(parents=True, exist_ok=True)
    chat_log._write_json_atomic(p, {"wakeups": items})


def arm(thread_id: str, *, reason: str, due_at: datetime) -> dict:
    """Arm one wake-up and return its record.

    `due_at` is stored in UTC whatever it arrives in, so a comparison never
    depends on the reader's zone, and rendered back in the local zone
    `services/clock` resolves -- the user's own setting where they made one,
    else the installation's -- wherever a person or a model reads it.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("a wake-up needs a reason: it is what the later turn is given")
    if due_at.tzinfo is None:
        raise ValueError("a wake-up time must carry a timezone")
    record = {
        "id": _new_id(),
        "due_at": due_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "reason": reason,
        "armed_at": _now().isoformat(timespec="seconds"),
    }
    items = read(thread_id)
    items.append(record)
    _write(thread_id, items)
    return record


def cancel(thread_id: str, wake_id: str) -> dict | None:
    """Take one back. Returns the cancelled record, or None if it was not there
    (already fired, already cancelled, never existed) — the three cases are the
    same answer to the caller: it is not armed now."""
    items = read(thread_id)
    hit = next((w for w in items if w.get("id") == wake_id), None)
    if hit is None:
        return None
    _write(thread_id, [w for w in items if w.get("id") != wake_id])
    return hit


def drop(thread_id: str, ids: set[str] | list[str]) -> None:
    ids = set(ids)
    if not ids:
        return
    _write(thread_id, [w for w in read(thread_id) if w.get("id") not in ids])


def forget(thread_id: str) -> None:
    """Everything this thread has armed, for a thread being deleted. An alarm
    for a conversation that no longer exists can only ever fire into nothing."""
    _write(thread_id, [])


def due(thread_id: str, *, now: datetime | None = None) -> list[dict]:
    """The armed wake-ups whose time has come, soonest first."""
    cutoff = (now or _now()).astimezone(timezone.utc).isoformat(timespec="seconds")
    return [w for w in read(thread_id) if str(w.get("due_at")) <= cutoff]


def threads_with_wakeups() -> list[str]:
    """Every thread holding an armed wake-up, for the owner pinned on this call."""
    out: list[str] = []
    for tid in chat_log.list_thread_ids():
        try:
            if path(tid).exists():
                out.append(tid)
        except Exception:  # pragma: no cover - defensive
            continue
    return out


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

def resolve_when(*, in_minutes: float | int | None = None, at: str | None = None,
                 now: datetime | None = None) -> datetime:
    """Turn what a caller asked for into an absolute instant.

    Exactly one of the two forms. `in_minutes` is relative and needs no zone at
    all. `at` is absolute and is read in the LOCAL zone in force unless it says
    otherwise, because a bare "16:30" from an assistant means the user's
    afternoon, never UTC's. That zone is whatever `services/clock` resolves, so
    a user who set their own timezone gets alarms on their clock rather than on
    whichever one the host happens to be set to.

    Raises ValueError with a sentence the model can act on. A wake-up that
    silently lands at the wrong hour is worse than one that is refused.
    """
    from services import clock

    tz = clock.tzinfo()
    now = (now or _now()).astimezone(tz)
    has_rel = in_minutes is not None
    has_abs = bool((at or "").strip())
    if has_rel and has_abs:
        raise ValueError("give a delay or a time, not both")
    if not has_rel and not has_abs:
        raise ValueError("say when: a delay in minutes, or a time to wake at")
    if has_rel:
        try:
            mins = float(in_minutes)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError("the delay must be a number of minutes") from None
        if mins <= 0:
            raise ValueError("a wake-up has to be in the future; the delay must be above zero")
        return now + timedelta(minutes=mins)

    raw = (at or "").strip()
    clock = _CLOCK_RE.match(raw)
    if clock:
        # A bare time of day: the NEXT time the clock reads that, so "07:00"
        # asked at midnight is this morning and asked at noon is tomorrow.
        want = now.replace(hour=int(clock.group(1)), minute=int(clock.group(2)),
                           second=0, microsecond=0)
        return want if want > now else want + timedelta(days=1)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            "that time could not be read. Use a delay in minutes, a 24-hour "
            "clock time like 16:30, or a full date and time like "
            "2026-01-31T09:15"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    if parsed <= now:
        raise ValueError("that time has already passed; a wake-up has to be in the future")
    return parsed


def render(record: dict) -> str:
    """One armed wake-up, as a line for whoever asked what is armed."""
    from services import clock

    try:
        when = datetime.fromisoformat(str(record.get("due_at"))).astimezone(
            clock.tzinfo())
        stamp = when.strftime("%Y-%m-%d %H:%M %Z").strip()
    except ValueError:  # pragma: no cover - defensive
        stamp = str(record.get("due_at"))
    return f"- {record.get('id')}: {stamp} — {record.get('reason')}"


def seed_text(record: dict) -> str:
    """The prompt that starts the turn a wake-up fires.

    Written to the MODEL and framed so it cannot be mistaken for the operator
    speaking, the same way a finished run's continuation seed is: nobody has said
    anything, a clock went off, and the reason is the assistant's own words from
    earlier. It is deliberately not a greeting — a wake-up that opens with "hi,
    you asked me to check in" spends a turn saying so and does nothing.
    """
    return (
        "[SCHEDULED WAKE-UP - not a message from the operator]\n"
        "A wake-up you armed earlier in this conversation has just come due. "
        "You wrote its reason yourself:\n\n"
        f"{record.get('reason')}\n\n"
        "Do that now. Nobody is necessarily watching, so act rather than ask: "
        "carry out what the reason says, then tell the operator the outcome in "
        "one or two lines. Do not greet them and do not ask what they want. If "
        "the reason no longer applies, say so briefly and stop. If it needs "
        "another wake-up afterwards, arm one."
    )


# ---------------------------------------------------------------------------
# Firing — the seam onto the existing wake path
# ---------------------------------------------------------------------------

def fire_due(*, now: datetime | None = None) -> int:
    """Turn every due wake-up into a continuation seed. Returns how many fired.

    Called from `chat_drain`'s resident loop and from its startup pass, for the
    owner pinned on this call. The seed, not the turn: `chat_drain` already owns
    when a thread is idle enough to start one, and a wake-up must obey exactly
    the same rules as every other wake reason rather than a second copy of them.

    ONE AT A TIME PER THREAD, because a thread holds at most one pending
    continuation. A second due wake-up simply waits for the next pass, by which
    time the first has been consumed — nothing is dropped and nothing overwrites
    a seed some other producer is holding.

    The seed is written BEFORE the record is dropped. Crashing in between leaves
    the wake-up armed and it fires again, where `write_continuation` recognises
    the id and refuses to double-fire. The other order loses the alarm.
    """
    from services import chat_drain, chat_queue

    fired = 0
    for tid in threads_with_wakeups():
        try:
            ready = due(tid, now=now)
            if not ready:
                continue
            if chat_queue.read_continuation(tid) is not None:
                # Something else armed this thread and has not been consumed
                # yet. Leave ours where it is rather than clobbering theirs.
                continue
            record = ready[0]
            chat_drain.request_continuation(
                tid, seed=seed_text(record), dedupe_id=str(record["id"]))
            drop(tid, [record["id"]])
            fired += 1
            log.info("wake-up %s fired on %s", record["id"], tid)
        except Exception as e:  # pragma: no cover - one bad thread is not the pass
            log.warning("wake-up pass failed for %s: %s", tid, e)
    return fired
