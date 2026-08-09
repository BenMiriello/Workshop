"""A thread never loses a message.

Mutations used to be read-modify-write of one whole JSON document, which is safe
only while no `await` sits between the read and the write. `/compact` broke that
rule by design: it read the thread, spent tens of seconds in a summarization
call, then saved the snapshot back — deleting everything the operator sent during
the fold. That is the bug behind "I sent a message during compact and it
vanished", and it outlived the earlier ordering fix because ordering only
describes messages that still exist.

The store used to catch that with a `LostUpdate` tripwire: any write that shrank
the transcript was refused, because nothing in the store ever removed a message.
The transcript is now an append-only log (docs/chat-rebuild-spec-2026-08-09.md)
and there is no operation that writes it as a whole, so the tripwire is gone and
what it guarded is gone with it. These tests pin the stronger guarantee that
replaced it: a stale snapshot does not raise, it simply cannot delete anything.
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


class TestAStaleSnapshotCannotDelete:
    def test_saving_a_stale_snapshot_keeps_the_message_it_never_saw(self, thread):
        """The old store refused this write. The log accepts it and still keeps
        the message, because "everything else goes back to what I read" is not
        something an append-only log can be asked to do."""
        tid = thread["id"]
        chat_store.append_message(tid, "user", "one")
        stale = chat_store.get_thread(tid)          # read
        chat_store.append_message(tid, "user", "two")  # …meanwhile
        stale["summary"] = "folded"
        chat_store.save_thread(stale)               # no longer raises
        t = chat_store.get_thread(tid)
        assert [m["content"] for m in t["messages"]] == ["one", "two"]
        assert t["summary"] == "folded"

    def test_the_tripwire_is_gone_because_what_it_guarded_is_gone(self):
        """`LostUpdate` guarded whole-document writes. There are none."""
        assert not hasattr(chat_store, "LostUpdate")
        assert not hasattr(chat_store, "_write")

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


class TestSaveThreadDiffsInsteadOfOverwriting:
    """`save_thread` is the shim for callers that still mutate a thread dict in
    place. It appends what the caller added and revises what it edited; it never
    writes the transcript as a whole, so nothing it does not mention is at risk."""

    def test_a_message_added_to_a_stale_snapshot_lands_after_the_one_it_missed(
            self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "one")
        stale = chat_store.get_thread(tid)
        chat_store.append_message(tid, "user", "two")      # …meanwhile
        stale["messages"].append({"role": "user", "content": "three",
                                  "ts": "2026-08-09T12:00:00+00:00"})
        chat_store.save_thread(stale)
        msgs = chat_store.get_thread(tid)["messages"]
        assert [m["content"] for m in msgs] == ["one", "two", "three"]

    def test_a_stored_message_is_never_edited(self, thread):
        """There is no edit event, deliberately. `revises` existed only to
        finalize a reply into a slot claimed at turn start, and that slot is what
        filed the operator's mid-turn messages below a reply that had not read
        them. A caller handing back a changed message writes nothing."""
        tid = thread["id"]
        chat_store.append_message(tid, "user", "one")
        chat_store.append_message(tid, "assistant", "the reply")
        t = chat_store.get_thread(tid)
        t["messages"][1]["content"] = "something else"
        chat_store.save_thread(t)
        msgs = chat_store.get_thread(tid)["messages"]
        assert [m["content"] for m in msgs] == ["one", "the reply"]

    def test_a_live_run_view_is_not_committed_by_a_write_back(self, thread):
        """A caller that read the thread mid-run is holding the run's live view
        as a trailing assistant message. Appending it would commit a half-written
        reply that the run is about to commit properly."""
        tid = thread["id"]
        chat_store.append_message(tid, "user", "go")
        chat_store.begin_run(tid, "run-1")
        chat_store.update_run(tid, "run-1", [{"t": "text", "text": "half"}], "half")
        t = chat_store.get_thread(tid)
        assert t["messages"][-1]["meta"]["streaming"] is True
        chat_store.save_thread(t)
        chat_store.commit_run(tid, "run-1", [], "the whole answer")
        msgs = chat_store.get_thread(tid)["messages"]
        assert [m["content"] for m in msgs] == ["go", "the whole answer"]

    def test_a_field_the_caller_dropped_is_removed(self, thread):
        tid = thread["id"]
        chat_store.set_thread_context_tokens(tid, 999)
        t = chat_store.get_thread(tid)
        t.pop("context_tokens")
        chat_store.save_thread(t)
        assert "context_tokens" not in chat_store.get_thread(tid)
