"""The guard ends repeated calls and bounds a turn, without asking a model."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts" / "local_turn_guard.py"
spec = importlib.util.spec_from_file_location("local_turn_guard", PATH)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setattr(guard.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(guard, "MAX_CALLS", 60)


def call(monkeypatch, turn, command="ls", stdin=None):
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": command}, "session_id": "s"}
    monkeypatch.setenv("WORKSHOP_GUARD_TURN", turn)
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(
        stdin if stdin is not None else json.dumps(event)))
    return guard.main()


def test_the_third_identical_call_is_refused_with_a_way_out(monkeypatch, capsys):
    assert call(monkeypatch, "t1") == 0
    assert call(monkeypatch, "t1") == 0
    assert call(monkeypatch, "t1") == 2
    assert "different approach" in capsys.readouterr().err


def test_different_calls_are_never_a_loop(monkeypatch):
    for i in range(10):
        assert call(monkeypatch, "t2", command=f"ls {i}") == 0


def test_a_turn_is_bounded_and_told_to_report(monkeypatch, capsys):
    monkeypatch.setattr(guard, "MAX_CALLS", 3)
    for i in range(3):
        assert call(monkeypatch, "t3", command=f"ls {i}") == 0
    assert call(monkeypatch, "t3", command="ls 99") == 2
    assert "Write your report now" in capsys.readouterr().err


def test_turns_do_not_share_a_count(monkeypatch):
    monkeypatch.setattr(guard, "MAX_CALLS", 3)
    for i in range(3):
        call(monkeypatch, "t4", command=f"ls {i}")
    assert call(monkeypatch, "t5") == 0


def test_a_guard_that_cannot_read_its_input_lets_the_call_through(monkeypatch):
    assert call(monkeypatch, "t6", stdin="not json") == 0


def answered(monkeypatch, turn, response, command="read tab"):
    event = {"hook_event_name": "PostToolUse", "tool_name": "Bash",
             "tool_input": {"command": command}, "tool_response": response, "session_id": "s"}
    monkeypatch.setenv("WORKSHOP_GUARD_TURN", turn)
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(json.dumps(event)))
    return guard.main()


def test_a_repeat_whose_answers_changed_is_not_a_loop(monkeypatch):
    for page in ("search form", "results", "page two", "a gallery"):
        assert call(monkeypatch, "t7", command="read tab") == 0
        assert answered(monkeypatch, "t7", page) == 0


def test_a_repeat_with_the_same_answer_is_still_refused(monkeypatch, capsys):
    for _ in range(2):
        assert call(monkeypatch, "t8", command="read tab") == 0
        answered(monkeypatch, "t8", "the same check page")
    assert call(monkeypatch, "t8", command="read tab") == 2
    assert "different approach" in capsys.readouterr().err


def test_a_call_retried_after_another_call_changed_something_is_not_a_loop(monkeypatch):
    for n in range(3):
        assert call(monkeypatch, "t9", command="close stage") == (2 if n == 2 else 0)
    assert call(monkeypatch, "t9", command="attach artifact") == 0
    answered(monkeypatch, "t9", "attached", command="attach artifact")
    assert call(monkeypatch, "t9", command="close stage") == 0


def test_a_failing_call_alternating_with_an_unchanged_answer_is_still_refused(monkeypatch):
    call(monkeypatch, "t10", command="read stage")
    answered(monkeypatch, "t10", "the same stage", command="read stage")
    for n in range(3):
        assert call(monkeypatch, "t10", command="close stage") == (2 if n == 2 else 0)
        call(monkeypatch, "t10", command="read stage")
        answered(monkeypatch, "t10", "the same stage", command="read stage")


def test_the_count_starts_again_only_from_the_change(monkeypatch, capsys):
    call(monkeypatch, "t11", command="close stage")
    call(monkeypatch, "t11", command="attach artifact")
    answered(monkeypatch, "t11", "attached", command="attach artifact")
    for _ in range(2):
        assert call(monkeypatch, "t11", command="close stage") == 0
    assert call(monkeypatch, "t11", command="close stage") == 2
    assert "3 times" in capsys.readouterr().err
