"""`list_threads` caches the fold. Phase 5 of the chat-performance fix:
`_fold` replays and JSON-parses a thread's whole event log to build one
sidebar row, so `list_threads` used to pay that cost for every thread on
every call. These tests pin the cache's observable contract: a hit skips the
fold, a miss folds and stores, and every writer that can change a row
(a new message, a meta-only change like a rename, or a rotation moving events
into the archive half of the log) invalidates it.
"""
import pytest

from services import chat_log, chat_store, ownership


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    # One-second timestamp resolution means anything done within the same
    # wall-clock second ties on `last_user_at`; give the store a monotonic
    # clock so ordering assertions are about the rule, not a race with time.time().
    ticks = iter(range(1, 10_000))
    monkeypatch.setattr(chat_store, "_now_iso",
                         lambda: f"2026-08-14T05:{next(ticks):04d}+00:00")
    chat_store._SUMMARY_CACHE.clear()
    try:
        yield
    finally:
        ownership.clear_current_owner()
        chat_store._SUMMARY_CACHE.clear()


def _fold_call_counter(monkeypatch):
    """Wrap `chat_store._fold` to count invocations, without changing what it
    returns. `_summary_row` (and therefore the cache) is the only caller in
    this module's read path."""
    calls = {"n": 0}
    real_fold = chat_store._fold

    def counting_fold(thread_id):
        calls["n"] += 1
        return real_fold(thread_id)

    monkeypatch.setattr(chat_store, "_fold", counting_fold)
    return calls


class TestHitAndMiss:
    def test_first_call_folds_every_thread(self, store, monkeypatch):
        a = chat_store.create_thread("general")["id"]
        b = chat_store.create_thread("general")["id"]
        chat_store.append_message(a, "user", "hi")
        chat_store.append_message(b, "user", "hey")
        calls = _fold_call_counter(monkeypatch)
        rows = chat_store.list_threads()
        assert {r["id"] for r in rows} == {a, b}
        assert calls["n"] == 2

    def test_second_call_is_a_pure_cache_hit(self, store, monkeypatch):
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "hi")
        chat_store.list_threads()  # warms the cache
        calls = _fold_call_counter(monkeypatch)
        rows = chat_store.list_threads()
        assert calls["n"] == 0
        assert rows[0]["id"] == tid

    def test_cache_hit_and_miss_produce_identical_rows(self, store):
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "hi")
        cold = chat_store.list_threads()
        warm = chat_store.list_threads()
        assert cold == warm
        # Independent objects: mutating the returned row must not corrupt
        # what the cache hands back next time.
        warm[0]["title"] = "mutated by a careless caller"
        assert chat_store.list_threads()[0]["title"] != "mutated by a careless caller"


class TestInvalidation:
    def test_a_new_message_invalidates(self, store):
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "first message")
        before = chat_store.list_threads()[0]
        assert before["message_count"] == 1

        chat_store.append_message(tid, "user", "second message, changing the preview")
        after = chat_store.list_threads()[0]
        assert after["message_count"] == 2
        assert after["last_message"] == "second message, changing the preview"
        assert after["last_user_at"] != before["last_user_at"]

    def test_a_new_message_reorders_the_list(self, store):
        old = chat_store.create_thread("general")["id"]
        chat_store.append_message(old, "user", "go do the big thing")
        recent = chat_store.create_thread("general")["id"]
        chat_store.append_message(recent, "user", "quick question")
        assert chat_store.list_threads()[0]["id"] == recent

        # The operator goes back to the older chat -- the cached row for
        # `recent` must stay untouched while `old`'s row and the ordering move.
        chat_store.append_message(old, "user", "actually one more thing")
        assert chat_store.list_threads()[0]["id"] == old

    def test_a_meta_only_change_invalidates_with_no_log_write(self, store):
        """Title lives in meta.json, not the log. A rename must be visible on
        the very next call even though `log.jsonl` never changes."""
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "hi")
        before_fp = None
        chat_store.list_threads()
        before_fp = chat_store._SUMMARY_CACHE[(1, tid)][0]

        chat_store.rename_thread(tid, "renamed")

        after_fp = chat_store._fold_fingerprint(tid)
        assert after_fp != before_fp
        row = chat_store.list_threads()[0]
        assert row["title"] == "renamed"

    def test_archiving_invalidates_and_moves_lists(self, store):
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "hi")
        chat_store.list_threads()  # warm the cache

        chat_store.archive_thread(tid)
        assert chat_store.list_threads() == []
        archived = chat_store.list_threads(archived=True)
        assert archived[0]["id"] == tid
        assert archived[0]["archived_at"]

    def test_rotation_moving_events_into_the_archive_half_invalidates(self, store):
        """`_fold` reads `log.jsonl` AND `log.1.jsonl` as one log
        (chat_log.read_events), so a rotation that appears between calls has to
        be seen even though it never touches `log.jsonl`'s own stat in a way
        that necessarily differs. Simulate the file-level effect directly
        rather than fighting `maybe_rotate_sync`'s size/floor gating, since
        what this cache promises is keyed on the archive file's own stat."""
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "seed")
        chat_store.list_threads()  # warm the cache
        before_fp = chat_store._SUMMARY_CACHE[(1, tid)][0]
        assert before_fp[1] is None  # no archive half yet

        chat_log.archive_path(tid).write_text('{"seq": 0, "kind": "noop"}\n')

        after_fp = chat_store._fold_fingerprint(tid)
        assert after_fp != before_fp
        assert after_fp[1] is not None
        # And the cache actually re-folds and re-stores under the new print,
        # rather than getting stuck returning the stale one.
        chat_store.list_threads()
        assert chat_store._SUMMARY_CACHE[(1, tid)][0] == after_fp

    def test_deleting_a_thread_evicts_it_from_the_cache_immediately(self, store):
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "hi")
        chat_store.list_threads()  # warm the cache
        assert (1, tid) in chat_store._SUMMARY_CACHE

        chat_store.delete_thread(tid)
        assert (1, tid) not in chat_store._SUMMARY_CACHE
        assert chat_store.list_threads() == []

    def test_a_thread_deleted_out_of_band_is_pruned_on_next_list(self, store):
        """Even without going through `delete_thread` (e.g. the directory was
        removed by something else), the next `list_threads` must not keep
        serving a cached row for a thread that no longer exists."""
        import shutil

        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "hi")
        chat_store.list_threads()
        assert (1, tid) in chat_store._SUMMARY_CACHE

        shutil.rmtree(chat_log.thread_dir(tid))

        assert chat_store.list_threads() == []
        assert (1, tid) not in chat_store._SUMMARY_CACHE


class TestFingerprintFallback:
    def test_unfingerprintable_thread_still_folds_correctly(self, store, monkeypatch):
        """If the fingerprint can't be computed, the row must still be right --
        just uncached. Correctness over speed."""
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "hi")
        monkeypatch.setattr(chat_store, "_fold_fingerprint", lambda thread_id: None)
        row = chat_store.list_threads()[0]
        assert row["id"] == tid
        assert row["message_count"] == 1
        assert (1, tid) not in chat_store._SUMMARY_CACHE
