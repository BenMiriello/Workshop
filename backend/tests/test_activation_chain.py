"""Why an agent does or does not run, as a chain the page can render.

The failure this replaces: an agent that had never run showed empty panels and no
reason, so a correctly-idle agent was indistinguishable from a broken one. "Is
autonomy on" is a conjunction across a global brake, the agent's declared mode,
and whether anything can start it, and the useful answer is which link is open.

These tests pin the ORDER of blame. Naming the wrong blocking link is worse than
naming none, because the operator would go and change the wrong thing.
"""
import pytest

from routes.agents import _activation_chain
from services import autonomy_override as ao


@pytest.fixture
def brake_clear(tmp_path, monkeypatch):
    """No global brake, so the agent's own declaration decides."""
    rt = tmp_path / ".runtime"
    rt.mkdir()
    monkeypatch.setattr(ao, "OVERRIDE_PATH", rt / "autonomy-override.json")
    monkeypatch.setattr(ao, "LEGACY_FLAG", rt / "AUTONOMY_DISABLED")


@pytest.fixture
def brake_on(tmp_path, monkeypatch):
    rt = tmp_path / ".runtime"
    rt.mkdir()
    (rt / "autonomy-override.json").write_text('{"engaged": true, "reason": "migration"}')
    monkeypatch.setattr(ao, "OVERRIDE_PATH", rt / "autonomy-override.json")
    monkeypatch.setattr(ao, "LEGACY_FLAG", rt / "AUTONOMY_DISABLED")


def _agent(**over):
    a = {"slug": "probe", "autonomy": "reactive", "triggers": [], "last_active": None}
    a.update(over)
    return a


def test_fully_enabled_agent_runs_unattended(brake_clear):
    out = _activation_chain(_agent(autonomy="autonomous", triggers=[{"kind": "cron"}]))
    assert out["runs_unattended"] is True
    assert out["blocked_by"] is None
    assert all(l["ok"] for l in out["links"])


def test_the_brake_is_blamed_first(brake_on):
    """Even a fully-configured agent is blocked by the brake, and the brake must
    be named: telling the operator to change the agent would not help."""
    out = _activation_chain(_agent(autonomy="autonomous", triggers=[{"kind": "cron"}]))
    assert out["blocked_by"] == "brake"
    assert out["runs_unattended"] is False


def test_reactive_is_blamed_when_the_brake_is_clear(brake_clear):
    out = _activation_chain(_agent(autonomy="reactive", triggers=[{"kind": "cron"}]))
    assert out["blocked_by"] == "autonomy"


def test_missing_trigger_is_blamed_last(brake_clear):
    out = _activation_chain(_agent(autonomy="supervised", triggers=[]))
    assert out["blocked_by"] == "trigger"
    assert "no triggers declared" in out["links"][2]["detail"]


def test_manual_only_triggers_cannot_start_anything(brake_clear):
    """A manual trigger is a way for a PERSON to start it, not a way for it to
    start itself. Counting it would report an agent as unattended when nothing
    will ever fire it."""
    out = _activation_chain(_agent(autonomy="autonomous", triggers=[{"kind": "manual"}]))
    assert out["blocked_by"] == "trigger"
    assert "manual invocation only" in out["links"][2]["detail"]


def test_supervised_counts_as_may_run(brake_clear):
    out = _activation_chain(_agent(autonomy="supervised", triggers=[{"kind": "event"}]))
    assert out["runs_unattended"] is True


def test_never_run_is_reported(brake_clear):
    out = _activation_chain(_agent(last_active=None))
    assert out["ever_ran"] is False


def test_having_run_is_reported(brake_clear):
    out = _activation_chain(_agent(last_active="2026-07-14"))
    assert out["ever_ran"] is True
    assert out["last_active"] == "2026-07-14"


def test_an_agent_with_no_declaration_defaults_to_reactive(brake_clear):
    """Absent autonomy must read as reactive, never as permitted."""
    out = _activation_chain({"slug": "bare"})
    assert out["links"][1]["detail"] == "reactive"
    assert out["runs_unattended"] is False


def test_chain_order_is_stable():
    """The page renders these in order and the copy under it keys off the ids."""
    out = _activation_chain(_agent())
    assert [l["id"] for l in out["links"]] == ["brake", "autonomy", "trigger"]
