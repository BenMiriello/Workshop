"""An ended schedule can be sent again or started over, and each schedule
shows what it has sent. Asked 10-25: an ended one could only be edited in
place or deleted, and its view said only "Sent 3".
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import chat_schedules as routes  # noqa: E402
from services import (chat_drain, chat_log, chat_queue, chat_schedule_sends,  # noqa: E402
                      chat_schedules, chat_store, ownership)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    monkeypatch.setattr(chat_drain, "drain_soon", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(routes.router, prefix="/chat")
    try:
        yield TestClient(app)
    finally:
        ownership.clear_current_owner()


@pytest.fixture
def thread(client):
    return chat_store.create_thread("general")["id"]


def _deliver(thread_id):
    """What the drain does with a schedule's waiting message: into the chat."""
    for e in chat_queue.read(thread_id):
        chat_log.append_message_sync(thread_id, "user", e["text"], client_id=e["client_id"],
                                     meta={"sender": e["sender"]}, ts=e["ts"])
    chat_queue.drop(thread_id, [e["client_id"] for e in chat_queue.read(thread_id)])


def _client_ids(thread_id):
    return [e["client_id"] for e in chat_queue.read(thread_id)]


def _ended_after_two(client, thread):
    sid = client.post(f"/chat/threads/{thread}/schedules",
                      json={"text": "Go on.", "max_sends": 2}).json()["id"]
    for _ in range(2):
        chat_schedule_sends.on_turn_end(thread)
        _deliver(thread)
    assert chat_schedules.get(thread, sid)["status"] == "done"
    return sid


def test_send_again_sends_an_ended_ones_message_and_it_stays_ended(client, thread):
    sid = _ended_after_two(client, thread)
    got = client.post(f"/chat/threads/{thread}/schedules/{sid}/again")
    assert got.status_code == 200
    assert got.json()["status"] == "done" and got.json()["sent"] == 2
    assert _client_ids(thread) == [f"{sid}-3"]
    again = client.post(f"/chat/threads/{thread}/schedules/{sid}/again")
    assert again.status_code == 200 and _client_ids(thread) == [f"{sid}-3"]


def test_send_again_is_only_for_an_ended_one(client, thread):
    sid = client.post(f"/chat/threads/{thread}/schedules", json={"text": "Go on."}).json()["id"]
    got = client.post(f"/chat/threads/{thread}/schedules/{sid}/again")
    assert got.status_code == 409 and "Send now" in got.json()["detail"]


def test_starting_over_runs_the_rule_again_without_reusing_a_message_id(client, thread):
    sid = _ended_after_two(client, thread)
    got = client.post(f"/chat/threads/{thread}/schedules/{sid}/rearm",
                      json={"text": "Go on, again.", "max_sends": 1})
    assert got.status_code == 200
    s = got.json()
    assert s["status"] == "active" and s["sent"] == 0 and s["why"] is None
    chat_schedule_sends.on_turn_end(thread)
    assert _client_ids(thread) == [f"{sid}-3"]
    assert chat_queue.read(thread)[0]["text"] == "Go on, again."
    _deliver(thread)
    chat_schedule_sends.on_turn_end(thread)
    after = chat_schedules.get(thread, sid)
    assert after["status"] == "done" and after["sent"] == 1 and _client_ids(thread) == []


def test_starting_over_refuses_a_rule_that_would_end_at_once(client, thread):
    sid = _ended_after_two(client, thread)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    got = client.post(f"/chat/threads/{thread}/schedules/{sid}/rearm", json={"until_at": past})
    assert got.status_code == 422 and "end" in got.json()["detail"]
    assert chat_schedules.get(thread, sid)["status"] == "done"


def test_history_lists_what_it_sent_newest_first_and_the_one_waiting(client, thread):
    sid = _ended_after_two(client, thread)
    client.post(f"/chat/threads/{thread}/schedules/{sid}/again")
    got = client.get(f"/chat/threads/{thread}/schedules/{sid}/sends")
    assert got.status_code == 200
    sends = got.json()["sends"]
    assert [s["state"] for s in sends] == ["waiting", "sent", "sent"]
    assert sends[1]["at"] >= sends[2]["at"]
    assert client.get(f"/chat/threads/{thread}/schedules/nope/sends").status_code == 404


def test_history_counts_a_delivered_message_once(client, thread):
    sid = client.post(f"/chat/threads/{thread}/schedules", json={"text": "Go on."}).json()["id"]
    chat_schedule_sends.on_turn_end(thread)
    e = chat_queue.read(thread)[0]
    chat_log.append_message_sync(thread, "user", e["text"], client_id=e["client_id"],
                                 meta={"sender": e["sender"]})
    sends = client.get(f"/chat/threads/{thread}/schedules/{sid}/sends").json()["sends"]
    assert [s["state"] for s in sends] == ["sent"]
