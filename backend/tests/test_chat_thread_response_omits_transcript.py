"""A route that answers with a thread answers with its SETTINGS, not its history.

Four routes return a `chat_store` thread document: GET, PATCH (rename), archive
and restore. That document carries `messages`. Exactly one of the four stripped
it, and carried a comment telling the next reader not to put it back; the other
three sat a few functions below returning the same document raw, so closing a
chat shipped its entire transcript to a client that discards it. Measured
2026-08-14: the worst thread's `messages` was 4.77 MB of a 4.79 MB response.

The client cannot catch this. `lib/chat.ts` types all four as `ThreadSummary`,
which has no `messages` field, so the payload is parsed and thrown away in
silence. Nothing on screen changes, which is why it survived. These tests go
through the router because the defect was in the route layer: the store is
right to return messages, and backend callers of get_thread() need them.

The transcript has its own endpoint (GET /events). That is the whole rule.
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import chat as chat_routes  # noqa: E402
from services import chat_store, ownership  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/chat")
    try:
        yield TestClient(app)
    finally:
        ownership.clear_current_owner()


@pytest.fixture
def thread_with_history(client):
    """A thread that actually has a transcript, so an omission is meaningful.

    Asserting `messages` is absent from an empty chat proves nothing: the key
    could be missing because there was nothing to put in it.
    """
    tid = chat_store.create_thread("general")["id"]
    chat_store.append_message(tid, "user", "the operator said this")
    chat_store.append_message(tid, "assistant", "and this came back")
    doc = chat_store.get_thread(tid)
    # The premise. If the store ever stops carrying messages these tests become
    # vacuous, so fail loudly here rather than passing for the wrong reason.
    assert len(doc.get("messages") or []) >= 2, "store no longer carries the transcript"
    return tid


class TestTheTranscriptIsNotInASettingsResponse:
    def test_get_omits_it(self, client, thread_with_history):
        r = client.get(f"/chat/threads/{thread_with_history}")
        assert r.status_code == 200
        assert "messages" not in r.json()

    def test_rename_omits_it(self, client, thread_with_history):
        r = client.patch(f"/chat/threads/{thread_with_history}", json={"title": "renamed"})
        assert r.status_code == 200
        assert "messages" not in r.json()

    def test_archive_omits_it(self, client, thread_with_history):
        r = client.post(f"/chat/threads/{thread_with_history}/archive")
        assert r.status_code == 200
        assert "messages" not in r.json()

    def test_restore_omits_it(self, client, thread_with_history):
        client.post(f"/chat/threads/{thread_with_history}/archive")
        r = client.post(f"/chat/threads/{thread_with_history}/restore")
        assert r.status_code == 200
        assert "messages" not in r.json()


class TestOmittingTheTranscriptDoesNotOmitTheAnswer:
    """Stripping one key must not strip the thing the caller actually asked for.

    A response that dropped everything would pass the tests above.
    """

    def test_rename_still_reports_the_new_title(self, client, thread_with_history):
        r = client.patch(f"/chat/threads/{thread_with_history}", json={"title": "renamed"})
        assert r.json()["title"] == "renamed"

    def test_archive_still_reports_the_thread_it_archived(self, client, thread_with_history):
        r = client.post(f"/chat/threads/{thread_with_history}/archive")
        assert r.json()["id"] == thread_with_history

    def test_the_settings_the_client_types_are_all_present(self, client, thread_with_history):
        # lib/chat.ts ThreadSummary requires these; a response missing one is a
        # different bug wearing this fix's clothes.
        body = client.get(f"/chat/threads/{thread_with_history}").json()
        for key in ("id", "context_key", "title", "updated_at"):
            assert key in body, f"{key} missing from the settings payload"
