"""Re-scoping a chat moves the persona, not just the cwd.

`POST /threads/{id}/rescope` wrote `context_key` and nothing else. The next turn
RESUMED the thread's existing claude session, and a resumed session is handed
only the new message — the persona, the protocol blocks and the project briefing
were assembled once, from the OLD context. So the chat got the new scope's
working directory and write tools while the model still believed it was in the
old scope: a build chat over one project, instructed as another.

Dropping the session is the whole fix. The next turn re-assembles the full prompt
(summary + transcript tail) for a fresh session, which is the same path a chat
whose session was pruned from disk already takes, so nothing is forgotten.
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


from tests.conftest import requires_app


def _rescope(thread_id: str, key: str):
    import routes.chat as rc
    return asyncio.run(rc.rescope_thread(thread_id, rc.RescopeThread(context_key=key)))


def test_the_live_session_is_dropped_with_the_scope(thread):
    requires_app("budget")
    tid = thread["id"]
    sid, _ = chat_store.ensure_thread_session(tid)
    assert chat_store.get_thread(tid)["claude_session"] == sid

    out = _rescope(tid, "budget")

    assert out["context_key"] == "budget"
    # The persona travels with the tools, or neither has really moved.
    assert chat_store.get_thread(tid).get("claude_session") is None


def test_the_conversation_itself_is_untouched(thread):
    requires_app("budget")
    tid = thread["id"]
    chat_store.append_message(tid, "user", "keep me")
    chat_store.ensure_thread_session(tid)

    out = _rescope(tid, "budget")

    assert out["id"] == tid
    assert out["message_count"] == 1
    assert chat_store.get_thread(tid)["messages"][0]["content"] == "keep me"


def test_an_unknown_scope_changes_nothing(thread):
    import routes.chat as rc
    from fastapi import HTTPException

    tid = thread["id"]
    sid, _ = chat_store.ensure_thread_session(tid)
    with pytest.raises(HTTPException):
        asyncio.run(rc.rescope_thread(tid, rc.RescopeThread(context_key="not-a-provider")))

    t = chat_store.get_thread(tid)
    assert t["context_key"] == "general"
    # A refused rescope must not cost the chat its session either.
    assert t["claude_session"] == sid
