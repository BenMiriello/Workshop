"""The chats list answers "where was I", so the OPERATOR's last message orders it.

`updated_at` moves for reasons that have nothing to do with the operator: a turn
finishing, an agent reporting back, an auto-name, a streaming placeholder being
rewritten. Ordering by it floats whichever chat a background process touched most
recently to the top, above the conversation the operator was actually in.
"""
import pytest

from services import chat_store, ownership


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    # Stored timestamps have one-second resolution, so everything a test does in
    # the same second ties and the order falls to whatever the directory listing
    # returns. Hand the store a monotonic clock: these tests are about the
    # ordering RULE, and a tie is a different question from the wrong key.
    ticks = iter(range(1, 10_000))
    monkeypatch.setattr(chat_store, "_now_iso",
                        lambda: f"2026-08-07T05:{next(ticks):04d}+00:00")
    try:
        yield
    finally:
        ownership.clear_current_owner()


def _ids():
    return [t["id"] for t in chat_store.list_threads()]


class TestOrdering:
    def test_the_chat_the_operator_spoke_in_last_is_first(self, store):
        a = chat_store.create_thread("general")["id"]
        b = chat_store.create_thread("general")["id"]
        chat_store.append_message(a, "user", "first")
        chat_store.append_message(b, "user", "second")
        assert _ids()[0] == b

    def test_an_assistant_reply_does_not_reorder_the_list(self, store):
        """The reported symptom. A long turn finishing in an old chat must not
        push it above the chat the operator just typed in."""
        old = chat_store.create_thread("general")["id"]
        chat_store.append_message(old, "user", "go do the big thing")
        recent = chat_store.create_thread("general")["id"]
        chat_store.append_message(recent, "user", "quick question")
        chat_store.append_message(old, "assistant", "done, here is the report")
        assert _ids()[0] == recent

    def test_a_brand_new_chat_sorts_by_creation(self, store):
        """It has no user message yet and still has to be reachable at the top."""
        spoken = chat_store.create_thread("general")["id"]
        chat_store.append_message(spoken, "user", "hi")
        blank = chat_store.create_thread("general")["id"]
        assert _ids()[0] == blank

    def test_the_summary_reports_when_the_operator_last_spoke(self, store):
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "hi")
        chat_store.append_message(tid, "assistant", "hello")
        summary = chat_store.list_threads()[0]
        user_ts = [m["ts"] for m in chat_store.get_thread(tid)["messages"] if m["role"] == "user"][-1]
        assert summary["last_user_at"] == user_ts


class TestDefaultModePreference:
    def test_the_platform_default_applies_when_nothing_is_set(self, store):
        assert chat_store.get_default_mode() == chat_store.DEFAULT_CHAT_MODE
        assert chat_store.create_thread("general")["mode"] == "direct"

    def test_a_new_chat_starts_in_the_operators_default(self, store):
        chat_store.set_default_mode("orchestrated")
        assert chat_store.create_thread("general")["mode"] == "orchestrated"

    def test_an_explicit_mode_still_wins(self, store):
        chat_store.set_default_mode("orchestrated")
        assert chat_store.create_thread("general", mode="direct")["mode"] == "direct"

    def test_changing_the_default_leaves_existing_chats_alone(self, store):
        """"Default" means the mode a NEW chat starts in. Rewriting live chats
        would silently move work out of conversations already in flight."""
        tid = chat_store.create_thread("general")["id"]
        chat_store.set_default_mode("orchestrated")
        assert chat_store.get_thread(tid)["mode"] == "direct"

    def test_junk_is_clamped_rather_than_stored(self, store):
        assert chat_store.set_default_mode("nonsense") == "direct"

    def test_the_prefs_file_is_not_a_thread(self, store):
        """It lives in the same directory as the threads, like drafts.json. A
        list that reads it as a thread 500s or shows a ghost chat."""
        chat_store.set_default_mode("orchestrated")
        chat_store.create_thread("general")
        assert len(chat_store.list_threads()) == 1
