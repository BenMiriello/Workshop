"""The reconciler between execution INTENT and the worker MECHANISM.

WHY THIS EXISTS. Everything the background work loop needs was already built and
none of it was connected:

  intent      services/scope_execution  — "this scope's filed work may auto-run"
              (per scope, reactive/supervised/autonomous, fail-closed)
  mechanism   scripts/queue_runner.sh   — a resident loop that sweeps ONE TREE,
              claiming ready tickets and running the worker
  gate        tasks.next_ready(unattended=True) — refuses to hand out work for a
              scope that is not armed
  brake       services/autonomy_override — the one global off-switch, above both

The chain is complete: an armed scope + a resident runner = work gets done. The
defect was that arming a scope and having a runner resident were two INDEPENDENT
controls and neither implied the other. Arming a scope from the chat's Work
settings therefore did exactly nothing, and starting a runner by hand ran a loop
that `next_ready` refused to feed. Two controls for one intent, silently
disagreeing in both directions, is the "built but not wired up" failure mode.

So: intent is the only thing anyone sets, and the runner is DERIVED from it.
This module is the control loop that makes the mechanism follow the intent, in
the same shape as the other reconcilers the lifespan owns (port_registry,
jobs). It is deliberately ADDITIVE: it only ever ENSURES a runner
for an armed scope. It never kills a runner it did not ask for, so a hand-started
sweep for debugging is left alone. Disarming stops the runner at the moment of
disarming (routes/autonomy), which is a direct consequence of an operator action
rather than a policing loop.

It also owns the runner's PROCESS FACTS (lock, pidfile, residency probe), which
lived in routes/autonomy.py. A route module should not own process management,
and two callers needed it.

And it owns THE PER-TREE LOCK ITSELF, not just the runner's use of it.
`workspace-ownership-and-leases-2026-08-22.md` §15 (step B2) is explicit that
the worktree record folds into "work_driver's existing per-tree lock rather than
adding a third", so `tree_lock()` is that lock generalized by a `kind` prefix
and `services/worktrees.py` calls it. There is one flock derivation in this
codebase and it is `queue_key`.

Composition with the global brake: an engaged override means no new runner is
started here. Running ones are NOT killed, because queue_runner.sh re-reads the
brake every iteration and idles on its own — that is the existing contract and
duplicating it as a kill would make releasing the brake require a restart.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import logging
import os
import signal
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from services import autonomy_override, scope_execution
from wsplatform import paths

log = logging.getLogger(__name__)

WORKSHOP_ROOT = paths.workshop_root()
QUEUE_WATCHDOG = WORKSHOP_ROOT / "scripts" / "queue_runner_watchdog.sh"
QUEUE_RUNNER_LOG = WORKSHOP_ROOT / ".runtime" / "queue-runner.log"

RECONCILE_INTERVAL = 60.0
# How long to wait before re-attempting a start that did not produce a resident
# runner. The watchdog refuses a tree the developer-mode gate rejects (ADR 0014)
# and logs the refusal, so retrying every tick would write a line a minute
# forever for a scope that can never run.
RETRY_AFTER = 600.0

# root -> monotonic timestamp of the last start attempt that did not take.
_failed_at: dict[str, float] = {}


# ---- process facts ---------------------------------------------------------

def queue_key(dir_str: str) -> str:
    """The per-tree key both the lock and the pidfile are named after. Mirrors
    the shell derivation exactly: md5 of `printf '%s\\n' "$DIR"`, first 12 hex."""
    return hashlib.md5((dir_str + "\n").encode()).hexdigest()[:12]


def _lock_file(dir_str: str, kind: str) -> Path:
    return WORKSHOP_ROOT / ".runtime" / f"{kind}-{queue_key(dir_str)}.lock"


def lock_path(dir_str: str) -> Path:
    return _lock_file(dir_str, "queue-runner")


@contextmanager
def tree_lock(dir_str: str | Path, *, kind: str = "worktree",
              timeout: float = 30.0) -> Iterator[None]:
    """Hold the per-tree lock for `dir_str` for the length of a block.

    THE ONE PER-TREE LOCK PRIMITIVE, and it is the one that was already here.
    `workspace-ownership-and-leases-2026-08-22.md` §15 (step B2) says to fold
    the worktree record's mutual exclusion into "work_driver's existing per-tree
    lock rather than adding a third", so this is that lock generalized rather
    than a second implementation of it: same `queue_key` derivation, same
    `.runtime/<kind>-<key>.lock` path, same flock, and `lock_path`/`is_resident`
    now go through the same `_lock_file`.

    KIND, NOT A SECOND MECHANISM. `queue-runner` is held for the entire
    RESIDENCY of a sweep -- minutes to hours -- so serializing a record write
    against that same file would not be mutual exclusion, it would be a wait for
    the sweep to exit. The `kind` prefix keeps the derivation shared while
    letting "a runner lives here" and "this tree is being mutated right now" be
    two different questions, which they are.

    Blocking with a bound, not spinning: a caller that cannot get the lock
    within `timeout` raises `TimeoutError` rather than proceeding, because
    proceeding is how two allocations of the same slug collide.
    """
    target = os.path.realpath(str(dir_str))
    lock = _lock_file(target, kind)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"could not take the {kind} lock for {target} within "
                        f"{timeout:g}s ({lock})")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def pid_path(dir_str: str) -> Path:
    return WORKSHOP_ROOT / ".runtime" / f"queue-runner-{queue_key(dir_str)}.pid"


def is_resident(dir_str: str) -> bool:
    """Whether a worker loop actually holds this tree's lock.

    Probing the flock rather than trusting a flag file is the point: the absence
    of the off-switch says work is PERMITTED, not that anything is RUNNING, and
    collapsing the two is how the UI came to report a loop that did not exist.
    """
    lock = lock_path(dir_str)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)  # we got it => nobody home
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def runner_pid(dir_str: str) -> int | None:
    """The runner's pid, reported only when the lock says someone is there: a
    pidfile outlives a SIGKILLed runner because the EXIT trap never fires."""
    if not is_resident(dir_str):
        return None
    try:
        return int(pid_path(dir_str).read_text().strip())
    except (OSError, ValueError):
        return None


def runner_state(dir_str: str | None = None) -> dict:
    """Real loop state for one tree: is a worker resident, and is work permitted.

    `running` and `enabled` are reported separately and never collapsed, because
    work only happens when both are true.
    """
    target = str(dir_str or WORKSHOP_ROOT)
    last_line = None
    try:
        lines = [ln for ln in QUEUE_RUNNER_LOG.read_text(errors="replace").splitlines()
                 if ln.strip()]
        last_line = lines[-1] if lines else None
    except OSError:
        last_line = None
    return {
        "running": is_resident(target),
        "pid": runner_pid(target),
        "enabled": not autonomy_override.is_engaged(),
        "dir": target,
        "lock_path": str(lock_path(target)),
        "log_path": str(QUEUE_RUNNER_LOG),
        "last_line": last_line,
    }


# ---- mechanism -------------------------------------------------------------

def _runner_flags(mode: str) -> list[str]:
    """Map the scope's execution mode onto the runner's supervision flag.

    supervised (the default) lands a passing ticket at `in_review` with the
    operator as the final gate; autonomous lets it reach `done`. This is the one
    place `scope_execution`'s stage ceiling becomes a process argument.
    """
    return ["--unsupervised"] if mode == "autonomous" else []


def worker_for(root: str | Path) -> str | None:
    """The worker this tree's sweep runs AS, as `agent:<slug>`, or None.

    THE SWEEP HAS TO NAME SOMEBODY OR IT SELECTS NOTHING.
    `tasks.next_ready(unattended=True)` matches only tickets assigned to the
    worker the sweep names, and treats an unnamed sweep as naming nobody:

        is_worker_assignee(assignee) and t.assignee == assignee

    That rule is right and is not what this function changes. What it fixes is
    that nothing ever computed the name. `project_ralph.sh` has taken
    `--assignee agent:<slug>` since it was written, `queue_runner.sh` had no
    such flag until 2026-09-16, and so every resident sweep passed None and
    refused every ticket while reporting itself healthy. Measured that day,
    after the operator released the autonomy brake and watched nothing happen:
    80 ready tickets, refused once per sweep, into a log file.

    The name is DERIVED, not configured, because a second place to set it is a
    second place for it to disagree with the scope it belongs to. A scope's
    worker is its maker agent: the one non-retired agent whose role is `maker`
    and whose `project_slug` is this scope. The per-ticket grant is unchanged
    and is still the only control: naming the worker says who may be assigned
    work here, assigning a ticket is what actually hands it over.

    Returns None when the scope has no maker, which `ensure` treats as a refusal
    to start rather than as a reason to start a sweep that cannot select
    anything. A runner that is resident and permanently idle is the exact
    failure this whole function exists to end.
    """
    from services import agents_registry, scope_execution

    scope = scope_execution._scope_of(root)
    if not scope:
        return None
    try:
        agents = agents_registry.list_agents()
    except Exception:
        log.exception("work driver: could not read the agent registry")
        return None
    makers = [a for a in agents
              if a.get("role") == "maker"
              and not a.get("retired")
              and (a.get("project_slug") or "") == scope]
    if len(makers) != 1:
        # Zero is an unconfigured scope. More than one is ambiguous, and
        # guessing which of two makers owns a queue is how a ticket gets run by
        # something the operator did not mean. Both refuse.
        log.warning("work driver: scope %r has %d maker agent(s); "
                    "an unattended sweep needs exactly one to run as",
                    scope, len(makers))
        return None
    return f"agent:{makers[0]['slug']}"


def ensure(root: str | Path, mode: str = "supervised") -> bool:
    """Make a runner resident for `root`. Idempotent and race-free: the watchdog
    probes the same flock the runner holds, so a blind call is safe.

    Returns True when a runner is resident afterwards.
    """
    target = os.path.realpath(str(root))
    if is_resident(target):
        _failed_at.pop(target, None)
        return True
    if not QUEUE_WATCHDOG.exists():
        log.warning("work driver: watchdog missing at %s", QUEUE_WATCHDOG)
        return False
    last = _failed_at.get(target)
    if last is not None and (time.monotonic() - last) < RETRY_AFTER:
        return False
    worker = worker_for(target)
    if worker is None:
        # Refuse rather than start. A sweep with no worker selects nothing, so
        # starting one here would put a resident, healthy-looking, permanently
        # idle runner on the tree, which is precisely the state this check was
        # added to stop being possible.
        _failed_at[target] = time.monotonic()
        log.warning("work driver: not starting a runner for %s: the scope has "
                    "no single maker agent to run as, so an unattended sweep "
                    "could select nothing", target)
        return False
    try:
        subprocess.Popen(
            ["bash", str(QUEUE_WATCHDOG), target,
             "--assignee", worker, *_runner_flags(mode)],
            cwd=str(WORKSHOP_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError:
        log.exception("work driver: could not spawn watchdog for %s", target)
        _failed_at[target] = time.monotonic()
        return False
    # The watchdog spawns the runner detached; give it a beat before deciding
    # whether the start took, so a slow fork is not recorded as a refusal.
    time.sleep(2.0)
    if is_resident(target):
        _failed_at.pop(target, None)
        log.info("work driver: runner now resident for %s (%s)", target, mode)
        return True
    # Most likely the developer-mode gate refused the tree; the watchdog has
    # already written why into queue-runner.log. Back off rather than retry
    # every tick forever.
    _failed_at[target] = time.monotonic()
    log.warning("work driver: runner did not come up for %s; see %s",
                target, QUEUE_RUNNER_LOG)
    return False


def stop(root: str | Path) -> bool:
    """Stop the resident runner for `root`. The sweep's own lease/reclaim path
    returns a half-worked ticket to the queue, so this loses no tickets."""
    target = os.path.realpath(str(root))
    pid = runner_pid(target)
    if not pid:
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return False
    _failed_at.pop(target, None)
    return True


# ---- the control loop ------------------------------------------------------

def reconcile() -> dict:
    """One pass: every armed scope gets a resident runner.

    Additive only. A tree with a runner but no arming is left alone: it is
    either an operator's hand-started sweep, or a scope disarmed a moment ago,
    and `next_ready(unattended=True)` already refuses to feed it work.
    """
    if autonomy_override.is_engaged():
        return {"armed": 0, "started": 0, "skipped": "autonomy override engaged"}
    armed = scope_execution.armed_roots()
    started = 0
    for root, mode in armed:
        if not is_resident(os.path.realpath(root)) and ensure(root, mode):
            started += 1
    return {"armed": len(armed), "started": started}


async def reconcile_loop(interval: float = RECONCILE_INTERVAL) -> None:
    """Run reconcile() forever on `interval`. Started from the backend lifespan.

    This is what makes background execution SURVIVE: the runner is a detached
    process that dies with a reboot, an OOM kill or a stray `kill`, and the only
    thing that ever restarted it was a cron entry that is not installed. The
    backend is a systemd unit that outlives every run, so it reconciles.

    A failed pass is logged and the loop continues; reconciliation must not die
    quietly or an armed scope silently stops working.
    """
    log.info("work driver reconcile loop started (interval=%ss)", interval)
    while True:
        try:
            out = await asyncio.to_thread(reconcile)
            if out.get("started"):
                log.info("work driver: started %s runner(s)", out["started"])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("work driver reconcile pass failed")
        await asyncio.sleep(interval)


# ---- the shared intent surface ---------------------------------------------
#
# ONE IMPLEMENTATION OF "START", "STOP" AND "SET THE ARM LEVEL", because there
# are now two callers: `routes/autonomy.py` (the Settings drawer, which names a
# TREE) and `routes/agents.py` (the maker agent, which names a SCOPE). Those two
# controls describe the same act, and a second copy of the intent-then-mechanism
# ordering is precisely how the pre-driver system came to have two controls for
# one intent that silently disagreed in both directions.
#
# THE RULE THESE PRESERVE, unchanged from the route code they were lifted from:
#
#   START ARMS.    The runner is DERIVED from `scope_execution` by `reconcile()`
#                  above, so a start that only spawned a process would be undone
#                  the moment the next pass found the scope unarmed -- and, before
#                  the driver existed, would simply die at the next reboot with
#                  nothing to bring it back. An already-`autonomous` scope is NOT
#                  downgraded: pressing Start on a scope the operator took all the
#                  way to run-to-done must not quietly move the ceiling back.
#   STOP DISARMS.  `reconcile()` re-ensures a runner for every armed scope once a
#                  minute, so killing without clearing the intent would have the
#                  loop back within the poll interval and Stop would be a button
#                  that lies. Clearing the intent IS the stop; the SIGTERM is only
#                  how it takes effect now rather than at the end of the sweep.
#
# A tree that maps to no scope (`scope=None`) can still be swept on demand; it
# just has no intent to record, and the reconciler will not keep it alive.

# How long to wait after a SIGTERM before reporting state, so the answer
# describes the runner's condition after the signal rather than a millisecond
# before it. Small, and it is the reason `stop_work` is called off the event
# loop (`asyncio.to_thread`) by both routes.
STOP_SETTLE_S = 1.5


def set_execution(scope: str | None, root: str | Path, mode: str) -> str:
    """Record the execution intent for `scope` and make the mechanism follow NOW.

    The store write is the source of truth and always happens; the ensure/stop
    that follows is best-effort, because `reconcile()` converges the mechanism on
    its next pass either way. Doing it here means the caller's response already
    reflects a running loop, and disarming stops immediately rather than at the
    end of the current sweep.
    """
    if mode not in scope_execution.MODES:
        raise ValueError(f"unknown execution mode {mode!r}; one of {scope_execution.MODES}")
    if scope:
        scope_execution.set_mode(scope, mode)
    armed = scope_execution.is_armed(scope) if scope else (mode != "reactive")
    try:
        if armed:
            ensure(root, mode)
        else:
            stop(root)
    except Exception:
        log.warning("work driver could not follow the execution change for %s",
                    root, exc_info=True)
    return mode


def start_work(scope: str | None, root: str | Path) -> bool:
    """Start working this tree: ARM the scope if it is not armed, then ensure a
    runner. Returns whether a runner is resident afterwards."""
    if scope and not scope_execution.is_armed(scope):
        scope_execution.set_mode(scope, "supervised")
    mode = scope_execution.get_mode(scope) if scope else "supervised"
    if mode == "reactive":
        # Only reachable for a tree that maps to no scope; a named scope was
        # just armed above. Sweeping on demand is allowed, supervised.
        mode = "supervised"
    return ensure(root, mode)


def stop_work(scope: str | None, root: str | Path, *, settle: float = STOP_SETTLE_S) -> bool:
    """Stop working this tree: DISARM the scope first, then kill the runner.

    Disarm-then-kill, never the other way round. The sweep's own lease/reclaim
    path returns a half-worked ticket to the queue, so this loses no tickets.
    """
    if scope:
        scope_execution.set_mode(scope, "reactive")
    killed = stop(root)
    if settle > 0:
        time.sleep(settle)
    return killed
