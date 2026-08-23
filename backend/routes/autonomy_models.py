"""Response models for `routes/autonomy.py` (ADR 0023 clause 6).

A SIBLING MODULE so `autonomy.py` stays under the `check_module_layout.py` big
-file threshold (800 lines) without these response models pushing it over.

Read off the emitters -- `services/autonomy_override.py`, `agent_slots.py`,
`jobs.py`, and this router's own `_queue_runner_state` / `_jobs_summary`
helpers. FastAPI FILTERS a return value through the declared model and silently
drops any key it does not name, so an under-declared field here is a live API
field that disappears.

Clause 4 (RFC3339, `*_at`) is not applied: `engaged_at`, `disabled_at` and
`expires_at` are the names already on the wire and in the frontend.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from routes.api_models import Job


# ------------------------------------------------------------------- override

class AutonomyOverride(BaseModel):
    """THE off-switch, and the only one. An absent file reads as clear; a
    corrupt or unreadable one reads as ENGAGED (fail-closed), with `reason`
    naming which failure it was and `engaged_by` set to "system" rather than to
    an operator who did not do it.

    Distinct from an individual agent's autonomy field: this is a brake over
    everything, that one is a setting, and engaging this never mutates it."""

    engaged: bool
    reason: str | None = None
    engaged_at: str | None = None
    engaged_by: str | None = None


class OverrideCleared(AutonomyOverride):
    """`cleared` names the switches this call actually released. It is a list
    because there used to be two of them read as one truth -- the overlay and a
    legacy flag file -- and clearing only one left the loop off while the UI
    reported it cleared. One switch now, and the field says so explicitly."""

    cleared: list[str]


class AutonomyToggled(BaseModel):
    """The enable/disable pair. `at` is present only on disable, since there is
    no instant to report for clearing."""

    ok: bool
    disabled: bool
    at: str | None = None


# --------------------------------------------------------------- queue runner

class QueueRunnerState(BaseModel):
    """Real loop state FOR ONE TREE. Runners are per tree: both the lock and the
    pidfile are keyed on it.

    `pid` is reported only when the lock says somebody is actually there -- a
    pidfile outlives a SIGKILLed runner because its EXIT trap never fires, so
    the pid alone is not evidence. `enabled` is the brake, and it is deliberately
    independent of `running`: starting or stopping a worker says nothing about
    whether unattended work is permitted."""

    running: bool
    pid: int | None = None
    enabled: bool
    dir: str | None = None
    lock_path: str
    log_path: str
    last_line: str | None = None


class RunnerTarget(BaseModel):
    """A tree a runner may be pointed at. The platform repo appears only in
    developer mode: offering it and then having the gate refuse it would be
    another control that lies."""

    key: str
    label: str
    dir: str
    # The SCOPE KEY for this tree (`development`, `project:<slug>`). Added when
    # the enumeration moved to `services/makers.runnable_scopes`: every other
    # control in this stack -- the arm level, the presence channel, the maker
    # agent -- is keyed by the scope, and a caller holding only a path had to
    # re-derive it, which is the kind of re-derivation that goes wrong once.
    scope: str | None = None


class RunnerTargets(BaseModel):
    targets: list[RunnerTarget]


# ------------------------------------------------------------------- overview

class CronAutonomy(BaseModel):
    """The same override state under the names the Settings screen reads. One
    source: a second stat of a flag file of its own is exactly how the UI came
    to report "enabled" while every tick skipped."""

    disabled: bool
    disabled_at: str | None = None
    reason: str | None = None


class JobsSummary(BaseModel):
    """Recent Job activity, over the last 200 records. `by_status` is a tally
    keyed by status; `last` is the newest record in full."""

    recent_count: int
    by_status: dict[str, int]
    running: int
    last: Job | None = None


class AutonomyStatus(BaseModel):
    cron_autonomy: CronAutonomy
    override: AutonomyOverride
    queue_runner: QueueRunnerState
    jobs: JobsSummary


# ---------------------------------------------------------------- concurrency

class SlotPool(BaseModel):
    """One pool's limit and what is actually in it. The active counts are the
    only view the system has of how many model processes it is holding."""

    limit: int
    active: int
    waiting: int


class ConcurrencySnapshot(BaseModel):
    """How MANY agent processes may run at once, which is a different question
    from WHETHER background work runs at all: this bounds the machine, the
    override bounds the policy. A change takes effect on the next acquire with
    no restart, and lowering a limit never cancels anything in flight."""

    turns: SlotPool
    helpers: SlotPool
