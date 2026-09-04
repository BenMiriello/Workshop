"""The trigger dispatcher, run from the backend on an interval.

WHY THIS EXISTS. A declared trigger is a promise that something happens without
anyone pressing a button, and until this module the only thing that could keep
that promise was a cron entry. `scripts/dispatch_triggers.sh` decides which
scopes have background work due and launches the sweeps; it had exactly one
caller, `cron_agent.sh`, which is reached only from a crontab. This host has no
crontab at all -- not a disabled entry, no crontab -- so the dispatcher had not
run since 2026-07-18 and `.runtime/trigger-state.json` was frozen at that date.
Every scheduled or unattended feature above it was therefore inert while looking
configured, which is the failure mode this repo keeps rediscovering.

WHAT IT DOES NOT DO. Nothing here decides anything. The shell script remains the
one implementation of "which scope is due and what runs for it", including the
autonomy override gate, the developer-mode gate and the detached launch; this
module only calls it on a timer. A second opinion about whether a scope may run
is how the two off-switches in `autonomy_override`'s header came to disagree,
and there is no reason to repeat it for the sake of skipping one 40 ms process
start.

So a tick here is not an arming decision. With the workshop autonomy override
engaged the script logs "dispatching nothing" and returns, exactly as it did
from cron; making the dispatcher tick and arming a scope stay separate acts.
"""
from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import subprocess
from pathlib import Path

from wsplatform import paths

log = logging.getLogger(__name__)

# HOW OFTEN, AND WHAT IT COSTS. One tick is one `bash dispatch_triggers.sh`.
# That is a short Python start for the autonomy gate (~40 ms, and the tick stops
# there whenever the override is engaged) and, when it is clear, a second start
# for the decision engine, which imports the service layer and reads the agent
# and project registries plus the tail of the Jobs log -- one to three seconds of
# one core. At 300 s that is well under 1% of a core, and it is the same cadence
# the semantic index maintenance loop already runs at.
#
# The latency it buys is bounded and lossless: `triggers.due` asks whether a cron
# expression would have fired in (last_fired, now], so a firing inside a tick is
# still due at the end of it. A five-minute interval therefore delays a trigger
# by up to five minutes; it never drops one. Shortening it buys promptness and
# nothing else, so it is not worth the process starts.
DISPATCH_INTERVAL = 300.0

# A tick that has not returned in this long is wedged, not slow: the script
# launches every sweep detached (`setsid nohup ... &`) and returns immediately,
# so its own wall time is the decision engine's, measured in seconds. Killing it
# keeps one bad tick from stopping the loop forever.
DISPATCH_TIMEOUT = 120.0


def dispatcher_path() -> Path:
    """The script this loop schedules. Resolved per call rather than pinned at
    import, so a caller that relocates the workshop root (the test suite builds
    a sandbox root with its own registries) reaches its own copy."""
    return paths.workshop_root() / "scripts" / "dispatch_triggers.sh"


def _lock_path() -> Path:
    """The dispatch lock. See `dispatch_once` for what it is protecting."""
    return paths.runtime_dir() / "trigger-dispatch.lock"


def dispatch_once(*, timeout: float = DISPATCH_TIMEOUT) -> dict:
    """Run one dispatcher tick. Never raises; returns what happened.

    TWO BACKENDS MUST NOT BOTH DISPATCH. The caps inside the script
    (MAX_PROJECT_SWEEPS, MAX_AGENT_SWEEPS) bound one tick, not two concurrent
    ones, and the cron firing it records is written by the last writer of
    `trigger-state.json` -- so two dispatchers would double-launch and then
    disagree about what had fired. The semantic index loop states the same
    requirement as an in-process flag, which is enough there because the process
    that must not rebuild is a child of this one. Here the process that must not
    dispatch is ANOTHER BACKEND, which no module-level flag can see, so the gate
    is an exclusive `flock` on a file both would open. A backend that cannot take
    it skips this tick rather than waiting: the next one is 300 s away and the
    holder is doing the work anyway.
    """
    script = dispatcher_path()
    if not script.exists():
        return {"ran": False, "skipped": f"no dispatcher at {script}"}
    lock = _lock_path()
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as e:
        return {"ran": False, "skipped": f"cannot open the dispatch lock: {e}"}
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"ran": False, "skipped": "another process holds the dispatch lock"}
        env = {**os.environ, "WORKSHOP": str(paths.workshop_root())}
        try:
            proc = subprocess.run(
                ["bash", str(script)],
                env=env, cwd=str(paths.workshop_root()),
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"ran": False, "skipped": f"dispatcher timed out after {timeout}s"}
        except OSError as e:
            return {"ran": False, "skipped": f"could not start the dispatcher: {e}"}
        return {
            "ran": True,
            "rc": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }
    finally:
        os.close(fd)


async def dispatch_loop(interval: float = DISPATCH_INTERVAL) -> None:
    """Tick the dispatcher forever. Started from the backend lifespan.

    A failed tick is logged and the loop continues. The dispatcher is the only
    thing that turns a declared trigger into a running sweep, so a loop that died
    on one bad tick would put the whole system back in the state this module
    exists to end -- configured, and silently doing nothing.
    """
    log.info("trigger dispatch loop started (interval=%ss, dispatcher=%s)",
             interval, dispatcher_path())
    while True:
        try:
            res = await asyncio.to_thread(dispatch_once)
            if res.get("skipped"):
                log.debug("trigger dispatch tick skipped: %s", res["skipped"])
            elif res.get("rc"):
                log.warning("trigger dispatch tick failed (rc=%s): %s",
                            res["rc"], res.get("stderr") or "(no output)")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("trigger dispatch tick raised")
        await asyncio.sleep(interval)
