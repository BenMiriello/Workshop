"""/clear drops context, keeps the transcript — no model call, no detached task.

The other half of the /compact vs /clear split (see chat_store.clear_thread_context
and routes.chat.clear_thread_context). The one thing that must never regress: a
/clear landing while a /compact fold is mid-flight must not let the fold's
delayed write resurrect a summary of content the clear just dropped.
"""
import asyncio
import time

import pytest

from services import chat_store, ownership


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()


def _slow_summarizer(monkeypatch, delay=0.4):
    import routes.chat as rc

    def summarize(prev, to_fold):
        time.sleep(delay)
        return "the folded summary"

    monkeypatch.setattr(rc, "_summarize_sync", summarize)
    monkeypatch.setattr(rc, "_schedule_distill", lambda *a, **kw: None)
    return rc


def test_clear_sets_compacted_through_to_the_end_and_drops_summary(thread, monkeypatch):
    import routes.chat as rc
    tid = thread["id"]
    for i in range(5):
        chat_store.append_message(tid, "user", f"m{i}")
    chat_store.update_thread(tid, summary="an earlier fold", claude_session="sess-1")

    r = asyncio.run(rc.clear_thread_context(tid))

    assert r["cleared"] == 5
    assert r["compacted_through"] == 5
    assert r["dropped_summary"] is True
    t = chat_store.get_thread(tid)
    assert t["compacted_through"] == 5
    assert "summary" not in t
    assert "claude_session" not in t
    assert t["context_cut"] == "cleared"
    assert len(t["messages"]) == 5   # nothing deleted from the transcript


def test_compact_after_a_clear_has_nothing_to_fold(thread, monkeypatch):
    rc = _slow_summarizer(monkeypatch)
    tid = thread["id"]
    for i in range(10):
        chat_store.append_message(tid, "user", f"m{i}")

    asyncio.run(rc.clear_thread_context(tid))

    def summarize_should_not_run(*a, **kw):
        raise AssertionError("compact should not call the summarizer after a clear")
    monkeypatch.setattr(rc, "_summarize_sync", summarize_should_not_run)

    r = asyncio.run(rc.compact_thread(tid))
    assert r["will_fold"] == 0


def test_a_clear_mid_fold_is_not_undone_by_the_late_write(thread, monkeypatch):
    """The race the write-guard in _compact_thread exists for: /compact starts,
    is slow in its summarization call, /clear lands and finishes first, then the
    fold's delayed write must not move compacted_through backward or bring the
    summary back."""
    rc = _slow_summarizer(monkeypatch, delay=0.3)
    tid = thread["id"]
    for i in range(10):
        chat_store.append_message(tid, "user", f"m{i}")

    async def main():
        task = asyncio.create_task(rc.compact_thread(tid))
        await asyncio.sleep(0.05)   # let compact_thread schedule its fold task
        await rc.clear_thread_context(tid)
        await task
        await asyncio.gather(*list(rc._FOLD_TASKS))

    asyncio.run(main())

    t = chat_store.get_thread(tid)
    assert t["compacted_through"] == 10
    assert "summary" not in t


def test_clearing_an_empty_thread_is_a_no_op(thread):
    import routes.chat as rc
    tid = thread["id"]
    r = asyncio.run(rc.clear_thread_context(tid))
    assert r["cleared"] == 0


def test_clear_unknown_thread_404s(thread):
    import routes.chat as rc
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        asyncio.run(rc.clear_thread_context("0" * 32))
    assert e.value.status_code == 404
