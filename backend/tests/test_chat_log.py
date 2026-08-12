"""The append-only chat log: seqs, idempotency, the write lock, and migration.

These pin the properties the rest of the chat system is now allowed to assume
(docs/chat-rebuild-spec-2026-08-09.md):

  - a seq is 1-based, monotonic per thread, and never reused, so it is a cursor
    that survives anything appended later — unlike the array index it replaced;
  - a client_id is committed once, so a browser retrying a send cannot double
    the message;
  - one writer at a time, so concurrent appends leave no gaps and no reordering;
  - a patch writes the fields it names and nothing else;
  - the live log has a bounded size, and rotating its head into the archive
    changes nothing a reader can observe: not a seq, not an order, not a cursor
    handed out before the rotation, and not a single event;
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


@pytest.fixture()
def small_rotation(monkeypatch):
    """The real thresholds in miniature, so a test can cross them in a few KB.

    Proportions are kept: rotate at 4KB down to a 1KB tail, never fewer than a
    handful of events live.
    """
    monkeypatch.setattr(chat_log, "ROTATE_BYTES", 4096)
    monkeypatch.setattr(chat_log, "KEEP_BYTES", 1024)
    monkeypatch.setattr(chat_log, "KEEP_MIN_EVENTS", 5)
    monkeypatch.setattr(chat_log, "ROTATE_RETRY_EVENTS", 3)


def _fill(thread_id, n, prefix="m", size=200):
    """`n` messages fat enough that a few dozen cross the small threshold."""
    for i in range(n):
        chat_log.append_message_sync(thread_id, "user", f"{prefix}{i}:" + "x" * size)


def _file_seqs(path):
    if not path.exists():
        return []
    return [json.loads(ln)["seq"] for ln in path.read_text().splitlines() if ln.strip()]


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


class TestRotation:
    """The live log is bounded. The record is not: nothing is ever dropped."""

    def test_a_small_thread_never_grows_an_archive(self, tid, small_rotation):
        _fill(tid, 5)
        assert not chat_log.archive_path(tid).exists()
        assert _seqs(tid) == [1, 2, 3, 4, 5]

    def test_the_live_log_stops_growing_at_the_threshold(self, tid, small_rotation):
        for i in range(60):
            _fill(tid, 1, prefix=f"m{i}-")
            assert chat_log.log_path(tid).stat().st_size <= chat_log.ROTATE_BYTES
        assert chat_log.archive_path(tid).exists()

    def test_every_event_survives_the_rotation_in_order(self, tid, small_rotation):
        _fill(tid, 60)
        events = chat_log.read_events(tid)
        assert [e["seq"] for e in events] == list(range(1, 61))
        assert [e["content"].split(":")[0] for e in events] == [f"m{i}" for i in range(60)]
        # ...and they really did move: the live file no longer holds the head.
        assert _file_seqs(chat_log.log_path(tid))[0] > 1
        assert _file_seqs(chat_log.archive_path(tid))[0] == 1

    def test_the_two_files_partition_the_log_exactly(self, tid, small_rotation):
        _fill(tid, 60)
        archived = _file_seqs(chat_log.archive_path(tid))
        live = _file_seqs(chat_log.log_path(tid))
        assert archived + live == list(range(1, 61))

    def test_seqs_keep_counting_after_a_rotation(self, tid, small_rotation):
        _fill(tid, 60)
        ev = chat_log.append_message_sync(tid, "user", "next")
        assert ev["seq"] == 61
        assert chat_log.read_meta(tid)["last_seq"] == 61

    def test_a_cursor_from_before_the_rotation_still_resolves(self, tid, small_rotation):
        _fill(tid, 6)
        cursor = chat_log.read_meta(tid)["last_seq"]        # a client polls, then goes quiet
        _fill(tid, 54, prefix="later")
        assert cursor < _file_seqs(chat_log.log_path(tid))[0]   # its cursor is now archived
        got = chat_log.read_events(tid, after=cursor)
        assert [e["seq"] for e in got] == list(range(cursor + 1, 61))
        assert [e["content"].split(":")[0] for e in got] == [f"later{i}" for i in range(54)]

    def test_a_cursor_at_the_very_start_returns_the_whole_log(self, tid, small_rotation):
        _fill(tid, 60)
        assert [e["seq"] for e in chat_log.read_events(tid, after=0)] == list(range(1, 61))

    def test_a_limit_pages_across_the_boundary(self, tid, small_rotation):
        _fill(tid, 60)
        first_live = _file_seqs(chat_log.log_path(tid))[0]
        page = chat_log.read_events(tid, after=first_live - 3, limit=6)
        assert [e["seq"] for e in page] == list(range(first_live - 2, first_live + 4))

    def test_a_current_cursor_never_opens_the_archive(self, tid, small_rotation, monkeypatch):
        """The poll path is the hot one: it must not pay for the cold half."""
        _fill(tid, 60)
        opened = []
        real = chat_log._read_rows
        monkeypatch.setattr(chat_log, "_read_rows",
                            lambda p: (opened.append(p.name), real(p))[1])
        last = chat_log.read_meta(tid)["last_seq"]
        chat_log.read_events(tid, after=last - 1)
        assert opened == [chat_log.LOG_NAME]

    def test_read_thread_tails_across_the_boundary(self, tid, small_rotation):
        _fill(tid, 60)
        got = chat_log.read_thread(tid, tail=3)
        assert [e["seq"] for e in got["events"]] == [58, 59, 60]
        assert got["last_seq"] == 60

    def test_concurrent_appends_still_leave_no_gaps(self, tid, small_rotation):
        async def main():
            await asyncio.gather(*[
                chat_log.append_message(tid, "user", f"m{i}:" + "x" * 200)
                for i in range(60)
            ])

        asyncio.run(main())
        assert _seqs(tid) == list(range(1, 61))
        assert chat_log.archive_path(tid).exists()


class TestRotationLeavesTheHotEventsAlone:
    def test_the_context_boundary_and_everything_after_it_stay_live(
            self, tid, small_rotation):
        """`boundary_seq` is where the model's context starts. That much of the
        thread is still being handed to a model on every turn, so it does not go
        cold — and the boundary itself must still be findable where it was."""
        _fill(tid, 10)
        cut = chat_log.append_sync(tid, {"kind": "boundary", "mode": "compact"})
        chat_log.patch_meta_sync(tid, boundary_seq=cut["seq"])
        _fill(tid, 50, prefix="after")

        assert chat_log.read_meta(tid)["boundary_seq"] == cut["seq"]
        live = _file_seqs(chat_log.log_path(tid))
        assert live[0] <= cut["seq"]
        assert cut["seq"] in live
        assert all(s < cut["seq"] for s in _file_seqs(chat_log.archive_path(tid)))
        assert _seqs(tid) == list(range(1, 62))

    def test_a_boundary_at_the_head_blocks_rotation_rather_than_moving_it(
            self, tid, small_rotation):
        _fill(tid, 2)
        chat_log.patch_meta_sync(tid, boundary_seq=1)
        _fill(tid, 40, prefix="after")
        assert not chat_log.archive_path(tid).exists()
        assert _seqs(tid) == list(range(1, 43))

    def test_a_blocked_thread_is_not_rescanned_on_every_append(
            self, tid, small_rotation, monkeypatch):
        """Nothing movable is a valid answer, but recomputing it per append means
        reading the whole oversized file per append — the cost rotation exists to
        remove."""
        _fill(tid, 2)
        chat_log.patch_meta_sync(tid, boundary_seq=1)
        _fill(tid, 30, prefix="fill")                 # well past the threshold
        scans = []
        real = chat_log._read_rows
        monkeypatch.setattr(chat_log, "_read_rows",
                            lambda p: (scans.append(p.name), real(p))[1])
        _fill(tid, 30, prefix="more")
        assert 0 < len(scans) <= 30 // chat_log.ROTATE_RETRY_EVENTS + 1

    def test_a_log_of_fat_events_does_not_rotate_on_every_append(
            self, tid, small_rotation, monkeypatch):
        """`KEEP_MIN_EVENTS` of big enough events blows the threshold on its own.
        Rotating one event per append — rewriting the file each time — is the
        cost this function exists to remove, so it backs off instead."""
        _fill(tid, 40, size=900)                      # 5 kept events > the 4KB threshold
        assert chat_log.log_path(tid).stat().st_size > chat_log.ROTATE_BYTES
        rewrites = []
        real = chat_log.Path.replace
        monkeypatch.setattr(chat_log.Path, "replace", lambda s, t: (
            rewrites.append(t.name) if t.name == chat_log.LOG_NAME else None,
            real(s, t))[1])
        _fill(tid, 30, prefix="more", size=900)
        assert 0 < len(rewrites) <= 30 // chat_log.ROTATE_RETRY_EVENTS + 1
        assert _seqs(tid) == list(range(1, 71))

    def test_a_run_in_flight_keeps_its_marker_live(self, tid, small_rotation):
        _fill(tid, 10)
        started = chat_log.append_sync(
            tid, {"kind": "run", "run_id": "r1", "state": "started"})
        chat_log.patch_meta_sync(tid, active_run_id="r1", active_run_seq=started["seq"])
        _fill(tid, 50, prefix="during")

        assert chat_log.active_run(tid) == {
            "run_id": "r1", "started_seq": started["seq"], "live": None}
        assert started["seq"] in _file_seqs(chat_log.log_path(tid))
        assert _seqs(tid) == list(range(1, 62))

    def test_an_older_run_marker_is_still_found_after_it_is_archived(
            self, tid, small_rotation):
        """A run opened by a build that did not record `active_run_seq` falls back
        to scanning the events. The scan spans the archive, so the fallback keeps
        working on a thread that has rotated."""
        started = chat_log.append_sync(
            tid, {"kind": "run", "run_id": "r1", "state": "started"})
        chat_log.patch_meta_sync(tid, active_run_id="r1")
        _fill(tid, 60, prefix="during")
        assert started["seq"] not in _file_seqs(chat_log.log_path(tid))
        assert chat_log.active_run(tid)["started_seq"] == started["seq"]


class TestClientIdAcrossTheBoundary:
    def test_a_resend_of_an_archived_message_still_appends_nothing(
            self, tid, small_rotation):
        first = chat_log.append_message_sync(
            tid, "user", "the real one", client_id="c-1")
        _fill(tid, 60)
        assert first["seq"] not in _file_seqs(chat_log.log_path(tid))
        assert first["seq"] in _file_seqs(chat_log.archive_path(tid))

        again = chat_log.append_message_sync(
            tid, "user", "a garbled retry", client_id="c-1")
        assert again == first
        assert chat_log.read_meta(tid)["last_seq"] == 61
        assert _seqs(tid) == list(range(1, 62))

    def test_a_new_client_id_still_lands_after_a_rotation(self, tid, small_rotation):
        _fill(tid, 60)
        ev = chat_log.append_message_sync(tid, "user", "fresh", client_id="c-2")
        assert ev["seq"] == 61
        assert chat_log.find_message_by_client_id(tid, "c-2") == ev


class TestRotationIsCrashSafe:
    def test_a_crash_before_the_live_file_is_replaced_duplicates_nothing(
            self, tid, small_rotation):
        """Step one of a rotation is durable and step two has not run: the head is
        in both files. A reader must see one log, not one and a half."""
        _fill(tid, 10)
        lines = chat_log.log_path(tid).read_text().splitlines()
        chat_log.archive_path(tid).write_text("\n".join(lines[:4]) + "\n")
        assert _seqs(tid) == list(range(1, 11))
        assert chat_log.find_message_by_client_id(tid, "nope") is None

    def test_a_rotation_replayed_after_a_crash_duplicates_nothing(
            self, tid, small_rotation):
        _fill(tid, 60)
        arc = chat_log.archive_path(tid)
        arc.write_text(arc.read_text() + arc.read_text())   # the same head, twice
        assert _seqs(tid) == list(range(1, 61))

    def test_a_torn_archive_line_does_not_swallow_the_next_one(
            self, tid, small_rotation):
        """An append interrupted mid-line loses that line and nothing else: the
        next rotation closes the torn line off before writing after it."""
        _fill(tid, 30)
        arc = chat_log.archive_path(tid)
        text = arc.read_text()
        torn_seq = _file_seqs(arc)[-1]
        arc.write_text(text[:-12])                          # a half-written last line
        _fill(tid, 40, prefix="more")                       # forces another rotation

        seqs = _seqs(tid)
        assert seqs == [s for s in range(1, 71) if s != torn_seq]
        assert chat_log.read_events(tid, after=torn_seq)[0]["seq"] == torn_seq + 1

    def test_the_thread_is_readable_at_every_point_of_a_rotation(
            self, tid, small_rotation, monkeypatch):
        """Whatever the rotation is in the middle of, `read_events` answers with
        the whole log. The failure this rules out is a window with no log at all."""
        _fill(tid, 30)
        seen = []
        real_replace = chat_log.Path.replace

        def watched_replace(self, target):
            if target.name == chat_log.LOG_NAME:
                # The archive is durable and the live file has not been swapped
                # yet — the one instant where the head exists in both.
                seen.append(_seqs(tid))
            return real_replace(self, target)

        monkeypatch.setattr(chat_log.Path, "replace", watched_replace)
        _fill(tid, 40, prefix="more")
        assert len(seen) >= 2
        assert all(s == list(range(1, len(s) + 1)) for s in seen)
        assert _seqs(tid) == list(range(1, 71))

    def test_a_rotation_that_fails_does_not_fail_the_append(
            self, tid, small_rotation, monkeypatch):
        _fill(tid, 30)
        monkeypatch.setattr(chat_log, "maybe_rotate_sync",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        ev = chat_log.append_message_sync(tid, "user", "still committed")
        assert ev["content"] == "still committed"
        assert chat_log.read_events(tid, after=ev["seq"] - 1)[0] == ev


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

    def test_a_legacy_fold_boundary_is_inert_and_the_messages_come_back(self, owned):
        """A legacy `compacted_through` came from server-side folding, which was
        removed on 2026-08-12. It hid messages behind a running summary that is
        no longer assembled into any prompt, so honouring it would hide them
        behind nothing at all. Only `/clear` cuts now, and migration writes no
        clear event, so the conversation is whole again.

        The cached `boundary_seq` is still migrated faithfully. It is simply not
        what decides any more."""
        tid, _ = _write_legacy(compacted_through=2)
        chat_log.migrate_owner()
        t = chat_store.get_thread(tid)
        assert "cleared_through" not in t
        assert [m["content"] for m in t["messages"]] == \
            [m["content"] for m in _LEGACY_MESSAGES]
        assert chat_log.read_meta(tid)["boundary_seq"] == 2

    def test_messages_replay_as_seqs_one_through_n(self, owned):
        tid, _ = _write_legacy()
        chat_log.migrate_owner()
        assert _seqs(tid) == [1, 2, 3, 4]

    def test_an_unfolded_thread_has_no_boundary(self, owned):
        tid, _ = _write_legacy(compacted_through=0)
        chat_log.migrate_owner()
        assert chat_log.read_meta(tid)["boundary_seq"] == 0
        assert "cleared_through" not in chat_store.get_thread(tid)

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
