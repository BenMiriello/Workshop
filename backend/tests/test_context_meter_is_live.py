"""The composer's meter reads the RUNNING turn, not the last one that finished.

`context_tokens` on the thread is written at COMMIT, so for the whole length of
a turn GET /context served the PREVIOUS turn's number. That is invisible on a
short turn and badly wrong on a long one: an agentic turn here routinely runs
for an hour and grows the prompt by an order of magnitude.

Measured 2026-09-03: the operator watched the meter read 2% through a
90-minute turn that finished at 24%, alongside a backend restart that dropped
his SSE, and reasonably concluded the meter was broken. It was reporting a
different conversation's size, confidently.

The live reading already existed on the wire (the driver pushes a `context`
event whenever the model reports a new prompt size), but the wire only reaches a
tab that is streaming at that instant. A reload, a second tab, or a reconnect
after a restart all fell back to this endpoint and got the stale number. It is
now persisted into the live snapshot, for the same reason `cut_head` is, and
this endpoint prefers it.
"""
import asyncio

import pytest

from services import chat_store, ownership


@pytest.fixture()
def tid(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        t = chat_store.create_thread("general")
        chat_store.append_message(t["id"], "user", "hello")
        yield t["id"]
    finally:
        ownership.clear_current_owner()


def _context(thread_id):
    import routes.chat as rc
    return asyncio.run(rc.thread_context(thread_id))


class TestARunningTurnOwnsTheReading:
    def test_the_live_reading_beats_the_committed_one(self, tid):
        chat_store.set_thread_context_tokens(tid, 5_000)     # the last turn's
        chat_store.begin_turn(tid, "r1")
        chat_store.update_turn(tid, "r1", [{"t": "text", "text": "x"}], "x",
                               context_tokens=400_000)       # this turn's
        out = _context(tid)
        assert out["source"] == "live"
        assert out["used_tokens"] == 400_000
        assert out["pct"] > 1, "the meter must not report the previous turn's size"

    def test_with_no_turn_running_the_committed_reading_still_wins(self, tid):
        chat_store.set_thread_context_tokens(tid, 5_000)
        out = _context(tid)
        assert out["source"] == "measured"
        assert out["used_tokens"] == 5_000

    def test_a_turn_that_has_not_reported_yet_falls_back(self, tid):
        """A turn only publishes a size once the model has reported one. Until
        then the last committed number is the best answer available, and it is
        better than zero."""
        chat_store.set_thread_context_tokens(tid, 5_000)
        chat_store.begin_turn(tid, "r1")
        chat_store.update_turn(tid, "r1", [{"t": "text", "text": "x"}], "x")
        out = _context(tid)
        assert out["source"] == "measured"
        assert out["used_tokens"] == 5_000
