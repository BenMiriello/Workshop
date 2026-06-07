"""Operator-locale time formatting.

Operator rule: ALL times surfaced to operator-facing output (Discord alerts,
UI labels, log lines aimed at humans) are US 12-hour in operator's local time
zone (America/Los_Angeles). Internal storage / state files stay ISO-8601 UTC
so downstream consumers don't have to re-parse locale strings.

Use `local_clock(dt)` for "7:23 AM" style clocks.
Use `local_clock_with_date(dt)` when the date is non-obvious (cross-midnight).
Use `local_relative(secs)` for "in 12 min" / "8 min ago".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

OPERATOR_TZ = ZoneInfo("America/Los_Angeles")


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def local(dt: datetime) -> datetime:
    """Return `dt` converted to operator's tz (assumes naive=UTC)."""
    return _as_aware(dt).astimezone(OPERATOR_TZ)


def local_clock(dt: datetime) -> str:
    """e.g. '7:23 AM' or '11:05 PM' — US 12-hour, no leading zero on hour,
    operator's tz."""
    t = local(dt)
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def local_clock_with_date(dt: datetime) -> str:
    """e.g. 'Wed 7:23 AM' — useful when crossing midnight or for log lines."""
    t = local(dt)
    return f"{t.strftime('%a')} {local_clock(t)}"


def local_iso(dt: datetime) -> str:
    """Operator-local ISO-8601-ish string with offset (for compact log lines).
    e.g. '2026-05-21T07:23:00-07:00'."""
    return local(dt).strftime("%Y-%m-%dT%H:%M:%S%z")


def parse_hhmm_to_local_today(hhmm: str, now: datetime | None = None) -> datetime:
    """'07:10' → today's 07:10 in operator's tz, returned as a tz-aware UTC dt.
    Used to anchor schedule targets against actual wall-clock now."""
    now = now or datetime.now(timezone.utc)
    local_now = local(now)
    h, m = (int(x) for x in hhmm.split(":"))
    target_local = local_now.replace(hour=h, minute=m, second=0, microsecond=0)
    return target_local.astimezone(timezone.utc)


def local_relative(seconds: float) -> str:
    """e.g. 'in 12 min' / '8 min ago' / 'now'. Positive = future, negative = past."""
    s = int(round(seconds))
    if -45 < s < 45:
        return "now"
    sign_future = s > 0
    s = abs(s)
    if s < 3600:
        m = max(1, round(s / 60))
        return f"in {m} min" if sign_future else f"{m} min ago"
    h = s / 3600
    return f"in {h:.1f} h" if sign_future else f"{h:.1f} h ago"


def is_weekday(dt: datetime | None = None) -> bool:
    t = local(dt or datetime.now(timezone.utc))
    return t.weekday() < 5  # 0=Mon..4=Fri


def day_key(dt: datetime | None = None) -> str:
    """'mon'/'tue'/.../'sun' for the operator's local day of `dt`."""
    t = local(dt or datetime.now(timezone.utc))
    return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][t.weekday()]
