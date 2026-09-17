"""WHICH CLOCK a time is expressed in, resolved in one place.

UTC IS CANONICAL AND IS NOT A PREFERENCE. Every timestamp this system stores is
UTC -- `timestamptz` in Postgres, ISO-8601 with an offset on disk -- and every
reading the platform hands a model states the UTC time. A local zone is a
presentation choice layered on top of that instant, never the storage format,
and nothing in here changes what is written.

WHERE THE LOCAL ZONE COMES FROM, highest rung first:

1. THE USER'S OWN SETTING (`user_prefs.get_timezone`), one field in the
   per-owner prefs file the settings screen already writes. This rung exists
   because a machine's zone is not a person's zone: a host in a data centre, a
   laptop that travelled, and a container built at UTC all report a clock its
   user does not live in, and until this rung existed there was no way to say so.
2. THE INSTALLATION'S DECLARATION (`installation.timezone_name()`, which reads
   WORKSHOP_TIMEZONE, else TZ, else the host's own zone file). Right for a
   personal installation running on a machine already set to its owner's time,
   which is why it stays the default rather than being demoted.
3. UTC, LABELLED AS UTC. An installation that declares nothing and a user who
   sets nothing get the canonical clock and are told that is what they are
   looking at. A guessed zone would be worse: a reading in the wrong zone that
   claims to be local is indistinguishable from a correct one.

A rung whose name this host's tz database cannot load is SKIPPED, not repaired.
A name that will not load cannot be converted through and must not be quoted at
a model as though it had been, so a junk WORKSHOP_TIMEZONE resolves to UTC and
says UTC.

THE BROWSER IS NOT A RUNG. A zone read from `Intl.DateTimeFormat` is a
suggestion the settings screen offers and the user accepts by hand; accepting it
is what writes rung 1. Adopting it automatically would mean the zone changes
whenever the user opens a different browser or travels, and then every armed
wake-up and every reported hour moves with it. A clock that follows the last
device to connect is worse than one that is merely wrong, because there is
nothing stable in it to correct.
"""
from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

from services import installation, user_prefs

UTC = _dt.timezone.utc

#: What `resolve()` reports as the rung that answered.
SOURCES = ("user", "installation", "utc")


def _loadable(name: str):
    """A tzinfo for `name`, or None if this host's tz database has never heard
    of it. Never raises: an unresolvable zone is a rung to skip, not an error to
    propagate into a prompt or a wake-up."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - any failure to load means "skip this rung"
        return None


def user_zone() -> str | None:
    """The user's own setting, or None when they have not made one."""
    return user_prefs.get_timezone()


def resolve() -> tuple[str, str, _dt.tzinfo]:
    """The zone in force, as (IANA name, which rung answered, tzinfo).

    The name is "UTC" and the rung is "utc" when nothing above it answered, so a
    caller never has to decide what to do about an empty zone.
    """
    name = user_zone() or ""
    tz = _loadable(name)
    if tz is not None:
        return name, "user", tz
    name = installation.timezone_name()
    tz = _loadable(name)
    if tz is not None:
        return name, "installation", tz
    return "UTC", "utc", UTC


def timezone_name() -> str:
    """The IANA name of the zone in force. Always non-empty."""
    return resolve()[0]


def source() -> str:
    """Which rung answered: "user", "installation" or "utc"."""
    return resolve()[1]


def tzinfo() -> _dt.tzinfo:
    """The tzinfo of the zone in force. Always usable."""
    return resolve()[2]


def utc_now() -> _dt.datetime:
    """Right now, in the canonical clock. This is the reading everything else
    here is derived from, so the two readings a caller prints are one instant."""
    return _dt.datetime.now(tz=UTC)


def now() -> _dt.datetime:
    """Right now, in the zone in force."""
    return utc_now().astimezone(tzinfo())


def offset_label(at: _dt.datetime | None = None) -> str:
    """"UTC-07:00" for a moment in the zone in force -- the part of a reading
    that stays true when the zone name means nothing to the reader."""
    moment = (at or now()).astimezone(tzinfo())
    raw = moment.strftime("%z")
    return f"UTC{raw[:3]}:{raw[3:]}" if raw else "an unknown offset"
