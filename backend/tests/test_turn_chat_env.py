"""A turn's environment names the chat it runs for, so a program the turn
starts can tell an app whose work it is (an app's queue shows that chat)."""
from services import build_streaming, chat_store


def test_a_turn_names_its_chat_to_what_it_runs(monkeypatch):
    monkeypatch.setattr(chat_store, "get_thread", lambda tid: {
        "id": tid, "title": "Story pages", "scope": "development"})
    env = build_streaming._turn_env(
        {"model": "claude-opus-5-5", "thread_id": "abc123"}, {})
    assert env["WORKSHOP_THREAD"] == "abc123"
    assert env["WORKSHOP_THREAD_TITLE"] == "Story pages"
    assert env["WORKSHOP_CHAT_SCOPE"] == "development"


def test_a_turn_with_no_chat_names_none():
    env = build_streaming._turn_env({"model": "claude-opus-5-5"}, {})
    assert "WORKSHOP_THREAD" not in env
