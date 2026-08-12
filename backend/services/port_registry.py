"""Centralized port registry — the single authority for local service ports.

Before this, two independent allocators existed (services/dev_servers, 8500-8599,
in-memory; services/local_runner, 8600-8699, in-memory + a runtime store). Neither
checked whether a port was *actually* free on the OS, neither survived a backend
restart cleanly, and a deploy in one window could not see a preview in the other.

This module is the one allocator they both delegate to, so a port is handed out at
most once and never collides with a port some other process already holds. It:

  - allocates from a per-KIND window (preview / deploy / app), so the windows can
    still be reasoned about separately while sharing one collision-free namespace;
  - probes the OS (bind to 127.0.0.1:port) before handing a port out, so a port held
    by ANY process on the box (postgres, another app, a stale orphan) is skipped;
  - persists allocations to .runtime/ports.json so the assignment for an owner is
    stable across calls and visible after a backend restart;
  - reconciles: an allocation whose recorded pid is dead is released automatically,
    so crashed/orphaned services free their port without manual cleanup.

An "owner" is an opaque stable key the caller chooses (e.g. "preview:gengui",
"deploy:gengui", "app:gengui/core"). Re-allocating for the same owner returns the
same port while it is still valid, which is what makes restarts idempotent.

Single-user box, one backend process: a threading.Lock around the read-modify-write
is all the concurrency control this needs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path
from services import paths

log = logging.getLogger("workshop.port_registry")

RECONCILE_INTERVAL = 15.0  # seconds between reconcile passes

# Per-kind allocation windows. Distinct ranges so a preview, a single-process
# deploy, and a multi-service app of the same project never fight for a port even
# before the OS probe runs.
WINDOWS: dict[str, tuple[int, int]] = {
    "preview": (8500, 8599),
    "deploy": (8600, 8699),
    "app": (8700, 8799),
}

# Ports the platform itself uses. The OS probe already skips anything currently
# bound, but these are listed so we never hand one out during a momentary gap
# (e.g. the backend restarting). Not a security boundary — a courtesy list.
RESERVED: frozenset[int] = frozenset({
    8000,   # workshop backend (uvicorn)
    5173, 5174, 5175,  # workshop frontend (vite) + alternates
    8283,   # letta
    11434, 11435,  # ollama
    8443, 8445, 8446, 8447,  # tailscale serve mounts
})

STORE = paths.env_path("WORKSHOP_PORT_REGISTRY", "ports.json")

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    if not STORE.exists():
        return {"allocations": {}}
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"allocations": {}}
    if not isinstance(data, dict) or not isinstance(data.get("allocations"), dict):
        return {"allocations": {}}
    return data


def _save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(STORE.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, STORE)


def process_alive(pid: int | None) -> bool:
    """True if `pid` is a live, non-zombie process.

    A child the backend spawned but never wait()s becomes a ZOMBIE when it exits —
    and `os.kill(pid, 0)` still succeeds for a zombie, which would make a crashed
    service look alive forever (defeating crash detection and port reclamation). So
    we read /proc/<pid>/stat and treat state 'Z' (and 'X', dead) as not alive, and
    opportunistically reap the zombie if it is our child."""
    if not pid:
        return False
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            data = f.read()
        # state is the char after the ")" that closes the (comm) field.
        state = data[data.rfind(b")") + 2: data.rfind(b")") + 3]
    except FileNotFoundError:
        return False
    except OSError:
        # Can't introspect; fall back to the signal probe.
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    if state in (b"Z", b"X", b"x"):
        try:
            os.waitpid(pid, os.WNOHANG)  # reap if it's our child; harmless otherwise
        except (ChildProcessError, OSError):
            pass
        return False
    return True


def _alloc_alive(pid: int | None) -> bool:
    """Liveness for an ALLOCATION: a None pid means 'reserved but not yet bound to
    a process' — kept (treated alive) so a fresh reservation is not reaped before
    its service spawns. A real pid is checked for real liveness."""
    if not pid:
        return True
    return process_alive(pid)


def port_free_on_os(port: int) -> bool:
    """True if nothing is currently bound to 127.0.0.1:port. Uses a throwaway
    socket with SO_REUSEADDR so a recently-closed port in TIME_WAIT is still
    considered free (matches how a server with SO_REUSEADDR would rebind)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _reconcile_locked(data: dict) -> bool:
    """Drop allocations whose recorded process is gone. Returns True if anything
    changed. Caller holds the lock."""
    allocs: dict = data["allocations"]
    dead = [owner for owner, rec in allocs.items()
            if not _alloc_alive(rec.get("pid"))]
    for owner in dead:
        allocs.pop(owner, None)
    return bool(dead)


def reconcile() -> dict:
    """Public reconcile pass: free ports whose owning process has died. Returns
    {released: [owner, ...]}. Safe to call on startup and periodically."""
    with _LOCK:
        data = _load()
        before = set(data["allocations"].keys())
        changed = _reconcile_locked(data)
        if changed:
            _save(data)
        after = set(data["allocations"].keys())
    return {"released": sorted(before - after)}


async def reconcile_loop(interval: float = RECONCILE_INTERVAL) -> None:
    """Run reconcile() forever on `interval`. Started from the backend lifespan.

    This is all that remains of the old app_monitor: it used to also re-spawn crashed
    subprocess services with exponential backoff, but the container backend retired
    that in favour of docker's own restart policy, and the re-spawn path had been
    unreachable ever since. Freeing the ports of dead processes is still ours.

    A failed pass is logged and the loop continues — reconciliation must not die
    quietly, or ports leak until the next backend restart.
    """
    log.info("port reconcile loop started (interval=%ss)", interval)
    while True:
        try:
            await asyncio.to_thread(reconcile)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("port reconcile pass failed")
        await asyncio.sleep(interval)


def allocate(owner: str, kind: str = "app", *, pid: int | None = None,
             preferred: int | None = None) -> int:
    """Allocate (or return the existing) port for `owner` within `kind`'s window.

    Idempotent: if `owner` already holds a still-valid port (process alive, port
    still free or held by that process), the same port is returned. Otherwise a
    fresh port is chosen — skipping RESERVED ports, ports held by other owners in
    the registry, and ports currently bound on the OS. `preferred` is honored when
    it is in-window and free. Raises RuntimeError if the window is exhausted.

    `pid` records the owning process so reconcile() can free the port when that
    process dies. It can be set later via set_pid() (e.g. allocate first, then
    spawn with PORT, then record the child pid)."""
    lo, hi = WINDOWS.get(kind, WINDOWS["app"])
    with _LOCK:
        data = _load()
        _reconcile_locked(data)
        allocs: dict = data["allocations"]

        existing = allocs.get(owner)
        if existing and lo <= existing.get("port", -1) <= hi:
            p = existing["port"]
            # Still ours if the port is free OR already bound by our own process.
            if port_free_on_os(p) or _alloc_alive(existing.get("pid")):
                if pid is not None:
                    existing["pid"] = pid
                    _save(data)
                return p

        taken = {rec["port"] for rec in allocs.values() if owner_other(rec, owner)}

        def _usable(p: int) -> bool:
            return (lo <= p <= hi and p not in RESERVED and p not in taken
                    and port_free_on_os(p))

        chosen: int | None = None
        if preferred is not None and _usable(preferred):
            chosen = preferred
        else:
            for p in range(lo, hi + 1):
                if _usable(p):
                    chosen = p
                    break
        if chosen is None:
            raise RuntimeError(f"no free port in {kind} window [{lo},{hi}]")

        allocs[owner] = {
            "port": chosen,
            "kind": kind,
            "owner": owner,
            "pid": pid,
            "claimed_at": _now(),
        }
        _save(data)
        return chosen


def owner_other(rec: dict, owner: str) -> bool:
    """Helper: True if a registry record belongs to a DIFFERENT owner (so its
    port is off-limits when allocating for `owner`)."""
    return rec.get("owner") != owner


def set_pid(owner: str, pid: int | None) -> None:
    """Record (or clear) the owning process id for an allocation. No-op if the
    owner has no allocation."""
    with _LOCK:
        data = _load()
        rec = data["allocations"].get(owner)
        if rec is None:
            return
        rec["pid"] = pid
        _save(data)


def release(owner: str) -> bool:
    """Free `owner`'s port. Returns True if an allocation was removed."""
    with _LOCK:
        data = _load()
        if owner in data["allocations"]:
            data["allocations"].pop(owner, None)
            _save(data)
            return True
    return False


def peek(kind: str = "app") -> int:
    """Lowest usable port in `kind`'s window WITHOUT claiming it. For populating a
    form default (e.g. the Preview port picker). Raises RuntimeError if exhausted."""
    lo, hi = WINDOWS.get(kind, WINDOWS["app"])
    with _LOCK:
        data = _load()
        _reconcile_locked(data)
        taken = {rec["port"] for rec in data["allocations"].values()}
    for p in range(lo, hi + 1):
        if p not in RESERVED and p not in taken and port_free_on_os(p):
            return p
    raise RuntimeError(f"no free port in {kind} window [{lo},{hi}]")


def get(owner: str) -> dict | None:
    """Current allocation record for `owner`, or None."""
    return _load()["allocations"].get(owner)


def all_allocations() -> list[dict]:
    """Every current allocation (after a reconcile pass), newest first."""
    reconcile()
    recs = list(_load()["allocations"].values())
    recs.sort(key=lambda r: r.get("claimed_at") or "", reverse=True)
    return recs
