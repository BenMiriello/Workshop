"""Concurrency gate for spawned model processes.

Every `claude` invocation is its own OS process holding its own context, so the
cost of running N of them is N whole processes, not N threads. Nothing bounded
that: a fan-out could spawn as many as it liked and the only backstop was the
host running out of memory. This module is the bound.

TWO POOLS, deliberately, because the two kinds of call have nothing in common
except the binary they run:

  - `turn`   — an agentic run. Minutes to hours, holds a working tree, streams.
  - `helper` — a one-shot model call: naming a thread, triaging a ticket,
               compaction, drafting. Seconds to a couple of minutes, no tools.

One shared pool would let a three-hour turn sit in front of the call that names
a chat, and the chat would stay untitled for three hours. Separate pools cost one
extra integer and remove that entirely.

WHAT THE DEFAULTS MEAN. The turn limit is deliberately set well above normal
interactive use: it is a runaway backstop, not a throttle. The operator opening a
fifth chat should never wait on the fourth, and reserving interactive headroom is
already the house rule (see usage_ledger's autonomy_budget_fraction, where
interactive chats are exempt from the budget for the same reason). If this limit
is ever reached in ordinary work it is set too low.

WAITING, NEVER KILLING. A full pool makes the next caller wait for a slot. It
never terminates work in flight and never imposes a deadline on a running turn:
this bounds HOW MANY run at once, not how long any of them may take. A gate that
killed a turn would be a wall clock wearing a different hat, and agentic turns
deliberately have neither a step cap nor a wall clock.

SCOPE. This binds what the backend process spawns, which is every chat and build
turn plus every helper call. Sweeps launched as separate shell processes
(ralph/project_ralph/queue_runner) are already serialized among themselves by
exclusive flocks, one claude each per tree, so they are bounded by a different
mechanism rather than being unbounded.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager, contextmanager

from services import paths

CONFIG_PATH = paths.env_path("WORKSHOP_AGENT_CONCURRENCY", "agent-concurrency.json")

_DEFAULT_CONFIG = {
    # Concurrent agentic runs (chat + build turns). High on purpose: see above.
    "max_turns": 8,
    # Concurrent one-shot helper calls (naming, triage, compaction, drafts).
    "max_helpers": 4,
}

# A limit below 1 would deadlock the system against itself; the upper bound is a
# guard against a fat-fingered value, not a capability claim.
_LIMIT_MIN = 1
_LIMIT_MAX = 64

_ENV_OVERRIDE = {
    "max_turns": "WORKSHOP_MAX_CONCURRENT_TURNS",
    "max_helpers": "WORKSHOP_MAX_CONCURRENT_HELPERS",
}


def _clamp(value: object, key: str) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        n = _DEFAULT_CONFIG[key]
    return max(_LIMIT_MIN, min(_LIMIT_MAX, n))


def load_config() -> dict:
    """The stored config, with every missing key defaulted. A missing or corrupt
    file is not an error: it means nobody has tuned this, so use the defaults."""
    cfg = dict(_DEFAULT_CONFIG)
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            for key in _DEFAULT_CONFIG:
                if key in stored:
                    cfg[key] = stored[key]
    except (OSError, json.JSONDecodeError):
        pass
    return {k: _clamp(v, k) for k, v in cfg.items()}


def limit(key: str) -> int:
    """The effective limit for a pool. Env wins over the stored config so a
    deployment can pin a value the UI cannot then contradict, matching how
    usage_ledger resolves its budget fraction. Read per call, which is what makes
    a change take effect without a restart."""
    raw = os.environ.get(_ENV_OVERRIDE[key])
    if raw is None or str(raw).strip() == "":
        return load_config()[key]
    return _clamp(raw, key)


def update_config(max_turns: int | None = None,
                  max_helpers: int | None = None) -> dict:
    """Persist new limits atomically and return the saved config. Values are
    clamped rather than rejected: the caller is a slider, and a clamp is a better
    answer than a 500. Raising the limit frees waiters on the next release."""
    cfg = load_config()
    if max_turns is not None:
        cfg["max_turns"] = _clamp(max_turns, "max_turns")
    if max_helpers is not None:
        cfg["max_helpers"] = _clamp(max_helpers, "max_helpers")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
    with _helper_cond:
        # A raised helper limit may already have room for somebody waiting.
        _helper_cond.notify_all()
    return cfg


# ---- turn pool (async: the event loop owns every agentic run) ---------------
#
# Plain ints are safe here without a lock: the whole pool is touched only from
# the backend's single event loop, and there is no await between the check and
# the increment.

_turn_active = 0
_turn_waiting = 0
_turn_cond: asyncio.Condition | None = None
_turn_loop: asyncio.AbstractEventLoop | None = None


def _cond_for_loop() -> asyncio.Condition:
    """The turn pool's condition, bound to the running loop.

    An asyncio.Condition belongs to the loop it was created on. The server has
    exactly one loop for its whole life, so this is created once; the rebind is
    for tests, which run each case on a fresh loop.
    """
    global _turn_cond, _turn_loop
    loop = asyncio.get_running_loop()
    if _turn_cond is None or _turn_loop is not loop:
        _turn_cond = asyncio.Condition()
        _turn_loop = loop
    return _turn_cond


@asynccontextmanager
async def turn_slot():
    """Hold a slot for an agentic run. Waits when the pool is full, for as long
    as it takes. Acquired BEFORE the process is spawned, so a waiting run costs
    nothing but a coroutine, and released only once the run is finished."""
    global _turn_active, _turn_waiting
    cond = _cond_for_loop()
    async with cond:
        if _turn_active >= limit("max_turns"):
            _turn_waiting += 1
            try:
                # Re-read the limit each pass: raising it must wake waiters, and
                # a spurious wake must not let a caller through.
                await cond.wait_for(lambda: _turn_active < limit("max_turns"))
            finally:
                _turn_waiting -= 1
        _turn_active += 1
    try:
        yield
    finally:
        async with cond:
            _turn_active -= 1
            cond.notify_all()


# ---- helper pool (sync: one-shot calls run in the threadpool) ---------------

_helper_cond = threading.Condition()
_helper_active = 0
_helper_waiting = 0


@contextmanager
def helper_slot():
    """Hold a slot for a one-shot model call. Blocks the calling thread, which is
    correct here: these callers are already running in a worker thread, never on
    the event loop."""
    global _helper_active, _helper_waiting
    with _helper_cond:
        if _helper_active >= limit("max_helpers"):
            _helper_waiting += 1
            try:
                _helper_cond.wait_for(lambda: _helper_active < limit("max_helpers"))
            finally:
                _helper_waiting -= 1
        _helper_active += 1
    try:
        yield
    finally:
        with _helper_cond:
            _helper_active -= 1
            _helper_cond.notify_all()


def snapshot() -> dict:
    """Live pool state, for the API and the Settings screen. Reports what is
    actually running, which nothing else in the system could answer before."""
    with _helper_cond:
        helper_active, helper_waiting = _helper_active, _helper_waiting
    return {
        "turns": {
            "limit": limit("max_turns"),
            "active": _turn_active,
            "waiting": _turn_waiting,
        },
        "helpers": {
            "limit": limit("max_helpers"),
            "active": helper_active,
            "waiting": helper_waiting,
        },
    }
