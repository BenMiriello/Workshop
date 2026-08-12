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
        chat_store.update_thread(tid, title="named later")
        t = chat_store.get_thread(tid)
        assert t["title"] == "named later"
        assert [m["content"] for m in t["messages"]] == ["one", "two"]

    def test_it_refuses_to_move_the_context_cut(self, thread):
        """The cut is made by /clear and by nothing else. It used to be writable
        here, which is how server-side folding hid messages: any caller could
        move the boundary while writing some unrelated field."""
        import pytest as _pytest
        with _pytest.raises(ValueError):
            chat_store.update_thread(thread["id"], cleared_through=1)
        with _pytest.raises(ValueError):
            chat_store.update_thread(thread["id"], compacted_through=1)

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
