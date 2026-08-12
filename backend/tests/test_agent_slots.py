"""The concurrency gate bounds spawned model processes without ever refusing one.

Each `claude` invocation is a whole OS process, so unbounded fan-out is bounded
only by the host's memory. These tests pin the three properties that make the
gate safe to put in front of the operator's own work:

  1. it CAPS concurrency (the point),
  2. it never rejects and never cancels — a caller waits and then runs,
  3. raising the limit at runtime frees waiters, so the setting takes effect
     without a restart.

Property 2 is the load-bearing one. A gate that dropped or timed out work would
be a wall clock on agentic turns wearing a different hat, and agentic turns
deliberately have neither a step cap nor a wall clock.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from services import agent_slots


@pytest.fixture(autouse=True)
def _isolated_limits(tmp_path, monkeypatch):
    """Point the config at a temp file and clear the env overrides, so these run
    against a known limit rather than whatever this deployment is tuned to."""
    monkeypatch.setattr(agent_slots, "CONFIG_PATH", tmp_path / "agent-concurrency.json")
    monkeypatch.delenv("WORKSHOP_MAX_CONCURRENT_TURNS", raising=False)
    monkeypatch.delenv("WORKSHOP_MAX_CONCURRENT_HELPERS", raising=False)
    # Reset the pools: module state survives between tests in one process.
    agent_slots._turn_active = 0
    agent_slots._turn_waiting = 0
    agent_slots._turn_cond = None
    agent_slots._turn_loop = None
    agent_slots._helper_active = 0
    agent_slots._helper_waiting = 0
    yield


def _peak(trace: list[tuple[str, int]]) -> int:
    depth = peak = 0
    for kind, _ in trace:
        depth += 1 if kind == "enter" else -1
        peak = max(peak, depth)
    return peak


def test_turn_pool_caps_concurrency_and_runs_everything(monkeypatch):
    monkeypatch.setenv("WORKSHOP_MAX_CONCURRENT_TURNS", "2")
    trace: list[tuple[str, int]] = []

    async def work(i: int) -> None:
        async with agent_slots.turn_slot():
            trace.append(("enter", i))
            await asyncio.sleep(0.02)
            trace.append(("exit", i))

    async def main() -> None:
        await asyncio.gather(*(work(i) for i in range(6)))

    asyncio.run(main())

    assert _peak(trace) == 2, "the pool let more than its limit run at once"
    # Nothing was refused: every one of the six eventually ran.
    assert sorted(i for kind, i in trace if kind == "enter") == list(range(6))


def test_a_full_pool_makes_the_next_caller_wait_not_fail(monkeypatch):
    monkeypatch.setenv("WORKSHOP_MAX_CONCURRENT_TURNS", "1")
    started: list[int] = []

    async def main() -> None:
        async with agent_slots.turn_slot():
            # The pool is full. A second caller must block rather than raise.
            waiter = asyncio.create_task(_enter(started))
            await asyncio.sleep(0.05)
            assert started == [], "the gate let a caller through a full pool"
            assert agent_slots.snapshot()["turns"]["waiting"] == 1
        await asyncio.wait_for(waiter, timeout=2)
        assert started == [1], "the waiter never got its slot"

    async def _enter(out: list[int]) -> None:
        async with agent_slots.turn_slot():
            out.append(1)

    asyncio.run(main())


def test_raising_the_limit_releases_waiters_without_a_restart(monkeypatch, tmp_path):
    """The operator moves the slider; work already waiting starts immediately."""
    monkeypatch.setattr(agent_slots, "CONFIG_PATH", tmp_path / "c.json")
    agent_slots.update_config(max_turns=1)
    started: list[int] = []

    async def _enter() -> None:
        async with agent_slots.turn_slot():
            started.append(1)

    async def main() -> None:
        async with agent_slots.turn_slot():
            waiter = asyncio.create_task(_enter())
            await asyncio.sleep(0.05)
            assert started == []
            # Raise the limit while the first slot is still held.
            agent_slots.update_config(max_turns=4)
            # Nudge the condition the way a release does; the waiter re-reads
            # the limit and finds room.
            cond = agent_slots._cond_for_loop()
            async with cond:
                cond.notify_all()
            await asyncio.wait_for(waiter, timeout=2)
        assert started == [1]

    asyncio.run(main())


def test_helper_pool_caps_threads_and_loses_nothing(monkeypatch):
    monkeypatch.setenv("WORKSHOP_MAX_CONCURRENT_HELPERS", "2")
    lock = threading.Lock()
    trace: list[tuple[str, int]] = []

    def work(i: int) -> None:
        with agent_slots.helper_slot():
            with lock:
                trace.append(("enter", i))
            time.sleep(0.02)
            with lock:
                trace.append(("exit", i))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert _peak(trace) == 2
    assert sorted(i for kind, i in trace if kind == "enter") == list(range(6))


def test_a_slot_is_released_when_the_body_raises():
    """A crashed turn must not leak its slot, or the pool drains to zero and the
    system wedges with nothing running."""
    async def main() -> None:
        with pytest.raises(RuntimeError):
            async with agent_slots.turn_slot():
                raise RuntimeError("turn blew up")
        assert agent_slots.snapshot()["turns"]["active"] == 0

    asyncio.run(main())

    with pytest.raises(RuntimeError):
        with agent_slots.helper_slot():
            raise RuntimeError("helper blew up")
    assert agent_slots.snapshot()["helpers"]["active"] == 0


def test_limits_are_clamped_not_obeyed_blindly(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_slots, "CONFIG_PATH", tmp_path / "c.json")
    # Zero would deadlock the system against itself.
    assert agent_slots.update_config(max_turns=0)["max_turns"] == agent_slots._LIMIT_MIN
    assert agent_slots.update_config(max_turns=9999)["max_turns"] == agent_slots._LIMIT_MAX
    # Garbage in the stored file falls back to the default rather than raising.
    (tmp_path / "c.json").write_text("{ not json", encoding="utf-8")
    assert agent_slots.load_config()["max_turns"] == agent_slots._DEFAULT_CONFIG["max_turns"]


def test_env_override_wins_over_the_stored_config(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_slots, "CONFIG_PATH", tmp_path / "c.json")
    agent_slots.update_config(max_turns=3)
    assert agent_slots.limit("max_turns") == 3
    monkeypatch.setenv("WORKSHOP_MAX_CONCURRENT_TURNS", "7")
    assert agent_slots.limit("max_turns") == 7, "a deployment pin must beat the UI value"


def test_config_survives_a_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_slots, "CONFIG_PATH", tmp_path / "c.json")
    agent_slots.update_config(max_turns=5, max_helpers=2)
    on_disk = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert on_disk == {"max_turns": 5, "max_helpers": 2}
    assert agent_slots.load_config() == {"max_turns": 5, "max_helpers": 2}


def test_the_concurrency_routes_are_reachable_and_not_shadowed():
    """`/agents/{agent_id}` is mounted before the autonomy router, so a
    two-segment `/agents/concurrency` would be captured as an agent id and never
    reach this handler. That collision is invisible until someone opens Settings,
    so it is pinned here."""
    import main

    paths_ = {r.path for r in main.app.routes}
    assert "/concurrency" in paths_
    assert "/agents/concurrency" not in paths_, (
        "this path collides with /agents/{agent_id}; keep it top-level"
    )
