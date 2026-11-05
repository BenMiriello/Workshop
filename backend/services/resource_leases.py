"""The built-in resource coordinator: who holds what, in one file on disk.

A LEASE says a holder is using a named resource ("gpu") right now. It is the
whole of the built-in coordinator, deliberately: it reads no hardware, runs
no daemon and needs no service, so it works the same on any machine and any
accelerator. Anything on the machine may take one over HTTP (`/resources`),
which is how a process outside Workshop says "I am using the card" and how it
asks whether Workshop is.

A lease ends when its holder drops it, when the process it names exits, or
when its time runs out, whichever comes first. So a holder that crashes
cannot hold a resource forever, and one that names its pid needs no renewal.

    .runtime/resource-leases.json   {"<lease id>": {resource, holder, label,
                                                    scope, pid, since,
                                                    expires_at}}

`scope` is whose work it is in Workshop's own terms (a chat's scope), so an
app reading the leases can tell its own chats from everyone else's.

An installation with its own broker replaces this module through
`WORKSHOP_RESOURCE_COORDINATOR` (see `services/resources`).
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager

from wsplatform import paths

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None

DEFAULT_TTL_S = 12 * 3600.0


def store_path():
    return paths.runtime_dir() / "resource-leases.json"


def _alive(pid) -> bool:
    if not pid:
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError, ValueError):
        return True
    return True


def _live(lease: dict, now: float) -> bool:
    return (isinstance(lease, dict) and float(lease.get("expires_at") or 0) > now
            and _alive(lease.get("pid")))


@contextmanager
def _locked(write: bool):
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as f:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            raw = json.loads(f.read() or "{}")
        except ValueError:
            raw = {}
        now = time.time()
        leases = {k: v for k, v in (raw if isinstance(raw, dict) else {}).items()
                  if _live(v, now)}
        yield leases
        if write or len(leases) != len(raw if isinstance(raw, dict) else {}):
            f.seek(0)
            f.truncate()
            f.write(json.dumps(leases))


def holders(resource: str) -> list[dict]:
    """Every live lease on `resource`, oldest first, each with its `id`."""
    with _locked(False) as leases:
        out = [{"id": k, **v} for k, v in leases.items() if v.get("resource") == resource]
    return sorted(out, key=lambda h: h.get("since") or 0)


def take(lease_id: str, resource: str, *, holder: str, label: str = "",
         pid: int | None = None, ttl_s: float | None = None,
         scope: str | None = None) -> dict:
    """Record a lease. Never refuses: deciding who goes first is the caller's
    business (`resources.free_for_thread`), and a holder already using the resource
    is a fact to record, not a request to grant."""
    now = time.time()
    lease = {"resource": resource, "holder": holder, "label": label or holder,
             "scope": scope or None, "pid": int(pid) if pid else None, "since": now,
             "expires_at": now + min(float(ttl_s or DEFAULT_TTL_S), DEFAULT_TTL_S)}
    with _locked(True) as leases:
        if lease_id in leases:
            lease["since"] = leases[lease_id].get("since") or now
        leases[lease_id] = lease
    return {"id": lease_id, **lease}


def drop(lease_id: str) -> bool:
    with _locked(True) as leases:
        return leases.pop(lease_id, None) is not None
