"""Model and effort commit on change, not only on a send.

They were written by the send alone, so a pick made and then left without
sending was never stored and the picker reset on the next load.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import chat_store, model_caps, ownership


@pytest.fixture
def client(tmp_path, monkeypatch):
    from routes import chat as chat_routes
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/chat")
    try:
        yield TestClient(app)
    finally:
        ownership.clear_current_owner()


def _other_model(current):
    return next(m for m in model_caps.EFFORT_BY_MODEL if m != current and model_caps.EFFORT_BY_MODEL[m])


def test_a_model_pick_is_stored_without_a_send(client):
    t = chat_store.create_thread(context_key="general")
    model = _other_model(t.get("model"))
    r = client.post(f"/chat/threads/{t['id']}/model", json={"model": model})
    assert r.status_code == 200
    assert chat_store.get_thread(t["id"])["model"] == model


def test_an_effort_pick_is_stored_and_a_blank_one_clears_it(client):
    t = chat_store.create_thread(context_key="general")
    model = _other_model(t.get("model"))
    level = model_caps.EFFORT_BY_MODEL[model][0]
    client.post(f"/chat/threads/{t['id']}/model", json={"model": model, "effort": level})
    assert chat_store.get_thread(t["id"])["effort"] == level
    r = client.post(f"/chat/threads/{t['id']}/model", json={"effort": ""})
    assert r.status_code == 200
    assert chat_store.get_thread(t["id"]).get("effort") in (None, "")


def test_an_omitted_effort_is_left_alone(client):
    t = chat_store.create_thread(context_key="general")
    model = _other_model(t.get("model"))
    level = model_caps.EFFORT_BY_MODEL[model][0]
    client.post(f"/chat/threads/{t['id']}/model", json={"model": model, "effort": level})
    client.post(f"/chat/threads/{t['id']}/model", json={"model": model})
    assert chat_store.get_thread(t["id"])["effort"] == level


def test_a_missing_thread_is_404(client):
    assert client.post("/chat/threads/nope/model", json={"model": "x"}).status_code == 404
