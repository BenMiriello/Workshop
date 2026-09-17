"""THE PER-OWNER PREFS FILE, and the account-level settings kept in it.

One small JSON document per owner at `<owner runtime>/chat/prefs.json`: the
handful of scalars the settings screen writes and every resolution chain reads.
It has one home and one spelling, because a path spelled twice is a path that
will be spelled wrong in one of the two places.

WHY THIS IS ITS OWN MODULE, DOWN HERE. The file used to be read and written
inside `chat_store`, which was right while every key in it was a chat default.
It stopped being right the moment an ACCOUNT-level setting moved in: the user's
timezone is read by `services/clock`, which the prompt layer and the wake-up
scheduler both sit on top of, and `chat_store` is inside the backend's large
import cycle (`scripts/check_module_layout.py`). A resolver that reached into
`chat_store` for one string would have joined that cycle, and the alternative --
a function-local import -- would only have hidden the edge rather than removed
it.

So the shared thing moved DOWN instead. This module depends on `ownership` and
nothing else, `chat_store` delegates its file access here and keeps its typed
chat defaults, and `clock` can read a preference without importing anything
above it. There is still exactly one prefs store.
"""
from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import available_timezones

from services import ownership

PREFS_FILE = "prefs.json"


def path() -> Path:
    """Where the pinned owner's prefs live. Callers that WRITE want this to
    raise when nobody is pinned, which it does, via `ownership`."""
    return ownership.user_runtime_dir() / "chat" / PREFS_FILE


def read() -> dict:
    """Every preference for the pinned owner, or {} when there are none.

    OwnerUnset is caught alongside the io errors on purpose. Reading a
    preference is a SOFT layer in every resolution chain that consults it, so
    "nobody is pinned" has to mean "no preference", not an exception thrown out
    of the middle of a turn. Writes still fail closed, because `path()` is
    called outside this guard.
    """
    try:
        data = json.loads(path().read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, ownership.OwnerUnset):
        return {}


def write(key: str, value: object) -> None:
    """Set (or, on None, clear) one key. Read-modify-write of a handful of
    scalars written only from the settings screen; there is no concurrent writer
    to lose an update to. Atomic replace, so a reader never sees half a file."""
    prefs = read()
    if value is None:
        prefs.pop(key, None)
    else:
        prefs[key] = value
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(prefs, indent=2))
    tmp.replace(p)


# --- the user's timezone -------------------------------------------------
# An ACCOUNT-level setting rather than a chat one, which is why it lives here
# beside the file rather than among `chat_store`'s chat defaults. What it MEANS
# and what outranks it is `services/clock`; this is only where it is kept.

TIMEZONE_KEY = "timezone"


def get_timezone() -> str | None:
    """The user's own IANA timezone, or None for "not set".

    Not set is a real answer and the common one: `services/clock` then falls
    back to what the installation declares, and finally to UTC labelled as UTC.
    An unspellable stored value reads as not set rather than raising, because
    this is read on the way into every prompt and a hand-edited prefs file must
    not be able to fail a turn.
    """
    v = read().get(TIMEZONE_KEY)
    if not isinstance(v, str):
        return None
    return v.strip() or None


def set_timezone(name: str | None) -> str | None:
    """Persist the user's timezone. Empty/None clears it. Returns what was stored.

    Raises ValueError on a name this host's tz database does not know, because
    unlike a read this is somebody looking at the control: a zone that could not
    be loaded would read back as "not set" and the screen would show UTC next to
    the name they had just picked.
    """
    value = (name or "").strip() or None
    if value is not None and value not in available_timezones():
        raise ValueError(
            f"unknown timezone {name!r}; use an IANA name such as Etc/UTC")
    write(TIMEZONE_KEY, value)
    return value
