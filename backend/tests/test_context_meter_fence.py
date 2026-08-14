"""The context measurement is fenced to the session it describes.

THE BUG THIS PINS, measured 2026-08-14 on thread 57e6c4b8: the operator pressed
compact at 03:20; the fold landed at 03:20:38, cleared the stale meter reading
and detached the session; the turn already in flight finalized at 03:20:39 and
wrote its measurement (467,005 tokens, taken against the session the fold had
just thrown away) back over the clear. The meter then read 47% on a freshly
compacted chat for ten minutes, until the next turn happened to end and write a
fresh number.

The rule: a context measurement is a fact about ONE live session. It may land
only while the thread still owns that session. A write that loses the fence is
dropped, not queued: the very next real turn produces a current number.
"""
import pytest

from services import chat_store, ownership


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
    t = chat_store.create_thread("general")
    chat_store.append_message(t["id"], "user", "hello")
    return t["id"]


class TestTheFence:
    def test_a_measurement_of_the_owned_session_lands(self, tid):
        sid, _ = chat_store.ensure_thread_session(tid)
        chat_store.set_thread_context_tokens(tid, 12345, measured_session=sid)
        assert chat_store.get_thread(tid).get("context_tokens") == 12345

    def test_a_measurement_of_a_detached_session_is_dropped(self, tid):
        """The incident: fold detaches the session, the in-flight turn's
        finalize must not resurrect its pre-fold measurement."""
        sid, _ = chat_store.ensure_thread_session(tid)
        chat_store.set_thread_context_tokens(tid, 467005, measured_session=sid)
        chat_store.clear_thread_session(tid)          # the fold's detach
        chat_store.clear_thread_context_tokens(tid)   # the fold's meter clear
        chat_store.set_thread_context_tokens(tid, 467005, measured_session=sid)
        assert "context_tokens" not in (chat_store.get_thread(tid) or {})

    def test_a_measurement_of_a_replaced_session_is_dropped(self, tid):
        """Detach then a new turn: the OLD turn's late write loses to the new
        session's ownership, whatever order the finalizes land in."""
        old, _ = chat_store.ensure_thread_session(tid)
        chat_store.clear_thread_session(tid)
        new, is_new = chat_store.ensure_thread_session(tid)
        assert is_new and new != old
        chat_store.set_thread_context_tokens(tid, 900_000, measured_session=old)
        assert "context_tokens" not in (chat_store.get_thread(tid) or {})
        chat_store.set_thread_context_tokens(tid, 15_000, measured_session=new)
        assert chat_store.get_thread(tid).get("context_tokens") == 15_000

    def test_an_agent_session_counts_as_owned(self, tid):
        """Agent chats measure against their (agent, thread) session, which
        lives in agent_sessions, not claude_session."""
        sid, _ = chat_store.ensure_agent_session(tid, "helper")
        chat_store.set_thread_context_tokens(tid, 22222, measured_session=sid)
        assert chat_store.get_thread(tid).get("context_tokens") == 22222

    def test_no_session_stated_means_no_fence(self, tid):
        """Callers with nothing to compare keep the old behavior."""
        chat_store.set_thread_context_tokens(tid, 31337)
        assert chat_store.get_thread(tid).get("context_tokens") == 31337
