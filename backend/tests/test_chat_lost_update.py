"""A thread never loses a message.

Every mutation in chat_store is read-modify-write of the whole file, which is
safe only while no `await` sits between the read and the write. `/compact` broke
that rule by design: it read the thread, spent tens of seconds in a
summarization call, then saved the snapshot back — deleting everything the
operator sent during the fold.

That is the bug behind "I sent a message during compact and it vanished", and it
outlived the earlier ordering fix because ordering only describes messages that
still exist.
"""
import asyncio

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


class TestWriteRefusesToShrink:
    def test_saving_a_stale_snapshot_raises(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "one")
        stale = chat_store.get_thread(tid)          # read
        chat_store.append_message(tid, "user", "two")  # …meanwhile
        stale["summary"] = "folded"
        with pytest.raises(chat_store.LostUpdate):
            chat_store.save_thread(stale)
        # And the message that would have been erased is still there.
        msgs = chat_store.get_thread(tid)["messages"]
        assert [m["content"] for m in msgs] == ["one", "two"]

    def test_an_up_to_date_save_still_works(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "one")
        t = chat_store.get_thread(tid)
        t["summary"] = "fine"
        chat_store.save_thread(t)
        assert chat_store.get_thread(tid)["summary"] == "fine"


class TestUpdateThread:
    def test_it_writes_onto_the_current_thread_not_a_snapshot(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "one")
        chat_store.get_thread(tid)                    # the stale read
        chat_store.append_message(tid, "user", "two")
        chat_store.update_thread(tid, summary="folded", compacted_through=1)
        t = chat_store.get_thread(tid)
        assert t["summary"] == "folded"
        assert t["compacted_through"] == 1
        assert [m["content"] for m in t["messages"]] == ["one", "two"]

    def test_none_removes_a_field(self, thread):
        tid = thread["id"]
        chat_store.set_thread_context_tokens(tid, 1234)
        chat_store.update_thread(tid, context_tokens=None)
        assert "context_tokens" not in chat_store.get_thread(tid)

    def test_it_will_not_write_messages(self, thread):
        with pytest.raises(ValueError):
            chat_store.update_thread(thread["id"], messages=[])

    def test_unknown_thread_is_none(self, thread):
        assert chat_store.update_thread("0" * 32, summary="x") is None


class TestCompactDoesNotEatMessages:
    """The real path: routes.chat._compact_thread with a slow summarizer, and the
    operator typing during the fold."""

    def _run(self, tid, monkeypatch):
        import routes.chat as rc

        def slow_summary(prev, to_fold):
            return "the folded summary"

        async def slow_to_thread(fn, *a, **kw):
            await asyncio.sleep(0.05)   # the window the message lands in
            return fn(*a, **kw)

        monkeypatch.setattr(rc, "_summarize_sync", slow_summary)
        monkeypatch.setattr(rc.asyncio, "to_thread", slow_to_thread)
        monkeypatch.setattr(rc, "_schedule_distill", lambda *a, **kw: None)

        async def main():
            snapshot = chat_store.get_thread(tid)   # what the route holds

            async def types():
                await asyncio.sleep(0.01)
                chat_store.append_message(tid, "user", "WAIT stop")

            fold = rc._compact_thread(snapshot, "workshop", force=True)
            return (await asyncio.gather(fold, types()))[0]

        return asyncio.run(main())

    def test_a_message_sent_during_the_fold_survives(self, thread, monkeypatch):
        tid = thread["id"]
        for i in range(10):
            chat_store.append_message(tid, "user" if i % 2 == 0 else "assistant", f"m{i}")

        _, _, folded = self._run(tid, monkeypatch)

        t = chat_store.get_thread(tid)
        assert folded == 6                       # 10 - _COMPACT_KEEP_TAIL
        assert t["summary"] == "the folded summary"
        assert t["compacted_through"] == 6
        assert len(t["messages"]) == 11
        assert any("WAIT stop" in m["content"] for m in t["messages"])

    def test_compacted_through_still_points_at_the_right_messages(self, thread, monkeypatch):
        """It counts from the FRONT and appends land at the BACK, which is why the
        index stays correct across a message arriving mid-fold."""
        tid = thread["id"]
        for i in range(10):
            chat_store.append_message(tid, "user", f"m{i}")
        self._run(tid, monkeypatch)
        t = chat_store.get_thread(tid)
        folded_away = t["messages"][: t["compacted_through"]]
        assert [m["content"] for m in folded_away] == [f"m{i}" for i in range(6)]


class TestOneFoldAtATime:
    def test_a_second_concurrent_fold_is_a_no_op(self, thread, monkeypatch):
        """Two folds would each compute the same range and write the same index,
        so the second folds already-folded turns into a summary built without the
        first — losing the difference."""
        import routes.chat as rc

        calls = []

        def summarize(prev, to_fold):
            calls.append(len(to_fold))
            return "s"

        async def slow_to_thread(fn, *a, **kw):
            await asyncio.sleep(0.05)
            return fn(*a, **kw)

        monkeypatch.setattr(rc, "_summarize_sync", summarize)
        monkeypatch.setattr(rc.asyncio, "to_thread", slow_to_thread)
        monkeypatch.setattr(rc, "_schedule_distill", lambda *a, **kw: None)

        tid = thread["id"]
        for i in range(10):
            chat_store.append_message(tid, "user", f"m{i}")

        async def main():
            a = chat_store.get_thread(tid)
            b = chat_store.get_thread(tid)
            return await asyncio.gather(
                rc._compact_thread(a, "workshop", force=True),
                rc._compact_thread(b, "workshop", force=True),
            )

        results = asyncio.run(main())
        assert len(calls) == 1
        assert sorted(r[2] for r in results) == [0, 6]
