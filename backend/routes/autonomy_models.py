"""Response models for `routes/autonomy.py` (ADR 0023 clause 6).

A SIBLING MODULE because `autonomy.py` is 619 lines and
`check_module_layout.py` flags any backend file over 800 as a big file against
a pinned count. Inlining these would land it at roughly 810, creating a new big
file and failing that ratchet for an unrelated reason.

Read off the emitters -- `services/autonomy_override.py`, `agent_slots.py`,
`gpubroker_client.py`, `jobs.py`, and this router's own `_queue_runner_state` /
`_jobs_summary` / `_nvidia_smi_snapshot` helpers. FastAPI FILTERS a return
value through the declared model and silently drops any key it does not name,
so an under-declared field here is a live API field that disappears.

TWO EXTERNAL SURFACES ARE LEFT OPEN, and both are somebody else's schema:
`gpubrokerd`'s status, lease and priority bodies are spliced in wholesale by
`gpubroker_client`, and Ollama's `/api/ps` model records come from Ollama.
Enumerating another process's keys here would drop whatever it adds next, so
those models set `extra="allow"` and name only the fields this repo reads.

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


# --------------------------------------------------------------- ollama + gpu

class GpuSnapshot(BaseModel):
    """One `nvidia-smi` sample of the first GPU. `available: false` with an
    `error` is what a host with no nvidia-smi, or unparseable output, looks
    like -- distinct from a card reporting zeros."""

    available: bool
    error: str | None = None
    memory_used_mib: int | None = None
    memory_free_mib: int | None = None
    memory_total_mib: int | None = None
    utilization_pct: int | None = None


class LoadedModel(BaseModel):
    """One resident Ollama model, projected from `/api/ps`."""

    name: str | None = None
    size_vram_bytes: int | None = None
    expires_at: str | None = None


class OllamaStatus(BaseModel):
    """`ollama_up: false` comes back with an empty model list rather than an
    error, so the Settings screen renders a reachable-but-down state."""

    ollama_up: bool
    loaded_models: list[LoadedModel]
    gpu: GpuSnapshot


class UnloadResult(BaseModel):
    """One model's eviction. `ok` is the HTTP verdict from Ollama, so a model
    that refused carries a `status`; a transport failure carries an `error`."""

    model_config = ConfigDict(protected_namespaces=())

    model: str
    ok: bool
    status: int | None = None
    error: str | None = None


class OllamaUnloaded(BaseModel):
    """The post-unload GPU snapshot rides along so the UI can show the freed
    memory immediately. `note` appears only when nothing was resident, which is
    a success with nothing to do rather than a failure."""

    ok: bool
    unloaded: list[UnloadResult]
    gpu: GpuSnapshot
    note: str | None = None


# ----------------------------------------------------------------- gpu broker

class BrokerStatus(BaseModel):
    """`gpubrokerd`'s own status body with `available` and this app's leases
    spliced on. The broker is ADDITIVE: when it is down this is just
    `{available: false}` and Workshop's own off-switch remains the authoritative
    control plane. Its body is the broker's schema, not ours, hence extra."""

    model_config = ConfigDict(extra="allow")

    available: bool
    my_leases: list[dict[str, Any]] | None = None


class BrokerRegistered(BaseModel):
    """Idempotent. Called on backend startup and exposed here so the operator
    can re-register after the broker restarts."""

    registered: bool


class BrokerLease(BaseModel):
    """A lease as `gpubrokerd` returns it. Left open for the same reason
    `BrokerStatus` is."""

    model_config = ConfigDict(extra="allow")


class BrokerReleased(BaseModel):
    released_count: int
