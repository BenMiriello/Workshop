"""The passes this installation runs on a timer, run BY the backend.

WHY THIS EXISTS. There is no crontab on this host -- not a disabled entry, no
crontab -- and `systemctl --user list-timers` is empty. Anything that needed to
happen on a schedule therefore did not happen, while continuing to read as
configured. Two measured cases:

  * `scripts/dispatch_triggers.sh` is the only thing that turns a declared `cron`
    or `loop` trigger into a running sweep, and its only caller was
    `cron_agent.sh`, which a crontab reaches and nothing else does. It had not
    run since 2026-07-18 and `.runtime/trigger-state.json` was frozen at that
    date.
  * every ratchet baseline in `scripts/` states that its number only goes down,
    and the only thing that reads the ABSOLUTE count is a bare run somebody
    chooses to do. Three silent upward drifts were found by audits rather than
    by any gate (b9fa2078 on 2026-08-25, two more on 2026-09-04).

`the-plan-2026-08-23.md` section 5 item 3 states the answer: anything scheduled
uses the backend lifespan reconciler, which the semantic index maintenance loop
already demonstrates. This is that reconciler, and it is ONE loop with a table of
passes rather than a loop per job, because the second scheduler is how a system
ends up with two answers to "did that run".

WHAT A PASS IS ALLOWED TO BE. A command, an interval, and a wall limit. Nothing
here decides anything: `dispatch_triggers.sh` remains the one implementation of
which scope is due and what runs for it (including the autonomy override, which
today stops every trigger tick before it dispatches), and
`report_ratchet_drift.py` remains the one implementation of what has drifted. A
second opinion held here is how the two off-switches in `autonomy_override`'s
header came to disagree.

So a tick is not an arming decision. Making the dispatcher tick and arming a
scope stay separate acts, and the second is the operator's.
"""
from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, NamedTuple, Sequence

from wsplatform import paths

log = logging.getLogger(__name__)


class Pass(NamedTuple):
    """One scheduled command. `command` is resolved per call rather than pinned
    at import, so a caller that relocates the workshop root -- the test suite
    builds a sandbox root with its own registries -- reaches its own copy."""
    name: str
    interval: float
    timeout: float
    command: Callable[[], list[str]]


# HOW OFTEN THE TRIGGER PASS RUNS, AND WHAT IT COSTS. One tick is one
# `bash dispatch_triggers.sh`. That is a short Python start for the autonomy gate
# (~40 ms, and the tick stops there whenever the override is engaged) and, when
# it is clear, a second start for the decision engine, which imports the service
# layer and reads the agent and project registries plus the tail of the Jobs log
# -- one to three seconds of one core. At 300 s that is well under 1% of a core,
# and it is the same cadence the semantic index maintenance loop already runs at.
#
# The latency it buys is bounded and lossless: `triggers.due` asks whether a cron
# expression would have fired in (last_fired, now], so a firing inside a tick is
# still due at the end of it. A five-minute interval delays a trigger by up to
# five minutes; it never drops one. Shortening it buys promptness and nothing
# else, so it is not worth the process starts.
TRIGGER_INTERVAL = 300.0
TRIGGER_TIMEOUT = 120.0

# HOW OFTEN THE RATCHET PASS RUNS, AND WHAT IT COSTS. Ten bare checkers, measured
# at 21 s of one core in total on 2026-09-04, dominated by `check_no_app_names`
# (10 s) and `check_dead_surface` (6 s). Six hours puts that at 0.1% of a core
# and satisfies the requirement it exists for -- an upward drift reported within
# a day of the commit that caused it. The script is passed the same number as
# `--if-older-than`, so the restarts this tree sees several times a day re-read
# the receipt instead of re-measuring the tree.
RATCHET_INTERVAL = 21600.0
RATCHET_TIMEOUT = 900.0

# How often the loop wakes to see whether any pass is due. Cheap by construction:
# a wake with nothing due is a dict comparison. It bounds the jitter on every
# interval above, which is why it is much smaller than the smallest of them.
TICK = 30.0


def _trigger_command() -> list[str]:
    return ["bash", str(paths.workshop_root() / "scripts" / "dispatch_triggers.sh")]


def _ratchet_command() -> list[str]:
    return [sys.executable,
            str(paths.workshop_root() / "scripts" / "report_ratchet_drift.py"),
            "--if-older-than", str(int(RATCHET_INTERVAL))]


TRIGGERS = Pass("triggers", TRIGGER_INTERVAL, TRIGGER_TIMEOUT, _trigger_command)
RATCHETS = Pass("ratchets", RATCHET_INTERVAL, RATCHET_TIMEOUT, _ratchet_command)

PASSES: tuple[Pass, ...] = (TRIGGERS, RATCHETS)


def _lock_path(name: str) -> Path:
    """The per-pass lock. See `run_pass` for what it is protecting."""
    return paths.runtime_dir() / f"schedule-{name}.lock"


def run_pass(p: Pass) -> dict:
    """Run one pass now. Never raises; returns what happened.

    TWO BACKENDS MUST NOT BOTH RUN THE SAME PASS. For triggers the caps inside
    the script (MAX_PROJECT_SWEEPS, MAX_AGENT_SWEEPS) bound one tick, not two
    concurrent ones, and the cron firing it records is written by the last writer
    of `trigger-state.json` -- so two dispatchers would double-launch and then
    disagree about what had fired. For ratchets, two runs would race on one
    receipt. The semantic index loop states the same requirement as an
    in-process flag, which is enough there because the process that must not
    rebuild is a child of this one. Here the process that must not run is ANOTHER
    BACKEND, which no module-level flag can see, so the gate is an exclusive
    `flock` on a file both would open. A backend that cannot take it skips this
    tick rather than waiting: the next one is a whole interval away and the
    holder is doing the work anyway.
    """
    argv = p.command()
    # argv[0] is the interpreter (`bash`, or this venv's python), argv[1] the
    # script. A missing script is reported rather than raised: a checkout without
    # it is a deployment fact, not a crash.
    target = Path(argv[1])
    if not target.exists():
        return {"ran": False, "skipped": f"no such command: {target}"}
    lock = _lock_path(p.name)
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as e:
        return {"ran": False, "skipped": f"cannot open the {p.name} lock: {e}"}
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"ran": False, "skipped": f"another process holds the {p.name} lock"}
        env = {**os.environ, "WORKSHOP": str(paths.workshop_root())}
        try:
            proc = subprocess.run(
                argv, env=env, cwd=str(paths.workshop_root()),
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=p.timeout,
            )
        except subprocess.TimeoutExpired:
            return {"ran": False, "skipped": f"{p.name} timed out after {p.timeout}s"}
        except OSError as e:
            return {"ran": False, "skipped": f"could not start {p.name}: {e}"}
        return {
            "ran": True,
            "rc": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }
    finally:
        os.close(fd)


async def schedule_loop(passes: Sequence[Pass] = PASSES) -> None:
    """Run every pass on its own interval, forever. Started from the lifespan.

    Each pass runs once at startup and then on its interval. A failed pass is
    logged and the loop continues, and a pass that raises does not stop its
    neighbours: this loop is the only thing keeping any of these promises, so a
    loop that died on one bad tick would put the system back in the state this
    module exists to end -- configured, and silently doing nothing.
    """
    log.info("backend schedule loop started (%s)",
             ", ".join(f"{p.name}@{p.interval:g}s" for p in passes))
    due = {p.name: 0.0 for p in passes}
    while True:
        now = time.monotonic()
        for p in passes:
            if now < due[p.name]:
                continue
            due[p.name] = now + p.interval
            try:
                res = await asyncio.to_thread(run_pass, p)
                if res.get("skipped"):
                    log.debug("%s pass skipped: %s", p.name, res["skipped"])
                elif res.get("rc"):
                    log.warning("%s pass failed (rc=%s): %s", p.name, res["rc"],
                                res.get("stderr") or res.get("stdout") or "(no output)")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s pass raised", p.name)
        await asyncio.sleep(TICK)
