"""Who may use a GPU right now: job claims and guest claims.

A JOB claim is work that has the card by right (a render queue, a training
run). A GUEST claim is work that may borrow the card only while no job wants
it, and must let go the moment one does, finishing on the processor instead.

A guest is also refused whenever the installation's resource coordinator
(`services/resources`) names a holder for the card through an optional
`guest_refused(device)`: something outside Workshop may need its headroom. The
built-in coordinator names none.

Jobs are never refused here. Ordering one job against another is the business
of whatever runs them; this service only answers the question a guest has to
ask before every piece of work: is any job claim live on this device?

Claims expire unless renewed, so a client that crashes or forgets to release
cannot hold a device forever. They live in one small file under the runtime
dir, read and written under an exclusive lock, so every backend process on the
machine sees the same claims and a restart loses none.
"""
from __future__ import annotations

import fcntl
import json
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from wsplatform import paths

KINDS = ("job", "guest")
MAX_TTL_S = 300.0


@dataclass
class Claim:
    name: str
    kind: str
    device: int
    label: str
    expires_at: float


def store_path():
    return paths.runtime_dir() / "gpu-claims.json"


@contextmanager
def _locked(write: bool):
    """The live claims, expired ones dropped; written back if `write`."""
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            raw = json.loads(f.read() or "{}")
        except ValueError:
            raw = {}
        now = time.time()
        claims = {n: Claim(**c) for n, c in raw.items() if c.get("expires_at", 0) > now}
        yield claims
        if write:
            f.seek(0)
            f.truncate()
            f.write(json.dumps({n: asdict(c) for n, c in claims.items()}))
            f.flush()


def _named(claims: dict[str, Claim], kind: str, device: int, besides: str) -> list[str]:
    return [c.name for c in claims.values()
            if c.kind == kind and c.device == device and c.name != besides]


def _outside_holder(device: int) -> str | None:
    """Who the coordinator says the card is not for borrowing from, if anyone."""
    from services import resources
    ask = getattr(resources.backend(), "guest_refused", None)
    if ask is None:
        return None
    try:
        return ask(device)
    except Exception:  # an unreadable coordinator refuses nothing
        return None


def claim(name: str, kind: str, ttl_s: float = 15.0, device: int = 0,
          label: str = "") -> dict:
    """Take or renew a claim. A guest that is refused holds nothing."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    ttl = max(1.0, min(float(ttl_s), MAX_TTL_S))
    with _locked(write=True) as claims:
        jobs = _named(claims, "job", device, name)
        held = _outside_holder(device) if kind == "guest" else None
        if kind == "guest" and (jobs or held):
            claims.pop(name, None)
            return {"granted": False, "yield": True, "expires_at": None,
                    "jobs": [*jobs, *([held] if held else [])]}
        entry = Claim(name, kind, device, label, time.time() + ttl)
        claims[name] = entry
        return {"granted": True, "yield": False, "expires_at": entry.expires_at,
                "jobs": jobs, "guests": _named(claims, "guest", device, name)}


def release(name: str) -> bool:
    with _locked(write=True) as claims:
        return claims.pop(name, None) is not None


def snapshot() -> list[dict]:
    with _locked(write=False) as claims:
        return [asdict(c) for c in claims.values()]
