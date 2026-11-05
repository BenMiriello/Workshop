"""scripts/app_chat_summary.py and the turn-window half of scripts/app_chat_run.py.

The fixture is one CLI session transcript holding an earlier turn, the turn
under test, and a later turn, plus the bookkeeping records the CLI interleaves
(queue operations, latches, a sidechain, an unparseable line). Only the middle
turn may be counted.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_FIXTURE = Path(__file__).parent / "fixtures" / "app_chat_transcript.jsonl"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


acs = _load("app_chat_summary")
runner = _load("app_chat_run")

START = "2026-10-01T10:00:00+00:00"
END = "2026-10-01T10:00:31+00:00"


def _turn_summary() -> dict:
    records = acs.load_records(_FIXTURE)
    return acs.summarize(acs.in_window(records, acs.parse_ts(START), acs.parse_ts(END)))


def test_only_the_turns_own_requests_are_counted():
    s = _turn_summary()
    assert s["requests"] == 3
    assert s["models"] == ["local-model"]
    assert s["output_tokens"] == 90


def test_input_counts_fresh_and_cached_tokens_once_per_request():
    s = _turn_summary()
    assert s["input_tokens"] == 5200
    assert s["input_tokens_cached"] == 1000


def test_tool_calls_and_errors_are_named():
    s = _turn_summary()
    assert s["tool_calls"] == {"mcp__app__search_items": 2}
    assert s["tool_errors"] == [{"tool": "mcp__app__search_items",
                                 "error": "Error executing tool search_items: missing 'limit'"}]


def test_compactions_thinking_and_visible_text():
    s = _turn_summary()
    assert s["compactions"] == [{"trigger": "auto", "preTokens": 32000,
                                 "postTokens": 2800, "durationMs": 5000}]
    assert s["thinking_chars"] == len("I should search.") + len("Now answer.")
    assert s["text_chars"] == len("Your three most recent items: A, B, C.")
    assert s["last_text"] == "Your three most recent items: A, B, C."
    assert s["stop_reason"] == "end_turn"


def test_an_open_ended_window_keeps_everything_after_the_start():
    records = acs.load_records(_FIXTURE)
    s = acs.summarize(acs.in_window(records, acs.parse_ts(START), None))
    assert s["requests"] == 4


def test_find_transcript_searches_every_home(tmp_path):
    session = "0f0f0f0f-1111-2222-3333-444455556666"
    second = tmp_path / "turn-user"
    target = second / ".claude" / "projects" / "-some-cwd" / f"{session}.jsonl"
    target.parent.mkdir(parents=True)
    shutil.copy(_FIXTURE, target)
    homes = [str(tmp_path / "empty"), str(second)]
    assert acs.find_transcript(session, homes) == target
    assert acs.find_transcript("no-such-session", homes) is None


def test_turn_window_prefers_the_replys_own_stamps():
    run = {"turn_id": "t-1", "state": "finished", "events": [
        {"kind": "turn", "turn_id": "t-1", "state": "started", "ts": "2026-10-01T09:59:59+00:00"},
        {"kind": "message", "role": "assistant", "content": "done",
         "meta": {"turn_id": "t-1", "started_at": START, "ended_at": END}},
        {"kind": "turn", "turn_id": "t-1", "state": "finished", "ts": END},
    ]}
    reply, start, end = runner.turn_window(run)
    assert reply["content"] == "done"
    assert (start, end) == (START, END)


def test_turn_window_of_an_unfinished_turn_has_no_end():
    run = {"turn_id": "t-2", "state": "timeout", "events": [
        {"kind": "turn", "turn_id": "t-2", "state": "started", "ts": START},
    ]}
    reply, start, end = runner.turn_window(run)
    assert reply == {}
    assert (start, end) == (START, None)
