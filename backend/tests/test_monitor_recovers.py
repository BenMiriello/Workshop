"""A supervisor never gives up permanently on a service that should be running.

`crashlooping` was terminal: `tick` skipped the service on every later pass and
`app_status` only ever downgrades a status, so nothing could lift the flag. The
practical result was that a service whose port had freed, or whose dependency had
come back, stayed dead until a human noticed - one app's api sat parked through
542 restarts and simply read as "the API is down".

Parking now means "stop hammering", not "stop trying".
"""
from __future__ import annotations

import pytest

from services import app_monitor


@pytest.fixture(autouse=True)
def clean_backoff():
    app_monitor._BACKOFF.clear()
    yield
    app_monitor._BACKOFF.clear()


@pytest.fixture()
def parked(monkeypatch):
    """One app, desired-running, with its only service parked as crashlooping."""
    state = {"widgets": {"slug": "widgets", "desired": "running",
                        "services": {"api": {"name": "api", "status": "crashlooping",
                                             "pid": None, "port": 8700}}}}
    monkeypatch.setattr(app_monitor.app_runner, "_STATE", state)
    monkeypatch.setattr(app_monitor.app_runner, "app_status", lambda slug: {
        "slug": slug,
        "services": [dict(s) for s in state[slug]["services"].values()],
    })
    monkeypatch.setattr(app_monitor.app_runner, "_use_containers", lambda: False)
    monkeypatch.setattr(app_monitor.app_runner, "cap_logs", lambda: [])
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(app_monitor.app_runner, "restart_service",
                        lambda slug, name: calls.append((slug, name)))
    return calls


def test_a_parked_service_is_retried(parked):
    result = app_monitor.tick(now=1000.0)
    assert parked == [("widgets", "api")]
    assert {"slug": "widgets", "service": "api", "action": "unparked-retry"} in result["actions"]


def test_it_is_not_retried_again_immediately(parked):
    """The cap exists to stop hammering; the retry must not undo that."""
    app_monitor.tick(now=1000.0)
    app_monitor.tick(now=1000.0 + 1)
    app_monitor.tick(now=1000.0 + 60)
    assert len(parked) == 1, "a parked service was hammered inside its cooldown"


def test_it_is_retried_again_after_the_cooldown(parked):
    app_monitor.tick(now=1000.0)
    app_monitor.tick(now=1000.0 + app_monitor.PARKED_RETRY_S + 1)
    assert len(parked) == 2


def test_a_failed_retry_does_not_kill_the_monitor(parked, monkeypatch):
    """A tick that raises would take down supervision for every other app."""
    def boom(slug, name):
        raise RuntimeError("port still held")
    monkeypatch.setattr(app_monitor.app_runner, "restart_service", boom)
    result = app_monitor.tick(now=1000.0)
    actions = [a for a in result["actions"] if a.get("action") == "unpark-failed"]
    assert actions and "port still held" in actions[0]["error"]


def test_a_stopped_app_is_left_alone(parked, monkeypatch):
    """Parking retries must respect desired state, or a stopped app resurrects."""
    app_monitor.app_runner._STATE["widgets"]["desired"] = "stopped"
    app_monitor.tick(now=1000.0)
    assert parked == []
