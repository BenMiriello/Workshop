"""Autonomy power switches for the Workshop autonomy stack.

Cron autonomy disable/enable engages the override overlay
(services/autonomy_override), which every gate reads through one function. It
halts the Ralph loop without touching crontab. This used to drop a separate
AUTONOMY_DISABLED flag file that only the shell scripts read, so the API and the
loop could disagree about whether autonomy was on; that flag is gone and this is
the single switch.

Plus the queue-runner controls, concurrency limits, and read-only status
endpoints feeding the Settings UI.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import os
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from routes import autonomy_models as am
from services import agent_slots, autonomy_override, jobs
from pydantic import BaseModel, Field
from wsplatform import paths


router = APIRouter(tags=["autonomy"])


WORKSHOP_ROOT = paths.workshop_root()
QUEUE_RUNNER = WORKSHOP_ROOT / "scripts" / "queue_runner.sh"
QUEUE_WATCHDOG = WORKSHOP_ROOT / "scripts" / "queue_runner_watchdog.sh"
QUEUE_RUNNER_LOG = WORKSHOP_ROOT / ".runtime" / "queue-runner.log"
DEV_MODE_GATE = WORKSHOP_ROOT / "scripts" / "dev_mode_gate.sh"
INSTANCE_LIB = WORKSHOP_ROOT / "scripts" / "lib" / "instance.sh"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _queue_lock_path(dir_str: str) -> Path:
    """The lock queue_runner.sh holds for a tree. Mirrors the shell derivation
    exactly: md5 of `printf '%s\\n' "$DIR"`, first 12 hex chars."""
    return Path(f"/tmp/workshop-queue-runner-{_queue_key(dir_str)}.lock")


def _queue_key(dir_str: str) -> str:
    """The per-tree key both the lock and the pidfile are named after. Mirrors
    the shell derivation exactly: md5 of `printf '%s\\n' "$DIR"`, first 12 hex."""
    return hashlib.md5((dir_str + "\n").encode()).hexdigest()[:12]


def _queue_pid_path(dir_str: str) -> Path:
    """The pidfile for a tree's runner.

    PER TREE, like the lock. It used to be one shared file while the lock was
    already per-tree, so two runners on two trees both ran but the second
    clobbered the first's pid: Stop then killed whichever started last, whatever
    tree it was asked about, and left the other one unstoppable from the UI."""
    return WORKSHOP_ROOT / ".runtime" / f"queue-runner-{_queue_key(dir_str)}.pid"


def _queue_runner_state(dir_str: str | None = None) -> dict:
    """Whether a worker loop is ACTUALLY resident, by probing the runner's flock
    rather than trusting a flag file. This is the distinction the old status
    endpoint missed: the AUTONOMY_DISABLED flag being absent says only that work
    is *permitted*, not that anything is *running* — which is how the UI came to
    report "enabled (Ralph fires every 20m)" while no worker existed at all.

    `running` = a live process holds the lock. `enabled` = the off-switch is
    clear. Work only happens when BOTH are true, so the UI reports them
    separately instead of collapsing them into one misleading label.

    The lock is PER TREE (`queue_runner.sh` derives it from `$DIR`), so the
    answer is always about one named tree. `dir` says which one, because
    "running" with no tree named is the same kind of lie this phase is deleting.
    """
    target = str(dir_str or WORKSHOP_ROOT)
    lock = _queue_lock_path(target)
    running = False
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        fd = None
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)  # we got it => nobody home
        except OSError:
            running = True
        finally:
            os.close(fd)

    pid = None
    try:
        pid = int(_queue_pid_path(target).read_text().strip())
    except (OSError, ValueError):
        pid = None
    # A pidfile outlives a SIGKILLed runner (the EXIT trap never fires), so it is
    # only reported when the lock says someone is actually there.
    if not running:
        pid = None

    last_line = None
    try:
        lines = [ln for ln in QUEUE_RUNNER_LOG.read_text(errors="replace").splitlines() if ln.strip()]
        last_line = lines[-1] if lines else None
    except OSError:
        last_line = None

    return {
        "running": running,
        "pid": pid,
        "enabled": not autonomy_override.is_engaged(),
        "dir": target,
        "lock_path": str(lock),
        "log_path": str(QUEUE_RUNNER_LOG),
        "last_line": last_line,
    }


# ============================================================
# Cron autonomy flag
# ============================================================


@router.post("/agents/autonomy/disable", response_model=am.AutonomyToggled, response_model_exclude_unset=True)
async def autonomy_disable():
    # Writes the ONE switch. This used to touch .runtime/AUTONOMY_DISABLED, a
    # second off-switch that only the shell loop read — so the API and the loop
    # could disagree about whether autonomy was on. That flag is gone; the
    # overlay is what every gate now reads (scripts/autonomy_gate.sh).
    rec = autonomy_override.engage("disabled from the API")
    return {"ok": True, "disabled": True, "at": rec.get("engaged_at")}


@router.post("/agents/autonomy/enable", response_model=am.AutonomyToggled, response_model_exclude_unset=True)
async def autonomy_enable():
    # Clearing this is now SUFFICIENT. Before, an operator could clear the flag
    # here and still have every tick skip, because the JSON overlay was engaged
    # and nothing surfaced that. One switch, one writer.
    autonomy_override.clear()
    return {"ok": True, "disabled": False}


@router.get("/agents/autonomy/status", response_model=am.AutonomyStatus, response_model_exclude_unset=True)
async def autonomy_status():
    # One source. `read()` already reports the union of everything that can hold
    # autonomy off, so the endpoint no longer stats a flag file of its own — that
    # second read is exactly how the UI came to report "enabled" while every
    # tick skipped.
    override = autonomy_override.read()
    disabled = bool(override.get("engaged"))
    return {
        "cron_autonomy": {
            "disabled": disabled,
            "disabled_at": override.get("engaged_at"),
            "reason": override.get("reason"),
        },
        "override": override,
        "queue_runner": _queue_runner_state(),
        "jobs": _jobs_summary(),
    }


# ============================================================
# Model-process concurrency
# ============================================================
# How MANY agent processes may run at once. Distinct from the autonomy switch
# below, which is about WHETHER background work runs at all: this bounds the
# machine, that one bounds the policy.


class ConcurrencyPatch(BaseModel):
    max_turns: int | None = Field(default=None, ge=1, le=64)
    max_helpers: int | None = Field(default=None, ge=1, le=64)


# Top-level, NOT under /agents: `agents.router` is mounted first and owns
# `/agents/{agent_id}`, so a two-segment `/agents/concurrency` would be captured
# as an agent id and 404 instead of reaching this.
@router.get("/concurrency", response_model=am.ConcurrencySnapshot, response_model_exclude_unset=True)
async def get_agent_concurrency():
    """Limits plus what is running right now: {turns:{limit,active,waiting},
    helpers:{...}}. The active counts are the only view the system has of how
    many model processes it is holding."""
    return agent_slots.snapshot()


@router.put("/concurrency", response_model=am.ConcurrencySnapshot, response_model_exclude_unset=True)
async def put_agent_concurrency(body: ConcurrencyPatch):
    """Change how many model processes may run at once. Takes effect on the next
    acquire, with no restart: raising a limit lets work that is already waiting
    start. Nothing in flight is ever cancelled by lowering one, it just stops new
    work starting until the pool drains."""
    agent_slots.update_config(max_turns=body.max_turns, max_helpers=body.max_helpers)
    return agent_slots.snapshot()


# ============================================================
# JSON override overlay (autonomy-model 2026-07-18 §1.2)
# ============================================================
# The overlay is THE off-switch, and it is distinct from an individual agent's
# autonomy field: this is a brake over everything, that one is a setting.


class OverrideEngageIn(BaseModel):
    reason: str | None = None


@router.get("/agents/autonomy/override", response_model=am.AutonomyOverride, response_model_exclude_unset=True)
async def get_autonomy_override():
    """Current override state: {engaged, reason, engaged_at, engaged_by}. File
    absent reads as clear; corrupt / unreadable reads as engaged (fail-closed)."""
    return autonomy_override.read()


@router.post("/agents/autonomy/override", response_model=am.AutonomyOverride, response_model_exclude_unset=True)
async def engage_autonomy_override(body: OverrideEngageIn):
    """Engage the override. Every scope answers `reactive` for background
    dispatch until cleared. Per-agent autonomy fields are NEVER mutated."""
    autonomy_override.OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "engaged": True,
        "reason": (body.reason or "").strip() or None,
        "engaged_at": _now_iso(),
        "engaged_by": "operator",
    }
    import json as _json
    autonomy_override.OVERRIDE_PATH.write_text(_json.dumps(payload, indent=2))
    return autonomy_override.read()


@router.delete("/agents/autonomy/override", response_model=am.OverrideCleared, response_model_exclude_unset=True)
async def clear_autonomy_override():
    """Clear the override. Each agent immediately resumes its configured mode
    without a restore step (the field was never mutated).

    There is now ONE switch to clear. This used to have to unlink a second flag
    file as well, because autonomy was gated by `flag.exists() or
    override.engaged` — two switches read as one truth. Clearing only the
    overlay left the flag holding the loop off while the UI reported the
    override cleared, with nothing on screen naming the thing still doing it.
    That is how the pause set for the 2026-07-18 migration survived until
    2026-08-04: whoever tried to lift it would have seen it "cleared" and still
    off. The flag is deleted and every gate reads this file."""
    autonomy_override.clear()
    return {**autonomy_override.read(), "cleared": ["override"]}


# ============================================================
# Queue worker loop (B0)
# ============================================================
# Start/stop the resident loop that drains the task queue, ON A NAMED TREE.
#
# THESE ENDPOINTS DO NOT TOUCH THE BRAKE, and that is the whole point of the
# split (phase 0, docs/platform-redesign/autonomy-implementation-2026-08-13.md
# §2.2). "Start" used to `autonomy_override.clear()` and then spawn the watchdog
# with NO argument, which the watchdog refuses under ADR 0014 (`DIR="${1:-}"`,
# then `assert_run_target "$DIR"`, exit 2). So one button cleared the global
# brake and started nothing at all. Starting a worker and permitting unattended
# work are two decisions; the brake keeps its own endpoints above
# (POST/DELETE /agents/autonomy/override).


class RunnerStart(BaseModel):
    # REQUIRED, no default. ADR 0014: a run target must be NAMED. A default of
    # $WORKSHOP is exactly how an always-on loop became a standing autonomous
    # writer on the platform's own source.
    dir: str


def _validated_run_target(dir_str: str) -> str:
    """Put `dir` through the SAME gate the watchdog uses, and return it.

    Shelling out to `scripts/dev_mode_gate.sh` rather than reimplementing ADR
    0014 in Python is deliberate: the refusal the operator reads in the UI is
    byte-identical to the one cron writes into queue-runner.log, and there is
    one place to change when the rule moves. `scripts/lib/instance.sh` is
    sourced first for the same reason the watchdog does it, so both halves read
    the same WORKSHOP_DEV_MODE.

    Raises HTTPException(400) carrying the gate's own stderr.
    """
    target = (dir_str or "").strip()
    # A SHAPE CHECK BEFORE THE POLICY GATE. `assert_run_target` answers "is this
    # tree PERMITTED", and it correctly says nothing about whether the path is a
    # working tree at all. But `dir` is free text from a form, and the first
    # thing a sweep does with it is `tasks.next_ready(dir)`, which CREATES
    # `.tasks.json` and `TODO.md` in whatever directory it is handed. So a typo
    # started a sweep against an arbitrary path and left two files there.
    #
    # This is not an allowlist and it is not a permission (single-operator
    # system): it refuses a path that is not a directory, and one that is
    # neither an existing task store nor a git worktree, because neither can be
    # a tree anyone meant to name.
    if not target:
        raise HTTPException(400, "no working tree named")
    p = Path(target)
    if not p.is_dir():
        raise HTTPException(400, f"not a directory: {target}")
    if not ((p / ".tasks.json").exists() or (p / ".git").exists()):
        raise HTTPException(
            400, f"{target} is neither a task store nor a git worktree, so it is "
                 "not a tree to sweep. Starting one here would create a task "
                 "store in it.")
    if not DEV_MODE_GATE.exists():
        raise HTTPException(500, f"gate missing: {DEV_MODE_GATE}")
    script = (
        f'source "{INSTANCE_LIB}"\n'
        f'source "{DEV_MODE_GATE}"\n'
        'assert_run_target "$1" "queue runner"\n'
    )
    proc = subprocess.run(
        ["bash", "-c", script, "queue-runner-start", target],
        cwd=str(WORKSHOP_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        reason = (proc.stderr or "").strip() or "refused: not a permitted run target"
        raise HTTPException(400, reason)
    return target


@router.get("/queue/runner", response_model=am.QueueRunnerState, response_model_exclude_unset=True)
async def queue_runner_status(dir: str | None = None):
    """Real loop state for one tree: is a worker resident, and is work
    permitted. No `dir` reports on the platform repo's lock, which is what the
    Settings summary has always shown."""
    return _queue_runner_state(dir)


@router.get("/queue/runner/targets", response_model=am.RunnerTargets, response_model_exclude_unset=True)
async def queue_runner_targets():
    """The trees a runner may be pointed at, for the scope picker.

    ADR 0014 says a target must be named, so the UI needs a list to name one
    from. The platform repo appears only when developer mode is on: offering it
    and then having the gate refuse it would be another control that lies.
    """
    from wsplatform import dev_mode

    out: list[dict] = []
    if dev_mode.dev_mode_on():
        out.append({
            "key": "development",
            "label": "development (the Workshop itself)",
            "dir": str(WORKSHOP_ROOT),
        })
    try:
        import json as _json
        data = _json.loads(paths.projects_registry().read_text())
    except (OSError, ValueError):
        data = {}
    for slug, raw in sorted((data.get("projects") or {}).items()):
        d = (raw or {}).get("dir")
        if not d or dev_mode.is_self_dir(d):
            continue
        out.append({
            "key": slug,
            "label": raw.get("title") or slug,
            "dir": str(d),
        })
    return {"targets": out}


@router.post("/queue/runner/start", response_model=am.QueueRunnerState, response_model_exclude_unset=True)
async def queue_runner_start(body: RunnerStart):
    # The brake is NOT touched here. See the section comment above.
    target = _validated_run_target(body.dir)
    if not QUEUE_WATCHDOG.exists():
        raise HTTPException(500, f"watchdog missing: {QUEUE_WATCHDOG}")
    subprocess.Popen(
        ["bash", str(QUEUE_WATCHDOG), target],
        cwd=str(WORKSHOP_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    # The watchdog spawns detached; give it a beat so the response reflects the
    # started runner rather than the pre-start state.
    await asyncio.sleep(1.5)
    return _queue_runner_state(target)


@router.post("/queue/runner/stop", response_model=am.QueueRunnerState, response_model_exclude_unset=True)
async def queue_runner_stop(dir: str | None = None):
    """Stop the resident runner + any in-flight sweep. The sweep's own
    lease/reclaim path returns a half-worked ticket to the queue, so stopping
    mid-run loses no tickets.

    This no longer engages the brake either. Stopping the worker you started is
    not a statement about whether unattended work is permitted, and conflating
    them meant a Stop left a global policy flag set that nothing on screen
    connected back to the button that set it.

    `dir` IS REQUIRED IN PRACTICE even though the parameter is optional, and it
    is refused rather than defaulted. Runners are per tree: the lock and the
    pidfile are both keyed on it. A missing `dir` used to resolve to
    WORKSHOP_ROOT, so a caller who had started a runner on `projects/birds` and
    then stopped without naming it killed the PLATFORM repo's runner (or nothing
    at all) and got a "stopped" answer back. Start requires a named tree; a
    per-tree Start with a Stop that guesses is worse than either.
    """
    if not (dir or "").strip():
        raise HTTPException(
            400, "name the tree to stop: runners are per tree, and stopping "
                 "an unnamed one would target whichever tree happens to be the "
                 "default rather than the one you started")
    state = _queue_runner_state(dir)
    pid = state.get("pid")
    if pid:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
    await asyncio.sleep(1.5)
    return _queue_runner_state(dir)


def _jobs_summary(limit: int = 200) -> dict:
    """Recent Job activity for the autonomy overview, sourced from the Job
    store (replaces the old agent-runs.jsonl tick counts)."""
    recent = jobs.read(limit=limit)
    by_status: dict[str, int] = {}
    for r in recent:
        st = r.get("status") or "unknown"
        by_status[st] = by_status.get(st, 0) + 1
    return {
        "recent_count": len(recent),
        "by_status": by_status,
        "running": by_status.get("running", 0),
        "last": recent[0] if recent else None,
    }
