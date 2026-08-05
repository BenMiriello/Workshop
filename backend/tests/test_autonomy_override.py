"""The autonomy off-switch, and the fact that there are two of them.

A kill switch is only allowed to move in one direction under refactoring: it may
come to report "engaged" more often, never less. These tests pin that direction.
The specific hazard is real and was live in this repo — the JSON overlay is what
the API reads, the AUTONOMY_DISABLED flag file is what actually gates the shell
worker loop, and with the two disagreeing the UI could report autonomy clear
while every tick skipped. `read()` reports their union so one call answers for
the whole system.
"""
import json

import pytest

from services import autonomy_override as ao


@pytest.fixture
def switches(tmp_path, monkeypatch):
    """Both switches, pointed at a sandbox so the real ones are never touched."""
    rt = tmp_path / ".runtime"
    rt.mkdir()
    monkeypatch.setattr(ao, "OVERRIDE_PATH", rt / "autonomy-override.json")
    monkeypatch.setattr(ao, "LEGACY_FLAG", rt / "AUTONOMY_DISABLED")
    return ao.OVERRIDE_PATH, ao.LEGACY_FLAG


def test_both_clear_is_the_only_way_to_be_clear(switches):
    assert ao.is_engaged() is False


def test_json_engaged_engages(switches):
    override, _ = switches
    override.write_text(json.dumps({"engaged": True, "reason": "operator"}))
    assert ao.is_engaged() is True
    assert ao.read()["reason"] == "operator"


def test_legacy_flag_alone_engages(switches):
    """The case that was broken: JSON says clear, flag file still gates the
    worker loop. Reporting 'clear' here is how a toggle comes to do nothing."""
    _, flag = switches
    flag.touch()
    assert ao.is_engaged() is True
    assert "legacy" in ao.read()["reason"]


def test_legacy_flag_beats_an_explicitly_clear_json(switches):
    override, flag = switches
    override.write_text(json.dumps({"engaged": False}))
    flag.touch()
    assert ao.is_engaged() is True


def test_json_reason_wins_when_both_are_set(switches):
    """When both switches are on, the operator's stated reason is the useful
    one; the flag file carries no reason at all."""
    override, flag = switches
    override.write_text(json.dumps({"engaged": True, "reason": "migration in flight"}))
    flag.touch()
    assert ao.read()["reason"] == "migration in flight"


def test_corrupt_json_fails_closed(switches):
    override, _ = switches
    override.write_text("{not json")
    assert ao.is_engaged() is True


def test_non_object_json_fails_closed(switches):
    override, _ = switches
    override.write_text("[1, 2, 3]")
    assert ao.is_engaged() is True


def test_clearing_both_switches_restores_autonomy(switches):
    """The whole point of a union read: it must still be possible to turn
    autonomy ON, or the switch is not a switch."""
    override, flag = switches
    override.write_text(json.dumps({"engaged": True}))
    flag.touch()
    assert ao.is_engaged() is True
    flag.unlink()
    override.write_text(json.dumps({"engaged": False}))
    assert ao.is_engaged() is False
