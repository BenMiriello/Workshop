"""A message can carry a rule for when it goes out, and the rule only ever
makes it wait: it never joins a running turn, never starts one early, and a
message whose rule has not come true does not hold up the ones behind it.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import chat as chat_routes  # noqa: E402
from services import (build_streaming, chat_drain, chat_queue, chat_store,  # noqa: E402
                      ownership, send_rules)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    started: list = []

    async def starter(thread_id, batch):
        started.append([e["client_id"] for e in batch])
        return True

    monkeypatch.setattr(chat_drain, "_starter", starter)
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/chat")
    try:
        c = TestClient(app)
        c.started = started
        yield c
    finally:
        ownership.clear_current_owner()
        build_streaming.TURNS.clear()


@pytest.fixture
def thread(client):
    return chat_store.create_thread("general")["id"]


def _later(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _running_turn(thread_id: str) -> str:
    chat_store.begin_turn(thread_id, "run-live")
    turn = build_streaming.Turn(id="run-live", thread_id=thread_id, agent_slug="a")
    turn.spawned = True
    build_streaming.TURNS["run-live"] = turn
    return "run-live"


def test_a_scheduled_message_waits_on_an_idle_chat(client, thread):
    r = client.post(f"/chat/threads/{thread}/turns", json={
        "content": "tomorrow's question", "client_id": "c1",
        "when": {"kind": "at", "at": _later(60)}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] is True
    assert body["queue"]["queued"][0]["when"]["kind"] == "at"
    assert client.started == []


def test_a_rule_never_joins_the_running_turn(client, thread, monkeypatch):
    _running_turn(thread)
    injected = []
    monkeypatch.setattr(build_streaming, "inject",
                        lambda *a, **k: injected.append(a) or True)
    r = client.post(f"/chat/threads/{thread}/turns", json={
        "content": "after this one", "client_id": "c1",
        "when": {"kind": "after_turn"}})
    assert r.json()["queued"] is True
    assert injected == []
    assert chat_queue.read(thread)[0]["when"] == {"kind": "after_turn"}


def test_a_waiting_rule_does_not_hold_up_later_messages(client, thread):
    chat_queue.enqueue(thread, text="held", client_id="c1", when={"kind": "hold"})
    chat_queue.enqueue(thread, text="plain", client_id="c2")
    assert [e["client_id"] for e in chat_queue.next_batch(thread)] == ["c2"]


def test_send_now_clears_the_rule_and_sends(client, thread):
    client.post(f"/chat/threads/{thread}/turns", json={
        "content": "hold this", "client_id": "c1", "when": {"kind": "hold"}})
    assert client.started == []
    r = client.put(f"/chat/threads/{thread}/queued/c1/when", json={"when": None})
    assert r.status_code == 200, r.text
    assert client.started == [["c1"]]


def test_a_time_comes_due():
    entry = {"when": {"kind": "at", "at": "2026-10-24T20:00:00+00:00"}}
    due = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc).timestamp()
    assert not send_rules.is_ready(entry, now=due - 1)
    assert send_rules.is_ready(entry, now=due)
    assert send_rules.needs_poll(entry)
    assert not send_rules.needs_poll({"when": {"kind": "hold"}})


def test_after_another_chat(client, thread):
    other = chat_store.create_thread("general")["id"]
    _running_turn(other)
    entry = {"when": send_rules.normalize(
        {"kind": "after_thread", "thread_id": other}, thread_id=thread)}
    assert not send_rules.is_ready(entry)
    chat_store.release_turn(other, "run-live")
    assert send_rules.is_ready(entry)


def test_bad_rules_are_refused_in_words(client, thread):
    for when in ({"kind": "someday"}, {"kind": "at", "at": "tomorrow"},
                 {"kind": "after_thread", "thread_id": thread}):
        r = client.post(f"/chat/threads/{thread}/turns", json={
            "content": "x", "client_id": "c9", "when": when})
        assert r.status_code == 400, when


def test_the_resident_loop_sends_it_when_due(client, thread):
    chat_queue.enqueue(thread, text="soon", client_id="c1",
                       when={"kind": "at", "at": _later(-1)})
    assert chat_queue.needs_poll(thread)
    assert asyncio.run(chat_drain.drain(thread))
    assert client.started == [["c1"]]
