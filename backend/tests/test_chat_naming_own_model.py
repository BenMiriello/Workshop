"""A chat on a model served outside the CLI is named by that model, after a
turn, and never by the cloud.

Pinned: the title call goes to `own_model.call` with the thread (so an app's
gateway sees it) and thinking off; `call_cloud` is never reached, not even when
that call fails; nothing is asked while a turn runs or while the operator's
message waits for its reply, and none of that spends the naming budget. A chat
on a cloud model is named exactly as before.
"""
from __future__ import annotations

import pytest

from services import (chat_naming, chat_store, llm_router, local_model_provider,
                      own_model)
from tests.test_app_chat_and_local_harness import harnessed  # noqa: F401

LOCAL = "a-harnessed-weight"


@pytest.fixture()
def no_cloud(monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError("a local-model chat's title went to the cloud")
    monkeypatch.setattr(chat_naming.llm_router, "call_cloud", refuse)


@pytest.fixture()
def own_calls(monkeypatch):
    calls: list[dict] = []

    def call(prompt, **kw):
        calls.append(kw)
        return {"text": "Lighthouse Keeper Sketches", "exit_code": 0}
    monkeypatch.setattr(own_model, "call", call)
    return calls


def _local_thread(*, reply: str | None = "Here are three sketches.") -> str:
    tid = chat_store.create_thread("general")["id"]
    chat_store.set_thread_model(tid, LOCAL)
    chat_store.append_message(tid, "user", "Draw a lighthouse keeper")
    if reply is not None:
        chat_store.append_message(tid, "assistant", reply)
    return tid


def test_a_local_chat_is_named_by_its_own_model_with_thinking_off(
        harnessed, no_cloud, own_calls):
    tid = _local_thread()
    chat_naming.generate_title(tid)
    assert chat_store.get_thread(tid)["title"] == "Lighthouse Keeper Sketches"
    (kw,) = own_calls
    assert kw["model_id"] == LOCAL and kw["thinking"] is False
    assert kw["thread"]["id"] == tid


def test_nothing_is_asked_while_a_turn_runs_and_it_costs_no_attempt(
        harnessed, no_cloud, own_calls):
    tid = _local_thread()
    chat_store.begin_turn(tid, "run-1", model=LOCAL)
    assert chat_naming.compute_title(tid) == (None, False, False)
    assert own_calls == []
    chat_store.commit_turn(tid, "run-1", [], "and a fourth")
    chat_naming.generate_title(tid)
    assert chat_store.get_thread(tid)["title"] == "Lighthouse Keeper Sketches"


def test_a_message_awaiting_its_reply_is_not_named_yet(harnessed, no_cloud, own_calls):
    tid = _local_thread(reply=None)
    assert chat_naming.compute_title(tid) == (None, False, False)
    assert own_calls == []


def test_a_failed_own_model_call_never_falls_back_to_the_cloud(
        harnessed, no_cloud, monkeypatch):
    def down(*a, **kw):
        raise RuntimeError("model server down")
    monkeypatch.setattr(own_model, "call", down)
    tid = _local_thread()
    assert chat_naming.compute_title(tid) == (None, False, False)
    assert chat_store.get_thread(tid).get("_name_attempts", 0) == 0


def test_a_cloud_chat_is_named_by_the_cloud_as_before(harnessed, monkeypatch):
    seen = {}

    def cloud(prompt, **kw):
        seen.update(kw)
        return {"text": "Lighthouse Keeper Sketches"}
    monkeypatch.setattr(chat_naming.llm_router, "call_cloud", cloud)
    monkeypatch.setattr(own_model, "call", lambda *a, **kw: pytest.fail("own model"))
    tid = chat_store.create_thread("general")["id"]
    chat_store.set_thread_model(tid, llm_router.TIER_SONNET)
    chat_store.append_message(tid, "user", "Draw a lighthouse keeper")
    chat_naming.generate_title(tid)
    assert seen["model"] == llm_router.TIER_HAIKU
    assert chat_store.get_thread(tid)["title"] == "Lighthouse Keeper Sketches"


def test_thinking_off_reaches_the_model_server_in_both_spellings(monkeypatch):
    sent = {}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json, headers):
            sent.update(json)
            return type("R", (), {"status_code": 200, "json": lambda self: {
                "content": [{"type": "text", "text": "A Title"}]}})()

    monkeypatch.setattr(local_model_provider, "_client", lambda timeout: _Client())
    provider = local_model_provider.LocalHttpProvider("http://127.0.0.1:1")
    provider.run_turn("p", model="w", thinking=False)
    assert sent["thinking"] == {"type": "disabled"}
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}
    sent.clear()
    provider.run_turn("p", model="w")
    assert "thinking" not in sent and "chat_template_kwargs" not in sent
