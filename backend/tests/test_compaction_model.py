"""A compaction goes to the compaction model when one is set and its window
fits, and every other request is left as it was.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import local_llm
from services import compaction_brief
from services import local_model_provider as lmp
from services import local_request_shape as shape

from tests.test_local_request_shape import ASK, RESULT, _Upstream

CHAT = "chat-model"
SUMMARY = "summary-model"
OTHER = "other-summary-model"
WINDOWS = {CHAT: 98304, SUMMARY: 131072, OTHER: 131072}


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_LOCAL_MODEL_CONFIG", str(tmp_path / "local.json"))


def body(text: str = "", max_tokens: int = 8000) -> bytes:
    return json.dumps({"model": CHAT, "max_tokens": max_tokens, "system": "p",
                       "messages": [{"role": "user", "content": [
                           RESULT, {"type": "text", "text": text}, ASK]}]}).encode()


def tokens_of(raw: bytes) -> int:
    """The estimate the shaper makes, which is after its split and the length limit."""
    payload = json.loads(raw)
    payload["messages"] = shape.split_results(payload["messages"])
    compaction_brief.briefed(payload["messages"])
    return shape.request_tokens(payload)


def sent(raw: bytes, klass: str | None = "compaction", to: str | None = SUMMARY,
         windows: dict = WINDOWS) -> dict:
    return json.loads(shape.shape(raw, klass, lambda m: windows.get(m, 0), to))


def test_a_compaction_goes_to_the_compaction_model():
    got = sent(body())
    assert got["model"] == SUMMARY
    assert got["max_tokens"] == shape.COMPACTION_OUT


def test_the_room_is_sized_by_the_final_models_window():
    """A prompt the chat's window would leave little room after: the summary
    model has more, and the room is measured against its window."""
    raw = body("x" * 3 * 85000)
    tokens = tokens_of(raw)
    chat_free = 98304 - tokens - shape.MARGIN
    assert 8000 < chat_free < shape.COMPACTION_OUT - 2000
    got = sent(raw, windows={CHAT: 98304,
                             SUMMARY: tokens + shape.MARGIN + chat_free + 1000})
    assert got["model"] == SUMMARY
    assert got["max_tokens"] == chat_free + 1000


def test_a_window_too_small_keeps_the_chats_model():
    raw = body("x" * 3 * 60000)
    tokens = tokens_of(raw)
    small = {CHAT: 98304, SUMMARY: tokens + 4000}
    got = sent(raw, windows=small)
    assert got["model"] == CHAT
    assert got["max_tokens"] == shape.COMPACTION_OUT


def test_less_room_than_the_chats_model_keeps_the_chats_model():
    """A window that holds the prompt but leaves the summary less room than
    the chat's own model would is not taken: a switch never shortens it."""
    raw = body("x" * 3 * 60000)
    tokens = tokens_of(raw)
    got = sent(raw, windows={CHAT: 98304,
                             SUMMARY: tokens + shape.MARGIN + 10000})
    assert got["model"] == CHAT


def test_an_unknown_window_keeps_the_chats_model():
    assert sent(body(), windows={CHAT: 98304})["model"] == CHAT


def test_no_compaction_model_keeps_the_chats_model():
    assert sent(body(), to=None)["model"] == CHAT
    assert sent(body(), to=CHAT)["model"] == CHAT


def test_a_request_that_is_not_a_compaction_is_untouched():
    for klass in (None, "main", "subagent", "auxiliary"):
        got = sent(body(), klass=klass)
        assert got["model"] == CHAT
        assert got["max_tokens"] == 8000


def test_the_hop_sends_only_compactions_to_the_compaction_model(monkeypatch):
    up = _Upstream()
    monkeypatch.setattr(lmp, "base_url", lambda: up.origin)
    monkeypatch.setattr(lmp, "compaction_model", lambda: SUMMARY)
    monkeypatch.setattr(local_llm, "_window", lambda m: WINDOWS.get(m, 0))
    app = FastAPI()
    app.include_router(local_llm.router)
    client = TestClient(app)
    try:
        client.post("/local-llm/v1/messages", content=body(),
                    headers={"x-claude-code-request-class": "compaction"})
        client.post("/local-llm/v1/messages", content=body())
        client.post("/local-llm/v1/messages", content=body(),
                    headers={"x-claude-code-request-class": "auxiliary"})
    finally:
        up.httpd.shutdown()
    compaction, main, side = up.seen
    assert compaction["model"] == SUMMARY
    assert main["model"] == CHAT and main["max_tokens"] == 8000
    assert side["model"] == CHAT


def test_the_hop_sends_a_compaction_to_its_apps_model(owned, monkeypatch):
    up = _Upstream()
    monkeypatch.setattr(lmp, "base_url", lambda: up.origin)
    monkeypatch.setattr(lmp, "compaction_model", lambda: SUMMARY)
    monkeypatch.setattr(lmp, "scope_compaction_models",
                        lambda: {"app:pics": OTHER})
    monkeypatch.setattr(local_llm, "_window", lambda m: WINDOWS.get(m, 0))
    app = FastAPI()
    app.include_router(local_llm.router)
    client = TestClient(app)
    compaction = {"x-claude-code-request-class": "compaction"}
    try:
        client.post("/local-llm/v1/messages", content=body(),
                    headers={**compaction, "x-workshop-app": "pics"})
        client.post("/local-llm/v1/messages", content=body(),
                    headers={**compaction, "x-workshop-app": "other"})
        client.post("/local-llm/v1/messages", content=body(), headers=compaction)
        client.post("/local-llm/v1/messages", content=body(),
                    headers={"x-workshop-app": "pics"})
    finally:
        up.httpd.shutdown()
    mine, other_app, unscoped, main = up.seen
    assert mine["model"] == OTHER
    assert other_app["model"] == SUMMARY
    assert unscoped["model"] == SUMMARY
    assert main["model"] == CHAT


def test_an_app_without_a_choice_and_no_global_keeps_the_chats_model(
        owned, monkeypatch):
    up = _Upstream()
    monkeypatch.setattr(lmp, "base_url", lambda: up.origin)
    monkeypatch.setattr(local_llm, "_window", lambda m: WINDOWS.get(m, 0))
    app = FastAPI()
    app.include_router(local_llm.router)
    try:
        TestClient(app).post(
            "/local-llm/v1/messages", content=body(),
            headers={"x-claude-code-request-class": "compaction",
                     "x-workshop-app": "pics"})
    finally:
        up.httpd.shutdown()
    assert up.seen[0]["model"] == CHAT
