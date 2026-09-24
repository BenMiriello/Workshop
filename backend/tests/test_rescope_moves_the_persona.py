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




@pytest.fixture()
def app_scope() -> str:
    """SOME installed app's context key, asked for rather than named.

    These tests need a scope the rescope route will accept, which means an app
    that is actually installed. Which one is irrelevant to what is asserted,
    and naming a real app here made a platform test carry one installation's
    app id -- the exact thing `check_no_app_names.py` counts. A fresh clone
    installs nothing, so with no app installed there is no second scope to
    move to and the test skips.
    """
    from services import app_registry

    for cls in app_registry.installed_chat_providers():
        key = getattr(cls, "key", None)
        if key:
            return key
    pytest.skip("no app with a chat persona is installed on this instance")


def _rescope(thread_id: str, key: str):
    import routes.chat as rc
    return asyncio.run(rc.rescope_thread(thread_id, rc.RescopeThread(context_key=key)))


def test_the_live_session_is_dropped_with_the_scope(thread, app_scope):
    tid = thread["id"]
    sid, _ = chat_store.ensure_thread_session(tid)
    assert chat_store.get_thread(tid)["claude_session"] == sid

    out = _rescope(tid, app_scope)

    assert out["context_key"] == app_scope
    # The persona travels with the tools, or neither has really moved.
    assert chat_store.get_thread(tid).get("claude_session") is None


def test_the_conversation_itself_is_untouched(thread, app_scope):
    tid = thread["id"]
    chat_store.append_message(tid, "user", "keep me")
    chat_store.ensure_thread_session(tid)

    out = _rescope(tid, app_scope)

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


def test_a_missing_folder_behind_a_bare_key_is_a_400(thread, tmp_path, monkeypatch):
    """A scope key need not contain ':' (the platform's own `development`
    scope is bare). When its folder is missing the route must say so, not
    crash building the message."""
    from fastapi import HTTPException
    from services import chat_context

    missing = object.__new__(chat_context.ProjectContextProvider)
    missing.key = "bare-scope"
    missing.dir = tmp_path / "not-there"
    monkeypatch.setattr(chat_context, "get_provider", lambda key: missing)

    with pytest.raises(HTTPException) as err:
        _rescope(thread["id"], "bare-scope")
    assert err.value.status_code == 400
