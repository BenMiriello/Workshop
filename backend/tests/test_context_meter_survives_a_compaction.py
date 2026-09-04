"""The meter must not fall back to an estimate for the whole turn after a compact.

THE DEFECT, measured 2026-09-04 on the operator's own thread, and it is a
RECORDING defect rather than a display one:

  - `GET /context` reads three sources in order: the RUNNING turn's live
    snapshot, then the thread's stored `context_tokens`, then an estimate from
    the stored transcript.
  - The stored value was written only when a turn COMMITTED.
  - A compaction turn produces no usable reading and correctly CLEARS the stored
    value — leaving a pre-compact number there is what made /compact read as a
    no-op, and that clear is right.
  - Nothing then restored it until the NEXT turn committed. On a thread whose
    turns run for an hour, that is an hour on the estimate; on a thread whose
    next turn is also a compaction, it never recovers.

Measured on thread f95ebd20: the transcript estimate said 21,242 tokens (2%)
while the model was reporting 234,014 (23%) on the same conversation. Two of its
three completed turns were compactions. The operator asked whether context was
working at all, which was the correct question.

WHAT WAS RULED OUT, so it is not re-investigated: the session fence on
`set_thread_context_tokens` was NOT the cause. It was proposed as one, and the
measurement contradicts it — 121 of 176 threads on this instance carry a stored
`context_tokens`, and the turn journals for the affected thread show
`meta.session_id` equal to the thread's `claude_session` on every turn, so the
fence passed. The reason that theory took an hour to disprove is that a fence
which drops a write left no trace, which is fixed here too.
"""
from __future__ import annotations

import logging

import pytest

from services import build_streaming, chat_store, ownership


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield
    finally:
        ownership.clear_current_owner()


@pytest.fixture()
def tid(owned):
    t = chat_store.create_thread("general")
    chat_store.append_message(t["id"], "user", "hello")
    return t["id"]


class _Turn:
    def __init__(self, thread_id):
        self.thread_id = thread_id
        self.id = "turn-1"


class TestTheReadingReachesTheThreadMidTurn:
    def test_the_first_reading_of_a_turn_lands_immediately(self, tid):
        """This is the whole fix: after a compaction has cleared the meter, the
        very next model call restores a real number within seconds instead of
        at the end of an hour-long turn."""
        sid, _ = chat_store.ensure_thread_session(tid)
        chat_store.set_thread_context_tokens(tid, 467_005, measured_session=sid)
        chat_store.clear_thread_context_tokens(tid)          # the compaction
        assert "context_tokens" not in (chat_store.get_thread(tid) or {})

        state: dict = {}
        build_streaming._persist_thread_context(
            _Turn(tid), {"session_id": sid}, state, 234_014)
        assert chat_store.get_thread(tid)["context_tokens"] == 234_014

    def test_it_is_throttled_so_the_meter_is_not_a_disk_load(self, tid):
        """An agentic turn makes hundreds of model steps and the thread record
        is a whole-file rewrite. The first write lands; the next one inside the
        window does not."""
        sid, _ = chat_store.ensure_thread_session(tid)
        state: dict = {}
        build_streaming._persist_thread_context(
            _Turn(tid), {"session_id": sid}, state, 100_000)
        build_streaming._persist_thread_context(
            _Turn(tid), {"session_id": sid}, state, 200_000)
        assert chat_store.get_thread(tid)["context_tokens"] == 100_000
        # Past the window, it writes again.
        state["thread_ctx_written_at"] -= build_streaming._THREAD_CTX_WRITE_EVERY_S + 1
        build_streaming._persist_thread_context(
            _Turn(tid), {"session_id": sid}, state, 200_000)
        assert chat_store.get_thread(tid)["context_tokens"] == 200_000

    def test_the_session_fence_still_applies_mid_turn(self, tid):
        """The 2026-08-14 incident must stay fixed: a fold that detached the
        window mid-turn wins over a measurement of the window it detached."""
        sid, _ = chat_store.ensure_thread_session(tid)
        chat_store.clear_thread_session(tid)
        build_streaming._persist_thread_context(
            _Turn(tid), {"session_id": sid}, {}, 467_005)
        assert "context_tokens" not in (chat_store.get_thread(tid) or {})

    def test_a_metering_failure_never_costs_the_turn(self, tid, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("store is down")
        monkeypatch.setattr(chat_store, "set_thread_context_tokens", boom)
        build_streaming._persist_thread_context(
            _Turn(tid), {"session_id": "s"}, {}, 1)  # must not raise

    def test_the_driver_actually_calls_it(self):
        """Wiring, not presence. The reading is computed in `_ingest` for
        parent-only frames; without the call there, the helper is a module
        nobody reaches and the meter is exactly as wrong as before."""
        src = build_streaming.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        i = text.index('_push(turn, "context", {"tokens": ctx})')
        after = text[i:i + 400]
        assert "_persist_thread_context(turn, meta, state, ctx)" in after


class TestADroppedWriteIsObservable:
    def test_the_fence_logs_what_it_dropped(self, tid, caplog):
        """A silent skip is the second defect and arguably the worse one: it is
        why a wrong theory about this code could not be checked by grep."""
        sid, _ = chat_store.ensure_thread_session(tid)
        chat_store.clear_thread_session(tid)
        with caplog.at_level(logging.WARNING, logger="workshop.chat_store"):
            chat_store.set_thread_context_tokens(tid, 467_005,
                                                 measured_session=sid)
        assert any("DROPPED a context measurement" in r.message
                   for r in caplog.records), caplog.text
        # It names the number and the session, so the log line is enough to
        # decide whether the fence was right without re-running anything.
        joined = caplog.text
        assert "467005" in joined and sid in joined

    def test_a_landed_write_is_not_noisy(self, tid, caplog):
        sid, _ = chat_store.ensure_thread_session(tid)
        with caplog.at_level(logging.WARNING, logger="workshop.chat_store"):
            chat_store.set_thread_context_tokens(tid, 1234, measured_session=sid)
        assert not [r for r in caplog.records
                    if "DROPPED a context measurement" in r.message]
