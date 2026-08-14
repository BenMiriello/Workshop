"""The composer draft store, and the one property that makes a send stick.

A draft is mirrored to the server so it survives a reload and reaches other
surfaces. That means more than one surface can be holding the same unsent text,
and only ONE of them does the sending. Every other copy learns the message went
out by asking the server and being told the draft is empty.

So the tombstone is not bookkeeping, it is the correction channel, and these pin
that it can still do that job after the operator has been away. The bug they
exist for: tombstones expired after five minutes, a pruned row reads back as
`ts: 0`, `ts: 0` loses the last-write-wins comparison against any real
timestamp, and the surface that missed the clear therefore kept its copy and went
on showing an already-sent message indefinitely.
"""
import pytest

from services import draft_store, ownership


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield draft_store
    finally:
        ownership.clear_current_owner()


DAY_MS = 24 * 3600 * 1000


class TestASendClearsTheDraft:
    def test_clear_leaves_an_empty_row_with_a_timestamp(self, store):
        now = store._now_ms()
        store.put_draft("general::t1", "half a thought", now - 1000, "tab-a")
        store.clear_draft("general::t1", now)
        got = store.get_draft("general::t1")
        assert got["text"] == ""
        # The timestamp is the point: it is what a stale surface compares against.
        assert got["ts"] == now

    def test_a_write_from_before_the_clear_cannot_resurrect_it(self, store):
        """The debounced write that was already in flight when Send was pressed."""
        now = store._now_ms()
        store.clear_draft("general::t1", now)
        out = store.put_draft("general::t1", "half a thought", now - 1000, "tab-a")
        assert out.get("superseded") is True
        assert store.get_draft("general::t1")["text"] == ""

    def test_typing_again_after_a_send_is_a_new_draft(self, store):
        """The clear must not become a permanent block on the same key."""
        now = store._now_ms()
        store.clear_draft("general::t1", now)
        store.put_draft("general::t1", "a second thought", now + 1000, "tab-a")
        assert store.get_draft("general::t1")["text"] == "a second thought"


class TestTheClearOutlivesTheOperatorBeingAway:
    """The regression that produced "I sent it and it is still in the box"."""

    def test_a_tombstone_survives_long_enough_to_correct_a_sleeping_tab(self, store):
        # Sent a day ago. A second tab, or a phone, has been closed since and
        # still holds the text; when it wakes it asks the server about this key.
        sent_at = store._now_ms() - DAY_MS
        store.put_draft("general::t1", "already sent this", sent_at - 1000, "tab-a")
        store.clear_draft("general::t1", sent_at)
        # Something else writes, which is what triggers a prune.
        store.put_draft("general::other", "unrelated", store._now_ms(), "tab-b")

        got = store.get_draft("general::t1")
        assert got["text"] == ""
        # NOT ts 0. A zero here is the whole bug: it loses the ordering
        # comparison against the stale copy's own timestamp, so the stale copy
        # wins and there is no path back.
        assert got["ts"] == sent_at, "the clear must still be able to correct a stale surface"

    def test_a_stale_surface_can_tell_the_clear_is_newer_than_its_copy(self, store):
        """The comparison the client actually makes, spelled out."""
        typed_at = store._now_ms() - DAY_MS - 5000
        store.put_draft("general::t1", "already sent this", typed_at, "phone")
        store.clear_draft("general::t1", typed_at + 5000)
        store.put_draft("general::other", "unrelated", store._now_ms(), "tab-b")

        server = store.get_draft("general::t1")
        assert server["ts"] > typed_at   # so the client adopts the server copy
        assert server["text"] == ""      # and what it adopts is empty


class TestDeadRowsAreReclaimed:
    def test_an_abandoned_draft_is_eventually_dropped(self, store):
        """A key nothing reads any more must not sit here for the life of the
        install. Found in the real store: a bare context key `budget` holding
        text from ten days earlier, written by a client version that predates
        per-chat draft keys, which nothing could ever clear."""
        ancient = store._now_ms() - (store.STALE_DRAFT_TTL_S + 86400) * 1000
        store.put_draft("budget", "… oh I see", ancient, "old-client")
        store.put_draft("general::t1", "current", store._now_ms(), "tab-a")
        assert store.get_draft("budget")["text"] == ""

    def test_an_old_but_not_ancient_draft_is_kept(self, store):
        """Unsent writing is the operator's, and is not thrown away for being a
        few days old."""
        recent = store._now_ms() - 3 * DAY_MS
        store.put_draft("general::t1", "still writing this", recent, "tab-a")
        store.put_draft("general::t2", "trigger a prune", store._now_ms(), "tab-a")
        assert store.get_draft("general::t1")["text"] == "still writing this"


class TestClearingAWholeConversation:
    def test_deleting_a_thread_clears_its_draft(self, store):
        store.put_draft("general::t1", "unsent", store._now_ms(), "tab-a")
        store.clear_for_thread("general", "t1")
        assert store.get_draft("general::t1")["text"] == ""

    def test_it_leaves_other_conversations_alone(self, store):
        now = store._now_ms()
        store.put_draft("general::t1", "one", now, "tab-a")
        store.put_draft("general::t2", "two", now, "tab-a")
        store.clear_for_thread("general", "t1")
        assert store.get_draft("general::t2")["text"] == "two"
