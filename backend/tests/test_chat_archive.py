"""Archiving is the reversible gesture; deleting is the exception behind it.

Closing a chat is something the operator does constantly and destroying one is
rare, so the constant gesture is the one that keeps the conversation. These pin
the rules that make that true: the two lists are disjoint, restoring is the exact
inverse of archiving, and the archive is ordered by when things were put in it.
"""
import pytest

from services import chat_store, ownership


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    # Stored timestamps have one-second resolution, so everything done inside the
    # same second ties and order falls to the directory listing. A monotonic
    # clock keeps these tests about the RULE rather than about ties.
    ticks = iter(range(1, 10_000))
    monkeypatch.setattr(chat_store, "_now_iso",
                        lambda: f"2026-08-12T19:{next(ticks):04d}+00:00")
    try:
        yield
    finally:
        ownership.clear_current_owner()


def _live():
    return [t["id"] for t in chat_store.list_threads()]


def _archived():
    return [t["id"] for t in chat_store.list_threads(archived=True)]


class TestTheTwoListsAreDisjoint:
    def test_an_archived_chat_leaves_the_list_and_joins_the_archive(self, store):
        a = chat_store.create_thread("general")["id"]
        b = chat_store.create_thread("general")["id"]
        chat_store.archive_thread(a)
        assert _live() == [b]
        assert _archived() == [a]

    def test_a_live_chat_is_never_in_the_archive(self, store):
        chat_store.create_thread("general")
        assert _archived() == []

    def test_the_context_filter_still_applies_inside_the_archive(self, store):
        a = chat_store.create_thread("general")["id"]
        b = chat_store.create_thread("project:x")["id"]
        chat_store.archive_thread(a)
        chat_store.archive_thread(b)
        assert [t["id"] for t in chat_store.list_threads("project:x", archived=True)] == [b]


class TestRestoreIsTheInverse:
    def test_restoring_puts_it_back_in_the_list(self, store):
        tid = chat_store.create_thread("general")["id"]
        chat_store.archive_thread(tid)
        chat_store.restore_thread(tid)
        assert _live() == [tid]
        assert _archived() == []

    def test_a_restored_thread_carries_no_trace_of_the_round_trip(self, store):
        """`archived_at` is REMOVED, not blanked. A falsy-but-present field is
        the shape that makes every later reader have to know which falsy value
        means "live", and one of them always gets it wrong."""
        tid = chat_store.create_thread("general")["id"]
        chat_store.archive_thread(tid)
        chat_store.restore_thread(tid)
        assert "archived_at" not in (chat_store.get_thread(tid) or {})

    def test_the_conversation_itself_is_untouched(self, store):
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "still here?")
        chat_store.archive_thread(tid)
        chat_store.restore_thread(tid)
        msgs = (chat_store.get_thread(tid) or {}).get("messages") or []
        assert [m["content"] for m in msgs] == ["still here?"]

    def test_restoring_a_thread_that_is_not_there_reports_it(self, store):
        assert chat_store.restore_thread("0" * 32) is None

    def test_archiving_a_thread_that_is_not_there_reports_it(self, store):
        assert chat_store.archive_thread("0" * 32) is None

    def test_an_id_that_is_not_even_an_id_reports_the_same_way(self, store):
        """A junk id must be NOT THERE, not an exception: the route turns None
        into a 404, and an escaping ValueError into a 500."""
        assert chat_store.archive_thread("../../etc") is None
        assert chat_store.restore_thread("nope") is None


class TestOrdering:
    def test_the_archive_is_ordered_by_when_things_were_archived(self, store):
        """Not by the conversation's own activity. You come here looking for the
        one you just put here, and ordering by last-spoken buries it under
        whatever old chat you archived after it."""
        old_talk = chat_store.create_thread("general")["id"]
        chat_store.append_message(old_talk, "user", "a long time ago")
        recent_talk = chat_store.create_thread("general")["id"]
        chat_store.append_message(recent_talk, "user", "just now")
        # Archived in the opposite order to when they were last spoken in.
        chat_store.archive_thread(recent_talk)
        chat_store.archive_thread(old_talk)
        assert _archived() == [old_talk, recent_talk]

    def test_archiving_does_not_disturb_the_live_list_order(self, store):
        a = chat_store.create_thread("general")["id"]
        chat_store.append_message(a, "user", "first")
        b = chat_store.create_thread("general")["id"]
        chat_store.append_message(b, "user", "second")
        c = chat_store.create_thread("general")["id"]
        chat_store.append_message(c, "user", "third")
        chat_store.archive_thread(b)
        assert _live() == [c, a]


class TestTheSummary:
    def test_an_archived_summary_says_when(self, store):
        tid = chat_store.create_thread("general")["id"]
        chat_store.archive_thread(tid)
        assert chat_store.list_threads(archived=True)[0]["archived_at"]

    def test_a_live_summary_does_not(self, store):
        chat_store.create_thread("general")
        assert chat_store.list_threads()[0]["archived_at"] is None
