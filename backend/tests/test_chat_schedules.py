"""A schedule sends a message into a chat again and again, as a record the
operator sees and manages there. Its messages are from the schedule, never
typed by him; his Pause and Stop are final for an app's schedule too.

Found 10-25: an automation run posted "goes on by itself" into its chat after
every turn as if he had typed it, with its only switch on another page.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import chat as chat_routes  # noqa: E402
from routes import chat_schedules as routes  # noqa: E402
from services import (build_streaming, chat_drain, chat_queue, chat_schedule_sends,  # noqa: E402
                      chat_schedules, chat_store, ownership, wakeups)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)

    async def starter(thread_id, batch):
        return True

    monkeypatch.setattr(chat_drain, "_starter", starter)
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/chat")
    app.include_router(routes.router, prefix="/chat")
    try:
        yield TestClient(app)
    finally:
        ownership.clear_current_owner()
        build_streaming.TURNS.clear()


@pytest.fixture
def thread(client):
    return chat_store.create_thread("general")["id"]


def _queued(thread_id):
    return [e for e in chat_queue.read(thread_id)
            if str(e.get("sender") or "").startswith("schedule:")]


def test_after_each_reply_sends_from_the_schedule_once_per_turn_up_to_its_max(client, thread):
    got = client.post(f"/chat/threads/{thread}/schedules",
                      json={"text": "Go on.", "max_sends": 2}).json()
    assert got["set_by"] == {"kind": "you", "name": None, "href": None}
    chat_schedule_sends.on_turn_end(thread)
    chat_schedule_sends.on_turn_end(thread)
    assert [e["sender"] for e in _queued(thread)] == [f"schedule:{got['id']}"]
    chat_queue.drop(thread, [e["client_id"] for e in _queued(thread)])
    chat_schedule_sends.on_turn_end(thread)
    after = chat_schedules.get(thread, got["id"])
    assert after["sent"] == 2 and after["status"] == "done" and after["why"] == "sent 2 of 2"
    chat_queue.drop(thread, [e["client_id"] for e in _queued(thread)])
    chat_schedule_sends.on_turn_end(thread)
    assert _queued(thread) == []


def test_stop_pauses_it_and_the_model_reads_who_sent_it(client, thread):
    got = client.post(f"/chat/threads/{thread}/schedules",
                      json={"text": "Go on.", "label": "Keep going"}).json()
    heading = chat_routes._message_heading([{"sender": f"schedule:{got['id']}"}], thread)
    assert "Keep going the operator set" in heading and "SENT AUTOMATICALLY" in heading
    chat_schedule_sends.on_turn_end(thread, stopped=True)
    sch = chat_schedules.get(thread, got["id"])
    assert sch["status"] == "paused" and _queued(thread) == []


def test_an_apps_schedule_sends_only_when_the_app_asks_and_never_when_paused(
        client, thread, monkeypatch):
    monkeypatch.setattr(routes, "_app", lambda request: "Another app")
    got = client.post(f"/chat/threads/{thread}/schedules", json={
        "text": "Run #9 goes on.", "label": "Automation run #9",
        "href": "/apps/another/runs/9"}).json()
    assert got["driver"] == "app" and got["set_by"]["href"] == "/apps/another/runs/9"
    chat_schedule_sends.on_turn_end(thread)
    assert _queued(thread) == []
    sent = client.post(f"/chat/threads/{thread}/schedules/{got['id']}/send", json={})
    assert sent.status_code == 200 and sent.json()["sent"] == 1
    monkeypatch.setattr(routes, "_app", lambda request: None)
    client.patch(f"/chat/threads/{thread}/schedules/{got['id']}", json={"status": "paused"})
    monkeypatch.setattr(routes, "_app", lambda request: "Another app")
    refused = client.post(f"/chat/threads/{thread}/schedules/{got['id']}/send", json={})
    assert refused.status_code == 409 and refused.json()["detail"] == "paused in the chat"


def test_an_app_changes_only_what_it_set(client, thread, monkeypatch):
    mine = client.post(f"/chat/threads/{thread}/schedules", json={"text": "Mine."}).json()
    monkeypatch.setattr(routes, "_app", lambda request: "Another app")
    r = client.patch(f"/chat/threads/{thread}/schedules/{mine['id']}", json={"status": "paused"})
    assert r.status_code == 403


def test_every_and_once_send_when_due_and_the_count_covers_everything_waiting(client, thread):
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    later = client.post(f"/chat/threads/{thread}/schedules",
                        json={"text": "Later.", "repeat": "every", "every_min": 30}).json()
    assert datetime.fromisoformat(later["next_at"]) > datetime.now(timezone.utc) + timedelta(minutes=29)
    every = client.post(f"/chat/threads/{thread}/schedules", json={
        "text": "Tick.", "repeat": "every", "every_min": 30, "start_at": past}).json()
    once = client.post(f"/chat/threads/{thread}/schedules",
                       json={"text": "Once.", "repeat": "once", "start_at": past}).json()
    assert chat_schedule_sends.fire_due([thread]) == [thread]
    assert {e["sender"] for e in _queued(thread)} == {f"schedule:{every['id']}",
                                                       f"schedule:{once['id']}"}
    assert chat_schedules.get(thread, once["id"])["status"] == "done"
    nxt = datetime.fromisoformat(chat_schedules.get(thread, every["id"])["next_at"])
    assert nxt > datetime.now(timezone.utc) + timedelta(minutes=29)
    wakeups.arm(thread, reason="check", due_at=datetime.now(timezone.utc) + timedelta(hours=1))
    got = client.get(f"/chat/threads/{thread}/schedules").json()
    assert got["count"] == 3 and len(got["wakeups"]) == 1 and got["waiting"] == 0


def test_a_rule_that_makes_no_sense_is_refused_in_words(client, thread):
    r = client.post(f"/chat/threads/{thread}/schedules", json={"text": "x", "repeat": "every"})
    assert r.status_code == 422 and "minutes" in r.json()["detail"]
    r = client.post(f"/chat/threads/{thread}/schedules", json={"text": " "})
    assert r.status_code == 422
