"""The queue over HTTP: the 2026-08-12 incident, replayed through the routes.

The service tests (`test_chat_queue`, `test_chat_queue_drain`) pin the storage
and the drain. These pin the WIRE, because a queue that is only reachable from
inside the process is exactly as useful to the operator's other laptop as the
localStorage one it replaced.

What has to be true from outside:

  - sending during a turn that reads nothing answers "queued", not "deferred,
    hold on to it yourself";
  - the queued message comes back on the sync endpoint, so a second device
    renders the same thing without having sent anything;
  - the composer it was typed in is cleared by the server;
  - it can be taken back out, and a held queue can be released.
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import chat as chat_routes  # noqa: E402
from services import build_streaming, chat_store, draft_store, ownership  # noqa: E402


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
        build_streaming.TURNS.clear()


@pytest.fixture
def thread(client):
    return chat_store.create_thread("general")["id"]


def _command_turn(thread_id: str) -> str:
    """Claim the thread with a run that reads no input, as `/compact` does."""
    from services import chat_log
    chat_store.begin_run(thread_id, "run-cmd")
    turn = build_streaming.Turn(id="run-cmd", thread_id=thread_id, agent_slug="a")
    turn.accepts_input = False
    turn.spawned = True
    build_streaming.TURNS["run-cmd"] = turn
    assert chat_log.read_meta(thread_id).get("active_run_id") == "run-cmd"
    return "run-cmd"


class TestSendingDuringACommandTurn:
    def test_the_server_takes_it_and_says_so(self, client, thread):
        _command_turn(thread)
        r = client.post(f"/chat/threads/{thread}/turns", json={
            "content": "the long one I typed during the compact",
            "client_id": "c1",
            "sent_at": "2026-08-12T17:44:20Z",
        })
        assert r.status_code == 200
        body = r.json()
        # NOT `deferred`. That answer meant "we did not take this, keep holding
        # it" — and the browser holding it is what lost two messages.
        assert body["queued"] is True
        assert body["queue"]["queued"][0]["text"] == "the long one I typed during the compact"

    def test_a_second_device_sees_it_without_having_sent_anything(self, client, thread):
        _command_turn(thread)
        client.post(f"/chat/threads/{thread}/turns", json={
            "content": "typed on the laptop", "client_id": "c1"})
        # A different client, which has never heard of this message, syncing the
        # way every client syncs.
        page = client.get(f"/chat/threads/{thread}/events?after=0").json()
        assert [q["text"] for q in page["queued"]] == ["typed on the laptop"]
        assert page["queue_held"] is False
        # ...and it is NOT in the transcript, because it has not been said yet.
        assert not [e for e in page["events"] if e.get("kind") == "message"]

    def test_opening_the_chat_shows_it_on_the_first_paint(self, client, thread):
        _command_turn(thread)
        client.post(f"/chat/threads/{thread}/turns", json={
            "content": "waiting", "client_id": "c1"})
        assert [q["text"] for q in client.get(f"/chat/threads/{thread}").json()["queued"]] \
            == ["waiting"]

    def test_it_keeps_the_moment_the_operator_hit_enter(self, client, thread):
        _command_turn(thread)
        client.post(f"/chat/threads/{thread}/turns", json={
            "content": "typed at 17:44", "client_id": "c1",
            "sent_at": "2026-08-12T17:44:20Z"})
        assert client.get(f"/chat/threads/{thread}/events").json()["queued"][0]["ts"] \
            == "2026-08-12T17:44:20Z"

    def test_a_resend_of_the_same_message_queues_it_once(self, client, thread):
        _command_turn(thread)
        for _ in range(3):
            client.post(f"/chat/threads/{thread}/turns", json={
                "content": "hello", "client_id": "c1"})
        assert len(client.get(f"/chat/threads/{thread}/events").json()["queued"]) == 1


class TestTheComposerIsClearedByTheServer:
    def test_accepting_a_message_clears_the_draft_it_came_from(self, client, thread):
        # Typed into the blank-chat slot, which is the case the client cannot
        # clear correctly on its own: sending the first message of a blank chat
        # changes the composer's key, so a local clear lands on the new one and
        # `::new` keeps the text forever.
        draft_store.put_draft("general::new", "the message", 1, "tab-1")
        _command_turn(thread)
        client.post(f"/chat/threads/{thread}/turns", json={
            "content": "the message", "client_id": "c1", "draft_key": "general::new"})
        assert draft_store.get_draft("general::new")["text"] == ""

    def test_the_clear_leaves_a_tombstone_so_a_late_write_cannot_undo_it(self, client, thread):
        draft_store.put_draft("general::new", "the message", 1000, "tab-1")
        _command_turn(thread)
        client.post(f"/chat/threads/{thread}/turns", json={
            "content": "the message", "client_id": "c1", "draft_key": "general::new"})
        # A PUT that was already in flight when the send happened, carrying the
        # older timestamp of the keystroke that produced it.
        draft_store.put_draft("general::new", "the message", 999, "tab-1")
        assert draft_store.get_draft("general::new")["text"] == ""


class TestTakingItBack:
    def test_a_queued_message_can_be_unsent(self, client, thread):
        _command_turn(thread)
        client.post(f"/chat/threads/{thread}/turns", json={
            "content": "actually no", "client_id": "c1"})
        r = client.post(f"/chat/threads/{thread}/queued/c1/cancel")
        assert r.status_code == 200
        assert r.json()["queued"] == []

    def test_releasing_a_held_queue_answers_with_its_state(self, client, thread):
        from services import chat_queue
        _command_turn(thread)
        client.post(f"/chat/threads/{thread}/turns", json={
            "content": "typed ahead", "client_id": "c1"})
        chat_queue.set_held(thread, True)
        assert client.get(f"/chat/threads/{thread}/events").json()["queue_held"] is True
        r = client.post(f"/chat/threads/{thread}/queued/release")
        assert r.status_code == 200
        # Still queued (the command turn still owns the thread), but no longer
        # holding: it goes out the moment that run ends.
        assert r.json()["queue_held"] is False
        assert [q["text"] for q in r.json()["queued"]] == ["typed ahead"]

    def test_deleting_the_chat_takes_its_queue_with_it(self, client, thread):
        from services import chat_queue
        _command_turn(thread)
        client.post(f"/chat/threads/{thread}/turns", json={
            "content": "orphan", "client_id": "c1"})
        client.delete(f"/chat/threads/{thread}")
        assert chat_queue.read(thread) == []
