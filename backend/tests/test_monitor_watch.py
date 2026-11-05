"""A deliberately stopped service is respawned within a second, not a full pass.

Agents restart an app's api by SIGTERMing it and letting the monitor respawn it.
With only the 15s pass, every such restart sat dead for up to 15s after the
process had already exited. The pid watch between passes closes that gap.
"""
from __future__ import annotations

import asyncio

import pytest

from services import app_monitor


@pytest.fixture()
def one_service(monkeypatch):
    svc = {"name": "api", "status": "running", "pid": 4242}
    state = {"widgets": {"slug": "widgets", "desired": "running",
                         "services": {"api": svc}}}
    alive = {"pid": True}
    monkeypatch.setattr(app_monitor.app_runner, "_STATE", state)
    monkeypatch.setattr(app_monitor.app_runner, "_use_containers", lambda: False)
    monkeypatch.setattr(app_monitor.app_runner, "_alive", lambda pid: alive["pid"])
    return state, svc, alive


def test_a_live_service_is_not_reported(one_service):
    assert app_monitor.any_died() is False


def test_a_dead_running_service_is_reported(one_service):
    _, _, alive = one_service
    alive["pid"] = False
    assert app_monitor.any_died() is True


@pytest.mark.parametrize("status", ["failed", "crashlooping", "starting", "stopped"])
def test_only_running_services_count(one_service, status):
    """A failed or parked service is the full pass's to handle, under its backoff;
    reporting it would run a pass every second and defeat that backoff."""
    _, svc, alive = one_service
    alive["pid"] = False
    svc["status"] = status
    assert app_monitor.any_died() is False


def test_an_app_not_desired_running_is_ignored(one_service):
    state, _, alive = one_service
    alive["pid"] = False
    state["widgets"]["desired"] = "stopped"
    assert app_monitor.any_died() is False


def test_the_wait_ends_early_when_a_service_dies(one_service, monkeypatch):
    _, _, alive = one_service
    monkeypatch.setattr(app_monitor, "WATCH_S", 0.01)
    alive["pid"] = False

    async def run():
        await asyncio.wait_for(app_monitor._wait_for_next_pass(60.0), timeout=1.0)

    asyncio.run(run())
