"""/compact answers immediately; the fold happens behind it.

The operator's report, twice: "I say compact, it shows a message saying
compacting... and then 30s later it starts ACTUALLY compacting."

That gap was the client awaiting the server-side fold — a summarization call —
before sending the half that is actually visible (the CLI's own /compact, which
runs as a turn). Nothing in the visible half depended on the fold, so the wait
bought nothing and cost the whole delay.
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
    """A summarize call that takes real time, as the model call does."""
    import routes.chat as rc

    def summarize(prev, to_fold):
        time.sleep(delay)
        return "the folded summary"

    monkeypatch.setattr(rc, "_summarize_sync", summarize)
    monkeypatch.setattr(rc, "_schedule_distill", lambda *a, **kw: None)
    return rc


class TestItReturnsBeforeTheFold:
    def test_the_response_does_not_wait_for_the_summarizer(self, thread, monkeypatch):
        rc = _slow_summarizer(monkeypatch, delay=0.4)
        tid = thread["id"]
        for i in range(10):
            chat_store.append_message(tid, "user", f"m{i}")

        async def main():
            t0 = time.monotonic()
            r = await rc.compact_thread(tid)
            elapsed = time.monotonic() - t0
            # Let the detached fold finish before the loop closes.
            await asyncio.gather(*list(rc._FOLD_TASKS))
            return r, elapsed

        r, elapsed = asyncio.run(main())
        assert elapsed < 0.2, f"compact blocked for {elapsed:.2f}s"
        assert r["will_fold"] == 6          # 10 - _COMPACT_KEEP_TAIL
        assert r["kept"] == 4
        assert r["before"] == 10
        # …and the fold did land, just not on the caller's clock.
        assert chat_store.get_thread(tid)["compacted_through"] == 6

    def test_will_fold_is_zero_on_a_short_transcript(self, thread, monkeypatch):
        """The client uses this for "nothing to compact", so it has to be right
        WITHOUT running the model call that used to produce it."""
        rc = _slow_summarizer(monkeypatch)
        tid = thread["id"]
        for i in range(3):
            chat_store.append_message(tid, "user", f"m{i}")
        r = asyncio.run(rc.compact_thread(tid))
        assert r["will_fold"] == 0
        assert not rc._FOLD_TASKS          # nothing scheduled either

    def test_will_fold_matches_what_actually_folds(self, thread, monkeypatch):
        """It is computed twice — once here as arithmetic, once inside
        _compact_thread. If they ever disagree the divider lies."""
        rc = _slow_summarizer(monkeypatch, delay=0.0)
        tid = thread["id"]
        for i in range(9):
            chat_store.append_message(tid, "user", f"m{i}")

        async def main():
            r = await rc.compact_thread(tid)
            await asyncio.gather(*list(rc._FOLD_TASKS))
            return r

        r = asyncio.run(main())
        assert r["will_fold"] == chat_store.get_thread(tid)["compacted_through"]

    def test_a_second_compact_folds_only_the_new_tail(self, thread, monkeypatch):
        rc = _slow_summarizer(monkeypatch, delay=0.0)
        tid = thread["id"]
        for i in range(10):
            chat_store.append_message(tid, "user", f"m{i}")

        async def run_once():
            r = await rc.compact_thread(tid)
            await asyncio.gather(*list(rc._FOLD_TASKS))
            return r

        asyncio.run(run_once())
        for i in range(4):
            chat_store.append_message(tid, "user", f"n{i}")
        r2 = asyncio.run(run_once())
        assert r2["compacted_through"] == 6      # where the first left it
        assert r2["will_fold"] == 4              # 8 in the tail, 4 kept
        assert chat_store.get_thread(tid)["compacted_through"] == 10

    def test_unknown_thread_404s(self, thread, monkeypatch):
        from fastapi import HTTPException
        rc = _slow_summarizer(monkeypatch)
        with pytest.raises(HTTPException) as e:
            asyncio.run(rc.compact_thread("0" * 32))
        assert e.value.status_code == 404
