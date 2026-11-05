"""Is the process a record names still the one that wrote it?

`os.kill(pid, 0)` answers "does some process hold this number", and pids are
recycled. Records that outlive their process (Jobs, ticket claims) outlive it
across a reboot too, and a freshly booted host hands out low pids again, so a
bare signal probe can make a long-dead run read as alive indefinitely.

Three facts settle it, strongest first:

  boot_id      `/proc/sys/kernel/random/boot_id`, recorded at launch. A record
               from another boot names a process that cannot exist any more.
  pid_start    field 22 of `/proc/<pid>/stat` (clock ticks since boot),
               recorded at launch. Two processes never share a pid AND a start
               time, so a mismatch is an exact recycle check.
  recorded_at  a wall-clock time at which the process was known to exist, for
               records that predate the two fields above. A host that booted
               after it means a reboot; a process at that pid that started well
               after it is a recycle.

Pure stdlib and Linux-only by nature; on a host without /proc every check
degrades to the signal probe, which is what callers had before.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache

ALIVE = "alive"
DEAD = "dead"
REBOOTED = "rebooted"
UNKNOWN = "unknown"

# A launcher may record a pid moments after it opens the record (it needs the
# record's id on the child's command line), so a process may legitimately start
# a little after `recorded_at`. A recycled pid starts much later than that.
REUSE_SLACK_S = 300.0

_BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
_STAT_PATH = "/proc/stat"


@lru_cache(maxsize=1)
def current_boot_id() -> str | None:
    try:
        with open(_BOOT_ID_PATH, encoding="ascii") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


@lru_cache(maxsize=1)
def boot_time() -> float | None:
    """Epoch seconds the host booted (`btime` in /proc/stat)."""
    try:
        with open(_STAT_PATH, encoding="ascii") as fh:
            for line in fh:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _stat_fields(pid: int) -> list[bytes] | None:
    """`/proc/<pid>/stat` from field 3 (state) onward. Readable regardless of
    which uid owns the target, unlike a signal."""
    try:
        with open(f"/proc/{int(pid)}/stat", "rb") as fh:
            raw = fh.read()
    except (OSError, ValueError):
        return None
    # comm (field 2) is parenthesised and may contain ')' or spaces.
    parts = raw.rsplit(b")", 1)
    if len(parts) != 2:
        return None
    return parts[1].split()


def read_pid_start(pid: int) -> str | None:
    """Field 22 of `/proc/<pid>/stat` (start time in ticks since boot), verbatim."""
    fields = _stat_fields(pid)
    # fields[0] is field 3, so field 22 is index 19.
    if not fields or len(fields) <= 19:
        return None
    return fields[19].decode()


def is_zombie(pid: int) -> bool:
    """Exited but not yet reaped. `os.kill(pid, 0)` succeeds on one, and a run
    the backend spawned and never waited on becomes one the moment it dies."""
    fields = _stat_fields(pid)
    return bool(fields) and fields[0] in (b"Z", b"X")


def started_at(pid: int) -> float | None:
    """Epoch seconds this pid's current process started, or None."""
    ticks = read_pid_start(pid)
    bt = boot_time()
    if ticks is None or bt is None:
        return None
    try:
        hz = os.sysconf("SC_CLK_TCK")
        return bt + int(ticks) / hz
    except (ValueError, OSError, ZeroDivisionError):
        return None


def identity(pid: int | None = None) -> dict:
    """What to record beside a pid at launch: `boot_id` always, `pid_start` when
    the pid is live and readable. Keys with no value are omitted."""
    out: dict = {}
    bid = current_boot_id()
    if bid:
        out["boot_id"] = bid
    if pid is not None:
        st = read_pid_start(pid)
        if st is not None:
            out["pid_start"] = st
    return out


def to_epoch(value) -> float | None:
    """An ISO timestamp, datetime or number as epoch seconds, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _signal_probe(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another uid
    except OSError:
        return False
    return True


def check(pid, *, boot_id: str | None = None, pid_start: str | None = None,
          recorded_at=None) -> str:
    """ALIVE, DEAD, REBOOTED, or UNKNOWN (no pid and no reboot evidence).

    REBOOTED is reported whenever the record provably predates the current boot,
    whether or not it carries a pid: nothing launched before a reboot survives
    it. Callers that do not care why can treat it as DEAD."""
    current_boot = current_boot_id()
    if boot_id and current_boot:
        if boot_id != current_boot:
            return REBOOTED
    else:
        since = to_epoch(recorded_at)
        booted = boot_time()
        if since is not None and booted is not None and booted > since:
            return REBOOTED

    if pid is None:
        return UNKNOWN
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return DEAD
    if pid <= 0 or not _signal_probe(pid) or is_zombie(pid):
        return DEAD

    if pid_start is not None:
        return ALIVE if read_pid_start(pid) == str(pid_start) else DEAD
    since = to_epoch(recorded_at)
    if since is not None:
        began = started_at(pid)
        if began is not None and began > since + REUSE_SLACK_S:
            return DEAD
    return ALIVE
