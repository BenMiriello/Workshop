"""`/clear` is the only thing that cuts a thread's context.

That is the whole model as of 2026-08-12. There is no server-side folding, no
running summary and no second kind of boundary; `/compact` reclaims the live CLI
session's window and does not touch the stored transcript. So the invariants
worth holding are small and blunt:

  - a clear cuts at the end, and everything before it stops being sent
  - nothing else can move the cut, at all
  - a boundary left behind by the old folding path is INERT, which hands those
    threads their history back instead of hiding it behind a summary that no
    longer exists
  - the transcript itself is never touched, so a clear is reversible

The previous version of this file was mostly about a clear racing a fold's
delayed write. That race cannot happen now because the fold does not exist.
"""
import asyncio

import pytest

from services import chat_log, chat_store, ownership


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()


def _fill(tid, n=5):
    for i in range(n):
        chat_store.append_message(tid, "user", f"m{i}")


class TestAClearCuts:
    def test_it_cuts_at_the_end(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        _fill(tid, 5)
        r = asyncio.run(rc.clear_thread_context(tid))
        assert r["cleared"] == 5
        assert r["cleared_through"] == 5

    def test_the_transcript_is_untouched(self, thread):
        """A clear hides messages from the model. It does not delete them, which
        is what makes it reversible and what keeps them searchable."""
        import routes.chat as rc
        tid = thread["id"]
        _fill(tid, 5)
        asyncio.run(rc.clear_thread_context(tid))
        t = chat_store.get_thread(tid)
        assert [m["content"] for m in t["messages"]] == [f"m{i}" for i in range(5)]

    def test_messages_after_a_clear_are_live_again(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        _fill(tid, 3)
        asyncio.run(rc.clear_thread_context(tid))
        chat_store.append_message(tid, "user", "after")
        t = chat_store.get_thread(tid)
        assert t["cleared_through"] == 3
        assert [m["content"] for m in t["messages"][t["cleared_through"]:]] == ["after"]

    def test_a_second_clear_moves_the_cut_forward(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        _fill(tid, 3)
        asyncio.run(rc.clear_thread_context(tid))
        chat_store.append_message(tid, "user", "after")
        r = asyncio.run(rc.clear_thread_context(tid))
        assert r["cleared"] == 1
        assert chat_store.get_thread(tid)["cleared_through"] == 4

    def test_clearing_an_empty_thread_is_a_no_op(self, thread):
        import routes.chat as rc
        assert asyncio.run(rc.clear_thread_context(thread["id"]))["cleared"] == 0

    def test_clearing_twice_with_nothing_between_is_a_no_op(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        _fill(tid, 2)
        asyncio.run(rc.clear_thread_context(tid))
        assert asyncio.run(rc.clear_thread_context(tid))["cleared"] == 0

    def test_unknown_thread_404s(self, thread):
        import routes.chat as rc
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as e:
            asyncio.run(rc.clear_thread_context("0" * 32))
        assert e.value.status_code == 404


class TestNothingElseMovesTheCut:
    """The cut used to be an ordinary writable field, which is precisely how
    folding hid messages: any caller could move it while writing something
    unrelated. Now there is one door."""

    def test_update_thread_refuses(self, thread):
        with pytest.raises(ValueError):
            chat_store.update_thread(thread["id"], cleared_through=1)

    def test_update_thread_refuses_the_old_name_too(self, thread):
        """So a caller that was never migrated fails loudly instead of silently
        doing nothing."""
        with pytest.raises(ValueError):
            chat_store.update_thread(thread["id"], compacted_through=1)

    def test_saving_a_thread_does_not_move_it(self, thread):
        tid = thread["id"]
        _fill(tid, 4)
        import routes.chat as rc
        asyncio.run(rc.clear_thread_context(tid))
        t = chat_store.get_thread(tid)
        t["title"] = "renamed"
        chat_store.save_thread(t)
        assert chat_store.get_thread(tid)["cleared_through"] == 4


class TestALegacyFoldBoundaryIsInert:
    """The one piece of legacy handling in the new model.

    Threads folded by the old server-side compaction carry a `boundary` event
    with `mode == "compact"`, and their messages sat behind a summary that is no
    longer assembled into anything. Honouring only `mode == "clear"` retires
    those boundaries and gives the conversation back, with no migration and no
    field to backfill: the condition IS the migration.
    """

    def test_a_compact_boundary_does_not_hide_anything(self, thread):
        tid = thread["id"]
        _fill(tid, 6)
        # Exactly what the old fold wrote: the event, plus the cached seq.
        seqs = [e["seq"] for e in chat_log.read_events(tid) if e.get("kind") == "message"]
        chat_log.append_sync(tid, {"kind": "boundary", "mode": "compact",
                                   "summary": "a summary of the first four"})
        chat_log.patch_meta_sync(tid, boundary_seq=seqs[3])

        t = chat_store.get_thread(tid)
        assert t.get("cleared_through", 0) == 0, "a retired fold boundary still cut the thread"
        assert len(t["messages"]) == 6

    def test_a_clear_written_before_at_seq_still_cuts(self, thread):
        """Clears written before the event carried its own position fall back to
        `meta.boundary_seq`. This is the compatibility path, and it is the only
        thing that still reads that field."""
        tid = thread["id"]
        _fill(tid, 6)
        seqs = [e["seq"] for e in chat_log.read_events(tid) if e.get("kind") == "message"]
        chat_log.append_sync(tid, {"kind": "boundary", "mode": "clear"})  # no at_seq
        chat_log.patch_meta_sync(tid, boundary_seq=seqs[3])

        assert chat_store.get_thread(tid)["cleared_through"] == 4

    def test_a_new_clear_records_its_own_position(self, thread):
        """Going forward the log answers by itself, without the cached field."""
        import routes.chat as rc
        tid = thread["id"]
        _fill(tid, 3)
        asyncio.run(rc.clear_thread_context(tid))
        boundaries = [e for e in chat_log.read_events(tid) if e.get("kind") == "boundary"]
        assert len(boundaries) == 1
        assert boundaries[0]["mode"] == "clear"
        assert boundaries[0]["at_seq"] > 0

    def test_a_clear_after_a_retired_fold_still_works(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        _fill(tid, 4)
        chat_log.append_sync(tid, {"kind": "boundary", "mode": "compact"})
        asyncio.run(rc.clear_thread_context(tid))
        assert chat_store.get_thread(tid)["cleared_through"] == 4
