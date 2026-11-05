"""A turn on a local model whose requests keep failing ends, and says why.

On 2026-10-03 a local model was unloaded under a turn and every request it
sent failed for two hours. The CLI retried each one without a word, the chat
showed the turn working with nothing under it, and only Stop ended it. These
pin the two bounds that now end such a turn, and the CLI's own retry count.
"""
import asyncio
import json
import types

import pytest

from services import (build_streaming, chat_store, llm_providers,
                      local_model_provider, model_failures, ownership)


def _retry(status=502, error="server_error") -> str:
    return json.dumps({"type": "system", "subtype": "api_retry", "attempt": 1,
                       "max_retries": 10, "retry_delay_ms": 500,
                       "error_status": status, "error": error})


def _text(s: str) -> str:
    return json.dumps({"type": "assistant",
                       "message": {"content": [{"type": "text", "text": s}]}})


def _tool() -> str:
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "tu-1", "name": "Bash", "input": {}}]}})


def _stop(cut: bool = False) -> str:
    """The `message_stop` the CLI forwards: the server's own, or the one it
    writes itself to close a response it gave up on."""
    ev = {"type": "stream_event", "event": {"type": "message_stop"}}
    if cut:
        ev["abandoned_blocks"] = {"api_message_id": "msg_1", "from_block_index": 0}
    return json.dumps(ev)


def _thinking(s: str = "hmm") -> str:
    return json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "thinking_delta", "thinking": s}}})


def _feed(streak, lines, now=0.0):
    for line in lines:
        kind, payload = build_streaming._parse_stream_event(line)
        streak.observe(kind, payload, now)


class TestTheStreak:
    def test_a_run_of_failures_ends_it_with_the_reason(self):
        streak = model_failures.Streak()
        for _ in range(model_failures.MAX_IN_A_ROW - 1):
            streak.failed({"error_status": 502, "error": "server_error"}, 0.0)
        assert streak.verdict(1.0) is None
        streak.failed({"error_status": 502, "error": "server_error"}, 1.0)
        why = streak.verdict(1.0)
        assert why.startswith("Stopped: the model kept failing")
        assert f"{model_failures.MAX_IN_A_ROW} requests in a row" in why
        assert "502 server_error" in why

    def test_a_finished_request_starts_the_count_again(self):
        streak = model_failures.Streak()
        for _ in range(model_failures.MAX_IN_A_ROW - 1):
            streak.failed({"error_status": 500}, 0.0)
        streak.progressed()
        streak.failed({"error_status": 500}, 0.0)
        assert streak.verdict(0.0) is None

    def test_a_slow_failure_ends_it_after_the_cap(self):
        streak = model_failures.Streak()
        streak.failed({"no_response": {"waited_ms": 600000}}, 100.0)
        assert streak.verdict(100.0 + model_failures.FAIL_CAP_S - 1) is None
        why = streak.verdict(100.0 + model_failures.FAIL_CAP_S)
        assert "no request succeeded" in why and "no response" in why

    def test_no_failure_is_never_a_verdict(self):
        assert model_failures.Streak().verdict(1e12) is None

    def test_a_request_cut_off_after_it_started_is_a_failure(self):
        streak = model_failures.Streak()
        for _ in range(model_failures.MAX_IN_A_ROW):
            _feed(streak, [_thinking(), _text("so far"), _stop(cut=True),
                           _retry(None, "unknown")])
        why = streak.verdict(0.0)
        assert f"{model_failures.MAX_IN_A_ROW} requests in a row" in why
        assert "cut off after it had started" in why

    def test_the_clis_retry_after_a_cut_is_the_same_failure(self):
        streak = model_failures.Streak()
        _feed(streak, [_thinking(), _stop(cut=True), _retry(None, "unknown")])
        assert streak.count == 1

    def test_only_a_finished_request_starts_the_count_again(self):
        streak = model_failures.Streak()
        _feed(streak, [_retry(), _thinking(), _text("partial")])
        assert streak.count == 1
        _feed(streak, [_stop()])
        assert streak.count == 0

    def test_a_whole_message_from_the_non_streamed_retry_finishes_it(self):
        streak = model_failures.Streak()
        _feed(streak, [_stop(cut=True)])
        whole = json.dumps({"type": "assistant", "message": {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "whole answer"}]}})
        _feed(streak, [whole])
        assert streak.count == 0

    def test_a_request_that_is_streaming_is_not_timed_out(self):
        streak = model_failures.Streak()
        _feed(streak, [_retry()], now=100.0)
        _feed(streak, [_thinking()], now=101.0)
        assert streak.verdict(100.0 + model_failures.FAIL_CAP_S * 2) is None


def test_the_stream_event_keeps_what_failed():
    kind, payload = build_streaming._parse_stream_event(_retry(503, "overloaded"))
    assert kind == "meta" and payload["subtype"] == "api_retry"
    assert (payload["error_status"], payload["error"]) == (503, "overloaded")


def test_only_a_provider_that_redirects_the_cli_is_bounded(monkeypatch):
    local = local_model_provider.LocalHttpProvider("http://127.0.0.1:1")
    monkeypatch.setattr(llm_providers, "provider_for_model",
                        lambda m: local if m == "w" else None)
    assert llm_providers.redirects_cli("w") is True
    assert llm_providers.redirects_cli("a-cloud-model") is False


def test_a_local_turn_lowers_the_clis_own_retries(monkeypatch):
    provider = local_model_provider.LocalHttpProvider("http://127.0.0.1:1")
    monkeypatch.setattr(provider, "models", lambda: [types.SimpleNamespace(
        id="w", capabilities=types.SimpleNamespace(context_tokens=0))])
    monkeypatch.delenv("WORKSHOP_BACKEND_PORT", raising=False)
    env = provider.cli_env("w", {})
    assert env["CLAUDE_CODE_MAX_RETRIES"] == str(
        local_model_provider.LOCAL_MAX_RETRIES)
    assert int(env["CLAUDE_CODE_MAX_RETRIES"]) < 10


def test_a_turn_through_the_hop_retries_long_enough_to_ride_out_a_restart(
        monkeypatch):
    provider = local_model_provider.LocalHttpProvider("http://127.0.0.1:1")
    monkeypatch.setattr(provider, "models", lambda: [])
    monkeypatch.setenv("WORKSHOP_BACKEND_PORT", "8999")
    env = provider.cli_env("w", {})
    assert env["CLAUDE_CODE_MAX_RETRIES"] == str(
        local_model_provider.HOP_MAX_RETRIES)
    assert int(env["CLAUDE_CODE_MAX_RETRIES"]) < 10


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    monkeypatch.setattr(build_streaming, "TURNS_DIR", tmp_path / "turns")
    ownership.set_current_owner(1)
    from services import chat_naming
    monkeypatch.setattr(chat_naming, "schedule", lambda *a, **kw: None)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()
        build_streaming.TURNS.clear()


class FakeProc:
    def __init__(self):
        self.pid = 4242
        self.returncode = None


def _drive(thread_id: str, lines: list[str], monkeypatch, *, local=True,
           die_after: float = 0.4):
    """The real drive loop over a journal already holding `lines`, for a
    process that dies on its own after `die_after` unless killed first."""
    monkeypatch.setattr(llm_providers, "redirects_cli", lambda m: local)
    turn_id = "run-failing"
    chat_store.begin_turn(thread_id, turn_id, model="m")
    turn = build_streaming.Turn(id=turn_id, thread_id=thread_id, agent_slug="a")
    turn.spawned = True
    build_streaming.TURNS[turn_id] = turn
    d = build_streaming._journal_dir(turn_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "stream.jsonl").write_text("".join(line + "\n" for line in lines))
    meta = {"turn_id": turn_id, "thread_id": thread_id, "agent_slug": "a",
            "cwd": "", "model": "m", "session_id": None, "resume": False,
            "speaker": None, "owner_id": 1, "prompt": "go", "timeout_s": 0,
            "pid": 4242, "status": "running", "started": 0.0}
    proc = FakeProc()
    turn.proc, turn.pid = proc, proc.pid
    killed = []

    def kill(pid, p=None, **_kw):
        killed.append(pid)
        proc.returncode = -15
    monkeypatch.setattr(build_streaming, "_kill_turn_proc", kill)

    async def go():
        driving = asyncio.create_task(
            build_streaming._drive_turn(turn, meta, proc=proc))
        await asyncio.sleep(die_after)
        if proc.returncode is None:
            proc.returncode = 0
        await asyncio.wait_for(driving, 5)
    asyncio.run(go())
    return chat_store.get_thread(thread_id)["messages"][-1], killed


def test_a_local_turn_that_keeps_failing_ends_with_the_reason(thread, monkeypatch):
    lines = [_retry() for _ in range(model_failures.MAX_IN_A_ROW)]
    last, killed = _drive(thread["id"], lines, monkeypatch)
    assert killed, "the loop ended the process itself"
    assert "the model kept failing" in last["content"]
    assert last["meta"].get("error")


def test_what_it_wrote_before_failing_is_kept_above_the_reason(thread, monkeypatch):
    lines = [_text("half an answer")] + [
        _retry() for _ in range(model_failures.MAX_IN_A_ROW)]
    last, killed = _drive(thread["id"], lines, monkeypatch)
    assert killed
    assert last["content"].startswith("half an answer")
    assert "the model kept failing" in last["content"]


def test_failures_with_a_finished_request_between_them_do_not_end_it(
        thread, monkeypatch):
    n = model_failures.MAX_IN_A_ROW - 1
    lines = [_retry()] * n + [_text("still here"), _stop()] + [_retry()] * n
    last, killed = _drive(thread["id"], lines, monkeypatch)
    assert not killed
    assert "kept failing" not in last["content"]


def test_a_turn_cut_off_each_time_it_starts_ends_with_the_reason(
        thread, monkeypatch):
    lines = [_thinking(), _text("reading"), _stop(cut=True),
             _retry(None, "unknown")] * model_failures.MAX_IN_A_ROW
    last, killed = _drive(thread["id"], lines, monkeypatch)
    assert killed
    assert "the model kept failing" in last["content"]


def test_a_turn_ended_by_failures_keeps_all_it_said(thread, monkeypatch):
    lines = [_text("First I read the plan."), _tool(), _stop(),
             _text("Then the second page."), _tool(), _stop()] + [
        _retry() for _ in range(model_failures.MAX_IN_A_ROW)]
    last, killed = _drive(thread["id"], lines, monkeypatch)
    assert killed
    assert last["content"].startswith(
        "First I read the plan.\n\nThen the second page.")
    assert "the model kept failing" in last["content"]


def test_a_cloud_turn_is_left_to_the_clis_own_retries(thread, monkeypatch):
    lines = [_retry() for _ in range(model_failures.MAX_IN_A_ROW * 2)]
    last, killed = _drive(thread["id"], lines, monkeypatch, local=False)
    assert not killed
