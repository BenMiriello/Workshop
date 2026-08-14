"""Stop on the fold line actually stops the fold.

The Stop control moved out of the composer and onto the lines that represent work
in flight (operator, 2026-08-14: "we want that stop button anywhere that the
system is working"). One of those lines is the fold's: "Compacting. Folding
earlier messages into a summary", which sits there for a ~30s model call while
the thread's queue is held behind it.

There was no cancel path at any layer, so that control would have been
decoration. This is the seam that makes it real.

IT REFUSES THE LANDING RATHER THAN KILLING THE TASK, and that is what makes it
safe. An operation computes for its whole life and writes ONCE, at the end
(summary + cut + boundary + detach). So declining to write is a complete cancel:
there is no partial state to unwind and no window in which half a fold is on
disk. The check lives inside the landing functions, not at the call sites,
because compact has two paths to a landing (inline, and deferred behind a live
run) and a stop honoured on one but not the other is worse than no stop at all.
"""
import asyncio

import pytest

from services import build_streaming, chat_log, chat_store, ownership, thread_ops


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    monkeypatch.setattr(build_streaming, "TURNS_DIR", tmp_path / "turns")
    ownership.set_current_owner(1)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()
        build_streaming.TURNS.clear()
        thread_ops._PENDING.clear()
        thread_ops._CANCELLED.clear()


@pytest.fixture()
def chatty(thread):
    tid = thread["id"]
    for i in range(10):
        chat_store.append_message(tid, "user", f"m{i} " + ("filler " * 30))
    return tid


def _routes():
    import routes.chat as rc
    return rc


def _boundaries(tid):
    return [e for e in chat_log.read_events(tid) if e.get("kind") == "boundary"]


def test_cancel_reports_which_operation_it_called_off(thread):
    tid = thread["id"]
    assert thread_ops.begin(tid, "compact") is True
    assert thread_ops.cancel(tid) == "compact"
    assert thread_ops.cancelled(tid) is True


def test_cancelling_nothing_is_not_an_error(thread):
    """The fold landed while the request was on the wire. A race, not a failure:
    the caller says "nothing to stop" rather than raising."""
    rc = _routes()
    out = asyncio.run(rc.cancel_thread_operation(thread["id"]))
    assert out == {"cancelled": False, "operation": ""}


def test_end_clears_the_flag(thread):
    """Or the NEXT operation on this thread would refuse to land before it began."""
    tid = thread["id"]
    thread_ops.begin(tid, "compact")
    thread_ops.cancel(tid)
    thread_ops.end(tid)
    assert thread_ops.cancelled(tid) is False
    assert thread_ops.begin(tid, "compact") is True
    assert thread_ops.cancelled(tid) is False


def test_a_cancelled_fold_writes_nothing(chatty, monkeypatch):
    """The whole point. The summarizer runs, the operator presses Stop, and the
    transcript is exactly as it was: no summary, no cut, no boundary event."""
    rc = _routes()

    async def _summary_then_stop(thread_id, _t):
        # Stands in for the ~30s model call, and for the operator pressing Stop
        # part-way through it.
        thread_ops.cancel(thread_id)
        return 4, "a summary that must never land", 4, ""

    monkeypatch.setattr(rc, "_fold_summary", _summary_then_stop)

    out = asyncio.run(rc.compact_thread(chatty))

    assert out["cancelled"] is True
    assert out["folded"] == 0
    t = chat_store.get_thread(chatty)
    assert "summary" not in t
    assert t.get("cleared_through", 0) == 0
    assert _boundaries(chatty) == []


def test_the_thread_is_released_so_queued_messages_go(chatty, monkeypatch):
    """A cancel must not leave the door held. The registration is released the
    same way it always is, so anything that queued behind the operation drains
    instead of waiting for a restart."""
    rc = _routes()

    async def _summary_then_stop(thread_id, _t):
        thread_ops.cancel(thread_id)
        return 4, "dropped", 4, ""

    monkeypatch.setattr(rc, "_fold_summary", _summary_then_stop)
    asyncio.run(rc.compact_thread(chatty))

    assert thread_ops.pending(chatty) is None
    assert thread_ops.cancelled(chatty) is False


def test_a_cancelled_deferred_fold_also_writes_nothing(chatty, monkeypatch):
    """The path that made the check belong in `_land_fold` rather than at the
    call sites: a fold computed while a run was live lands later, from a
    background task, and has to honour the same stop."""
    rc = _routes()
    thread_ops.begin(chatty, "compact")
    thread_ops.cancel(chatty)

    async def _quiet(_tid):
        return None

    monkeypatch.setattr(thread_ops, "wait_quiet", _quiet)
    asyncio.run(rc._land_fold_when_quiet(chatty, "must never land", 4))

    t = chat_store.get_thread(chatty)
    assert "summary" not in t
    assert _boundaries(chatty) == []
    assert thread_ops.pending(chatty) is None


def test_a_cancelled_clear_writes_nothing(chatty, monkeypatch):
    """/clear has no model call, so its window is narrow, but it lands through
    the same gate and must obey the same stop."""
    rc = _routes()
    thread_ops.begin(chatty, "clear")
    thread_ops.cancel(chatty)

    async def _quiet(_tid):
        return None

    monkeypatch.setattr(thread_ops, "wait_quiet", _quiet)
    asyncio.run(rc._land_clear_when_quiet(chatty))

    t = chat_store.get_thread(chatty)
    assert t.get("cleared_through", 0) == 0
    assert _boundaries(chatty) == []
