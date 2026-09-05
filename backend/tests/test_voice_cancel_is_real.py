"""Cancelling a spoken turn stops it, and what it did spend is recorded.

Three separate ways voice claimed to have stopped something it had not:

  1. `_interrupt` wrote a control request to a pipe and swallowed every
     exception. A pipe write succeeds whether or not anything acts on it, so a
     cancel that changed nothing looked exactly like one that worked.
  2. The pump dropped every event for a turn already marked complete — and a
     cancelled turn is marked complete immediately, while the CLI's `result`,
     the ONLY event carrying usage, arrives after. So an interrupted generation
     was billed to the account and recorded nowhere.
  3. A control response is answered against a completed turn by definition, so
     rule 2 also meant no interrupt could ever be acknowledged.

Also pinned: a voice turn claims the thread, so the rest of the system can see a
run is in flight, and gives it back when it ends.
"""
import asyncio
import json

import pytest

from services import (build_streaming, chat_store, ownership, usage_ledger,
                      voice_worker)


@pytest.fixture
def turn(monkeypatch):
    monkeypatch.setattr(voice_worker, "usage_scope_for_thread", lambda _tid: "workshop")
    return voice_worker.VoiceTurn(id="vt-1", session_key="thread-1", thread_id="thread-1",
                                  user_text="tell me about the sweep")


class FakeStdin:
    def __init__(self):
        self.written: list[bytes] = []

    def write(self, b: bytes) -> None:
        self.written.append(b)

    async def drain(self) -> None:
        return None

    def requests(self) -> list[dict]:
        return [json.loads(b.decode()) for b in self.written]


class FakeProc:
    def __init__(self):
        self.stdin = FakeStdin()
        self.returncode = None


def _worker() -> voice_worker.VoiceWorker:
    w = voice_worker.VoiceWorker(
        session_key="thread-1", thread_id="thread-1", session_id="s",
        model="m", context_key="general")
    w.proc = FakeProc()
    return w


class TestAnInterruptIsAnswered:
    def test_it_waits_for_the_cli_to_acknowledge(self):
        w = _worker()

        async def go():
            task = asyncio.create_task(voice_worker._interrupt(w))
            # Let the request reach the pipe, then answer it the way the CLI does.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            req = w.proc.stdin.requests()[0]
            voice_worker._resolve_control(w, {
                "type": "control_response",
                "response": {"subtype": "success", "request_id": req["request_id"]},
            })
            return await task

        assert asyncio.run(go()) is True

    def test_an_unanswered_interrupt_reports_failure_rather_than_success(self, monkeypatch):
        """The whole point. Nothing answers, so the generation is still running,
        and saying otherwise is how the operator was told a turn had stopped
        while it went on spending."""
        monkeypatch.setattr(voice_worker, "INTERRUPT_WAIT_S", 0.05)
        w = _worker()
        assert asyncio.run(voice_worker._interrupt(w)) is False

    def test_an_error_response_is_not_a_stop(self):
        w = _worker()

        async def go():
            task = asyncio.create_task(voice_worker._interrupt(w))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            req = w.proc.stdin.requests()[0]
            voice_worker._resolve_control(w, {
                "type": "control_response",
                "response": {"subtype": "error", "request_id": req["request_id"]},
            })
            return await task

        assert asyncio.run(go()) is False

    def test_a_dead_worker_does_not_claim_to_have_stopped_anything(self):
        w = _worker()
        w.dead = True
        assert asyncio.run(voice_worker._interrupt(w)) is False

    def test_a_response_nobody_is_waiting_for_is_harmless(self):
        w = _worker()
        voice_worker._resolve_control(w, {"response": {"subtype": "success", "request_id": "nope"}})
        assert w.control_waiters == {}


class TestACancelledTurnIsStillBilled:
    def test_usage_arriving_after_the_cancel_is_recorded(self, turn, monkeypatch):
        # Patch the LEDGER, not `_record_spend`. A fake standing in for
        # `_record_spend` has to re-declare its signature, and when the real one
        # grew an `envelope` argument (so voice reads `modelUsage` through the
        # same seam the text path does) the two-argument fake started raising
        # TypeError inside the pump, which swallows it — so this test failed
        # with "the tokens went unrecorded" while the product was fine. Going
        # one layer down means the assertion runs the real spend path and cannot
        # drift from it again.
        seen: list[int] = []
        monkeypatch.setattr(usage_ledger, "record",
                            lambda scope, tokens, **kw: seen.append(int(tokens)))
        w = _worker()
        w.current = turn
        # The turn is closed the instant the operator barges in...
        voice_worker._finish_turn(turn, error=None)
        # ...and the CLI reports what it spent afterwards.
        asyncio.run(_pump_one(w, {"type": "result", "usage": {"output_tokens": 900}}))
        assert seen == [900], "the interrupted generation's tokens went unrecorded"

    def test_a_completed_turn_still_ignores_ordinary_events(self, turn):
        w = _worker()
        w.current = turn
        voice_worker._finish_turn(turn, error=None)
        before = len(turn.events)
        asyncio.run(_pump_one(w, {"type": "stream_event", "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "still talking"}}}))
        # Text after the end is not part of the reply; only the spend is.
        assert len(turn.events) == before
        assert "still talking" not in turn.text


async def _pump_one(worker: voice_worker.VoiceWorker, ev: dict) -> None:
    """Drive the pump's routing over exactly one line.

    The pump reads from a real stdout stream, so the loop body is exercised
    through a one-line stream rather than reimplemented here — a copy of the
    routing would pass while the routing itself stayed broken."""
    reader = asyncio.StreamReader()
    reader.feed_data((json.dumps(ev) + "\n").encode())
    reader.feed_eof()
    worker.proc.stdout = reader
    await voice_worker._pump(worker)


class TestAVoiceTurnIsAVisibleRun:
    @pytest.fixture()
    def thread(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
        ownership.set_current_owner(1)
        try:
            yield chat_store.create_thread("general")
        finally:
            ownership.clear_current_owner()
            build_streaming.TURNS.clear()

    def test_it_holds_the_thread_and_refuses_typed_input(self, thread):
        tid = thread["id"]

        async def go():
            await build_streaming.claim_turn(tid, "vt-1", model="m", accepts_input=False)

        asyncio.run(go())
        # Visible: something is running on this chat.
        assert chat_store.active_turn_id(tid) == "vt-1"
        assert build_streaming.turn_is_live("vt-1") is True
        # And it is not a channel typed text can be pushed into.
        assert build_streaming.turn_accepts_input("vt-1") is False

    def test_it_gives_the_thread_back_when_it_ends(self, thread):
        tid = thread["id"]
        asyncio.run(build_streaming.claim_turn(tid, "vt-1", model="m", accepts_input=False))
        build_streaming.release_external_turn(tid, "vt-1")
        assert chat_store.active_turn_id(tid) != "vt-1"
        assert build_streaming.turn_is_live("vt-1") is False
