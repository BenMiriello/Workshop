"""The subscription's usage windows, as the CLI reports them on every turn.

Every Claude turn's stream carries a `rate_limit_event`: for each window (the
five-hour session and the seven-day week) how much is used and the epoch
second it resets. That is the only machine-readable source of the reset time,
and the stream parser used to drop it. When a window is actually exhausted the
CLI ALSO says so in prose, as a synthetic assistant message ("You've hit your
session limit · resets 9:40pm (America/Los_Angeles)"), which is kept as the
fallback because the structured event's `rejected` form has not been observed.

What is kept, per owner, in `.runtime/users/<owner>/usage-limits.json`:

    {
      "windows": {"five_hour": {"resets_at": <epoch>, "utilization": 0.3,
                                "status": "allowed"}, ...},
      "blocked_until": <epoch or null>,
      "observed_at": <epoch>
    }

`blocked_until` is the one fact a scheduler acts on: while it is in the
future, a turn on the subscription would only be answered with the limit
message, so the queue waits instead (`turn_admission`).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services import chat_log, ownership

log = logging.getLogger(__name__)

FILE = "usage-limits.json"
# The CLI's own reset time is to the minute; starting a turn the instant it
# passes can still land inside the old window.
RESET_MARGIN_S = 60


def _path():
    return ownership.user_runtime_dir() / FILE


def read() -> dict:
    try:
        data = json.loads(_path().read_text() or "{}")
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    windows = data.get("windows")
    return {"windows": windows if isinstance(windows, dict) else {},
            "blocked_until": data.get("blocked_until"),
            "observed_at": data.get("observed_at")}


def _write(data: dict) -> None:
    chat_log._write_json_atomic(_path(), data)


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def observe_event(info: dict, *, now: float | None = None) -> None:
    """Record one `rate_limit_info` payload."""
    if not isinstance(info, dict):
        return
    now = now or time.time()
    data = read()
    windows = dict(data["windows"])
    unified = info.get("unifiedWindows")
    if isinstance(unified, dict):
        for name, w in unified.items():
            if isinstance(w, dict):
                windows[name] = {"resets_at": _num(w.get("resetsAt")),
                                 "utilization": _num(w.get("utilization")),
                                 "status": w.get("status") or ""}
    kind = info.get("rateLimitType")
    status = info.get("status") or ""
    resets = _num(info.get("resetsAt"))
    if kind:
        entry = dict(windows.get(kind) or {})
        entry.update({"resets_at": resets, "status": status})
        if _num(info.get("utilization")) is not None:
            entry["utilization"] = _num(info.get("utilization"))
        windows[kind] = entry
    blocked = data["blocked_until"]
    if status == "rejected" and resets:
        blocked = max(resets, _num(blocked) or 0)
    elif status.startswith("allowed") and (_num(blocked) or 0) > now:
        # The account answered a turn after we thought it was blocked: whatever
        # we inferred is wrong now (a plan change, extra usage, a new window).
        blocked = None
    _write({"windows": windows, "blocked_until": blocked, "observed_at": now})


_RESETS = re.compile(
    r"hit your [\w -]*limit\s*\S?\s*resets\s+"
    r"(?:(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2}),?\s+(?:at\s+)?)?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)"
    r"(?:\s*\((?P<tz>[^)]+)\))?", re.I)


def parse_reset_text(text: str, *, now: float | None = None) -> float | None:
    """The epoch a "hit your ... limit · resets <when>" line names, or None.

    The text has no year and usually no date, so it means the NEXT such moment
    after now: "resets 1:50am" said at 11pm is tomorrow."""
    m = _RESETS.search(text or "")
    if not m:
        return None
    from services import clock

    try:
        tz = ZoneInfo(m.group("tz").strip()) if m.group("tz") else clock.tzinfo()
    except Exception:
        tz = clock.tzinfo()
    base = datetime.fromtimestamp(now or time.time(), tz)
    hour = int(m.group("h")) % 12 + (12 if m.group("ap").lower() == "pm" else 0)
    minute = int(m.group("m") or 0)
    if m.group("mon"):
        try:
            month = datetime.strptime(m.group("mon"), "%b").month
        except ValueError:
            return None
        at = base.replace(month=month, day=int(m.group("day")), hour=hour,
                          minute=minute, second=0, microsecond=0)
        if at < base - timedelta(days=1):
            at = at.replace(year=at.year + 1)
    else:
        at = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at <= base:
            at += timedelta(days=1)
    return at.timestamp()


def observe_reply(text: str, *, now: float | None = None) -> float | None:
    """Record a limit the CLI announced in prose. Returns its reset epoch."""
    resets = parse_reset_text(text, now=now)
    if resets is None:
        return None
    data = read()
    data["blocked_until"] = max(resets, _num(data["blocked_until"]) or 0)
    data["observed_at"] = now or time.time()
    _write(data)
    return resets


def observe_line(line: str) -> float | None:
    """Feed one raw stream-json line. Returns the reset epoch when this line
    says the limit was HIT, so the caller can arrange to carry on after it."""
    if '"rate_limit_event"' not in line and "hit your" not in line:
        return None
    try:
        ev = json.loads(line)
    except ValueError:
        return None
    if not isinstance(ev, dict):
        return None
    try:
        if ev.get("type") == "rate_limit_event":
            info = ev.get("rate_limit_info") or {}
            observe_event(info)
            if info.get("status") == "rejected":
                return _num(info.get("resetsAt"))
            return None
        msg = ev.get("message") or {}
        if ev.get("type") == "assistant" and msg.get("model") == "<synthetic>":
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    hit = observe_reply(block.get("text") or "")
                    if hit:
                        return hit
    except Exception as e:  # pragma: no cover - never let bookkeeping break a turn
        log.warning("usage limit bookkeeping failed: %s", e)
    return None


def blocked_until(*, now: float | None = None) -> float | None:
    """When the exhausted window resets (with margin), or None if not blocked."""
    b = _num(read()["blocked_until"])
    if b is None:
        return None
    b += RESET_MARGIN_S
    return b if b > (now or time.time()) else None


def next_reset(*, now: float | None = None) -> float | None:
    """The moment "when the usage limit resets" means right now: the blocking
    window's reset while blocked, else the session window's next reset."""
    now = now or time.time()
    b = blocked_until(now=now)
    if b:
        return b
    w = read()["windows"].get("five_hour") or {}
    r = _num(w.get("resets_at"))
    return r + RESET_MARGIN_S if r and r + RESET_MARGIN_S > now else None
