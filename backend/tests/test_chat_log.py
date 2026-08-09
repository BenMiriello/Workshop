"""The append-only chat log: seqs, idempotency, the write lock, and migration.

These pin the properties the rest of the chat system is now allowed to assume
(docs/chat-rebuild-spec-2026-08-09.md):

  - a seq is 1-based, monotonic per thread, and never reused, so it is a cursor
    that survives anything appended later — unlike the array index it replaced;
  - a client_id is committed once, so a browser retrying a send cannot double
    the message;
  - one writer at a time, so concurrent appends leave no gaps and no reordering;
  - a patch writes the fields it names and nothing else;
  - migration off the old one-document-per-thread layout round-trips exactly and
    can be run again without duplicating anything.
"""
import asyncio
import json
import uuid

import pytest

from services import chat_log, chat_store, ownership


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
    return chat_store.create_thread("general")["id"]


def _seqs(thread_id):
    return [e["seq"] for e in chat_log.read_events(thread_id)]


class TestSeq:
    def test_it_is_one_based_and_monotonic(self, tid):
        for i in range(5):
            chat_log.append_message_sync(tid, "user", f"m{i}")
        assert _seqs(tid) == [1, 2, 3, 4, 5]

    def test_every_kind_draws_from_the_same_sequence(self, tid):
        chat_log.append_message_sync(tid, "user", "hi")
        chat_log.append_sync(tid, {"kind": "run", "run_id": "r1", "state": "started"})
        chat_log.append_sync(tid, {"kind": "boundary", "mode": "compact"})
        chat_log.append_sync(tid, {"kind": "title", "title": "T", "source": "auto"})
        events = chat_log.read_events(tid)
        assert [e["seq"] for e in events] == [1, 2, 3, 4]
        assert [e["kind"] for e in events] == ["message", "run", "boundary", "title"]

    def test_meta_last_seq_tracks_the_log(self, tid):
        for i in range(3):
            chat_log.append_message_sync(tid, "user", f"m{i}")
        assert chat_log.read_meta(tid)["last_seq"] == 3

    def test_a_meta_left_behind_by_a_crash_defers_to_the_log(self, tid):
        """The log is the record. If a crash landed between the append and the
        meta write, the next seq must still come after what is on disk."""
        for i in range(3):
            chat_log.append_message_sync(tid, "user", f"m{i}")
        meta = json.loads(chat_log.meta_path(tid).read_text())
        meta["last_seq"] = 1                      # as if the write never landed
        chat_log.meta_path(tid).write_text(json.dumps(meta))
        ev = chat_log.append_message_sync(tid, "user", "after")
        assert ev["seq"] == 4
        assert _seqs(tid) == [1, 2, 3, 4]

    def test_ids_are_ulids_that_sort_in_creation_order(self, tid):
        for i in range(20):
            chat_log.append_message_sync(tid, "user", f"m{i}")
        ids = [e["id"] for e in chat_log.read_events(tid)]
        assert all(len(i) == 26 for i in ids)
        assert ids == sorted(ids)
        assert len(set(ids)) == 20


class TestClientIdIsCommittedOnce:
    def test_the_same_client_id_twice_appends_once(self, tid):
        first = chat_log.append_message_sync(tid, "user", "send", client_id="c-1")
        again = chat_log.append_message_sync(tid, "user", "send", client_id="c-1")
        assert again["seq"] == first["seq"]
        assert again["id"] == first["id"]
        assert _seqs(tid) == [1]

    def test_a_retry_returns_what_was_committed_not_what_was_resent(self, tid):
        first = chat_log.append_message_sync(tid, "user", "the real one", client_id="c-1")
        again = chat_log.append_message_sync(tid, "user", "a garbled retry", client_id="c-1")
        assert again["content"] == "the real one"
        assert first["content"] == "the real one"

    def test_different_client_ids_both_land(self, tid):
        chat_log.append_message_sync(tid, "user", "a", client_id="c-1")
        chat_log.append_message_sync(tid, "user", "b", client_id="c-2")
        assert _seqs(tid) == [1, 2]

    def test_a_message_with_no_client_id_is_never_deduped(self, tid):
        chat_log.append_message_sync(tid, "user", "same")
        chat_log.append_message_sync(tid, "user", "same")
        assert _seqs(tid) == [1, 2]


class TestOneWriterAtATime:
    def test_concurrent_appends_leave_no_gaps_and_keep_their_order(self, tid):
        async def main():
            await asyncio.gather(*[
                chat_log.append_message(tid, "user", f"m{i}") for i in range(50)
            ])

        asyncio.run(main())
        events = chat_log.read_events(tid)
        assert [e["seq"] for e in events] == list(range(1, 51))
        assert [e["content"] for e in events] == [f"m{i}" for i in range(50)]

    def test_an_append_waits_for_the_lock(self, tid):
        """The lock is what pass B holds across an await to check-and-act (start
        a run only if none is active). It has to actually block."""
        async def main():
            lock = chat_log.lock_for(tid)
            await lock.acquire()
            task = asyncio.create_task(chat_log.append_message(tid, "user", "blocked"))
            await asyncio.sleep(0.05)
            in_flight = task.done()
            lock.release()
            await task
            return in_flight

        assert asyncio.run(main()) is False
        assert [e["content"] for e in chat_log.read_events(tid)] == ["blocked"]

    def test_a_worker_thread_write_is_refused(self, tid):
        async def main():
            chat_log.append_message_sync(tid, "user", "on the loop")  # teach it the loop
            return await asyncio.to_thread(
                chat_log.append_message_sync, tid, "user", "from a worker")

        with pytest.raises(chat_log.OffLoopWrite):
            asyncio.run(main())
        assert [e["content"] for e in chat_log.read_events(tid)] == ["on the loop"]

    def test_reading_from_a_worker_thread_is_fine(self, tid):
        """Only writes race. The auto-namer reads the transcript in a worker
        thread to build its prompt, and that has to keep working."""
        chat_log.append_message_sync(tid, "user", "hi")

        async def main():
            return await asyncio.to_thread(chat_log.read_events, tid)

        assert [e["content"] for e in asyncio.run(main())] == ["hi"]


class TestPatchMeta:
    def test_it_writes_only_the_named_fields(self, tid):
        chat_log.patch_meta_sync(tid, summary="s", claude_session="sess-1")
        chat_log.patch_meta_sync(tid, summary="s2")
        meta = chat_log.read_meta(tid)
        assert meta["summary"] == "s2"
        assert meta["claude_session"] == "sess-1"     # untouched
        assert meta["context_key"] == "general"       # and so is everything else

    def test_none_removes_a_field(self, tid):
        chat_log.patch_meta_sync(tid, context_tokens=1234)
        chat_log.patch_meta_sync(tid, context_tokens=None)
        assert "context_tokens" not in chat_log.read_meta(tid)

    def test_it_cannot_touch_the_transcript(self, tid):
        chat_log.append_message_sync(tid, "user", "one")
        chat_log.patch_meta_sync(tid, summary="folded")
        assert [e["content"] for e in chat_log.read_events(tid)] == ["one"]

    def test_an_unknown_thread_is_none(self, owned):
        assert chat_log.patch_meta_sync("0" * 32, summary="x") is None


class TestReadEventsCursor:
    def test_after_returns_only_what_is_newer(self, tid):
        for i in range(5):
            chat_log.append_message_sync(tid, "user", f"m{i}")
        assert [e["content"] for e in chat_log.read_events(tid, after=3)] == ["m3", "m4"]

    def test_after_zero_is_the_whole_log(self, tid):
        for i in range(3):
            chat_log.append_message_sync(tid, "user", f"m{i}")
        assert len(chat_log.read_events(tid, after=0)) == 3

    def test_a_cursor_at_the_end_returns_nothing(self, tid):
        for i in range(3):
            chat_log.append_message_sync(tid, "user", f"m{i}")
        assert chat_log.read_events(tid, after=3) == []

    def test_limit_caps_the_page_from_the_cursor(self, tid):
        for i in range(10):
            chat_log.append_message_sync(tid, "user", f"m{i}")
        page = chat_log.read_events(tid, after=2, limit=3)
        assert [e["content"] for e in page] == ["m2", "m3", "m4"]

    def test_the_cursor_survives_a_boundary(self, tid):
        """The point of a seq: a cut, a rename or a run marker does not shift it,
        which is exactly what `compacted_through` could not promise."""
        for i in range(3):
            chat_log.append_message_sync(tid, "user", f"m{i}")
        chat_log.append_sync(tid, {"kind": "boundary", "mode": "compact"})
        chat_log.append_message_sync(tid, "user", "after")
        assert [e["content"] for e in chat_log.read_events(tid, after=3)
                if e["kind"] == "message"] == ["after"]

    def test_read_thread_reports_the_cursor_and_the_active_run(self, tid):
        chat_log.append_message_sync(tid, "user", "go")
        chat_log.append_sync(tid, {"kind": "run", "run_id": "r1", "state": "started"})
        chat_log.patch_meta_sync(tid, active_run_id="r1")
        got = chat_log.read_thread(tid)
        assert got["last_seq"] == 2
        assert got["active_run"] == {"run_id": "r1", "started_seq": 2, "live": None}

    def test_read_thread_bounds_the_tail(self, tid):
        for i in range(10):
            chat_log.append_message_sync(tid, "user", f"m{i}")
        got = chat_log.read_thread(tid, tail=3)
        assert [e["content"] for e in got["events"]] == ["m7", "m8", "m9"]


# ---- migration -------------------------------------------------------------

_LEGACY_MESSAGES = [
    {"role": "user", "content": "how do subagents work", "ts": "2026-08-01T10:00:00+00:00"},
    {"role": "assistant", "content": "like this", "ts": "2026-08-01T10:00:05+00:00",
     "meta": {"segments": [{"t": "text", "text": "like this"}], "streaming": False}},
    {"role": "user", "content": "and after a restart", "ts": "2026-08-01T10:01:00+00:00"},
    {"role": "assistant", "content": "still fine", "ts": "2026-08-01T10:01:09+00:00"},
]


def _write_legacy(compacted_through: int = 2) -> tuple[str, dict]:
    tid = uuid.uuid4().hex
    doc = {
        "id": tid,
        "context_key": "general",
        "scope": "workshop",
        "mode": "direct",
        "title": "Subagents",
        "created_at": "2026-08-01T09:59:00+00:00",
        "updated_at": "2026-08-01T10:01:09+00:00",
        "summary": "they were explained",
        "compacted_through": compacted_through,
        "claude_session": "sess-legacy",
        "card_states": {"abc": {"ran": True}},
        "messages": [dict(m) for m in _LEGACY_MESSAGES],
    }
    d = chat_log.chat_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tid}.json").write_text(json.dumps(doc, indent=2))
    return tid, doc


class TestMigration:
    def test_a_legacy_thread_round_trips_exactly(self, owned):
        tid, doc = _write_legacy()
        assert chat_log.migrate_owner() == {"migrated": 1, "skipped": 0}
        t = chat_store.get_thread(tid)
        assert t["messages"] == _LEGACY_MESSAGES
        assert t["title"] == "Subagents"
        assert t["summary"] == "they were explained"
        assert t["claude_session"] == "sess-legacy"
        assert t["card_states"] == {"abc": {"ran": True}}
        assert t["created_at"] == doc["created_at"]

    def test_the_boundary_lands_on_the_same_messages(self, owned):
        tid, _ = _write_legacy(compacted_through=2)
        chat_log.migrate_owner()
        t = chat_store.get_thread(tid)
        assert t["compacted_through"] == 2
        folded_away = t["messages"][:t["compacted_through"]]
        assert [m["content"] for m in folded_away] == \
            ["how do subagents work", "like this"]
        # And it is stored as a seq, so appending cannot move it.
        assert chat_log.read_meta(tid)["boundary_seq"] == 2
        chat_store.append_message(tid, "user", "later")
        assert chat_store.get_thread(tid)["compacted_through"] == 2

    def test_messages_replay_as_seqs_one_through_n(self, owned):
        tid, _ = _write_legacy()
        chat_log.migrate_owner()
        assert _seqs(tid) == [1, 2, 3, 4]

    def test_an_unfolded_thread_has_no_boundary(self, owned):
        tid, _ = _write_legacy(compacted_through=0)
        chat_log.migrate_owner()
        assert chat_log.read_meta(tid)["boundary_seq"] == 0
        assert "compacted_through" not in chat_store.get_thread(tid)

    def test_the_original_is_kept_under_a_migrated_name(self, owned):
        tid, _ = _write_legacy()
        chat_log.migrate_owner()
        d = chat_log.chat_dir()
        assert not (d / f"{tid}.json").exists()
        assert (d / f"{tid}.json.migrated").exists()

    def test_running_it_again_changes_nothing(self, owned):
        tid, _ = _write_legacy()
        chat_log.migrate_owner()
        before = chat_log.log_path(tid).read_text()
        assert chat_log.migrate_owner() == {"migrated": 0, "skipped": 0}
        assert chat_log.log_path(tid).read_text() == before
        assert chat_store.get_thread(tid)["messages"] == _LEGACY_MESSAGES

    def test_a_migrated_thread_takes_new_messages_after_its_replay(self, owned):
        tid, _ = _write_legacy()
        chat_log.migrate_owner()
        chat_store.append_message(tid, "user", "next")
        t = chat_store.get_thread(tid)
        assert [m["content"] for m in t["messages"]][-1] == "next"
        assert _seqs(tid) == [1, 2, 3, 4, 5]

    def test_the_prefs_file_is_not_a_thread(self, owned):
        chat_store.set_default_mode("orchestrated")
        assert chat_log.migrate_owner() == {"migrated": 0, "skipped": 0}
        assert chat_store.get_default_mode() == "orchestrated"

    def test_an_unreadable_document_is_left_where_it_is(self, owned):
        d = chat_log.chat_dir()
        d.mkdir(parents=True, exist_ok=True)
        junk = d / f"{uuid.uuid4().hex}.json"
        junk.write_text("{ this is not json")
        assert chat_log.migrate_owner() == {"migrated": 0, "skipped": 1}
        assert junk.exists()

    def test_a_crash_before_the_rename_is_finished_not_repeated(self, owned):
        """The directory is built before the legacy file is renamed, so a crash
        in that window leaves both. The next run must adopt the directory, not
        replay the messages into it a second time."""
        tid, doc = _write_legacy()
        chat_log.migrate_owner()
        d = chat_log.chat_dir()
        (d / f"{tid}.json.migrated").rename(d / f"{tid}.json")   # as if it crashed
        assert chat_log.migrate_owner() == {"migrated": 1, "skipped": 0}
        assert chat_store.get_thread(tid)["messages"] == _LEGACY_MESSAGES
        assert _seqs(tid) == [1, 2, 3, 4]

    def test_every_owner_is_migrated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
        ids = {}
        for owner in (1, 2):
            ownership.set_current_owner(owner)
            ids[owner], _ = _write_legacy()
        ownership.clear_current_owner()
        assert chat_log.migrate_all_owners()["migrated"] == 2
        for owner, tid in ids.items():
            ownership.set_current_owner(owner)
            try:
                assert chat_store.get_thread(tid)["messages"] == _LEGACY_MESSAGES
            finally:
                ownership.clear_current_owner()
