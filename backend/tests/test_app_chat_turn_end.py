"""An app gateway is told when each chat turn ends, and how.

Pinned: the outcome words, the POST itself (declared path under the gateway's
path, the gateway's current port, the thread and turn headers, the JSON body),
silence for an app that declares no `turn_end` and for any other scope, a
failing post that costs nothing, and that every ending reaches it: each
outcome of `_finalize_turn`, the restart-lost pass, and a dead turn taken over
by a new claim.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from services import (app_chat_turns, app_runner, build_streaming, chat_drain,
                      chat_store, ownership)
from tests.test_app_chat_and_local_harness import SLUG, app_tree  # noqa: F401

THREAD = {"id": "t-1", "scope": f"app:{SLUG}", "title": "A b"}


@pytest.fixture()
def posts(monkeypatch):
    sent: list[dict] = []

    def post(url, *, headers, json, timeout):
        sent.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return types.SimpleNamespace(status_code=204)

    monkeypatch.setattr(app_chat_turns.httpx, "post", post)
    return sent


def _announce(ended="finished", thread=THREAD):
    worker = app_chat_turns.announce(thread, "turn-9", ended)
    if worker is not None:
        worker.join(5)
    return worker


@pytest.mark.parametrize("kw,expected", [
    ({"rc": 0}, "finished"),
    ({"rc": -15, "stopped": True}, "stopped"),
    ({"rc": -1, "lost": True}, "lost"),
    ({"rc": 1}, "error"),
    ({"rc": 0, "halted": True}, "error"),
    ({"rc": 0, "timed_out": True}, "error"),
    ({"rc": 0, "model_failed": True}, "error"),
    ({"rc": 0, "stopped": True, "model_failed": True}, "stopped"),
])
def test_the_outcome_words(kw, expected):
    assert app_chat_turns.outcome(**kw) == expected


def test_the_end_is_posted_to_the_declared_path_at_the_current_port(
        app_tree, posts, monkeypatch):
    app_tree({"gateway": {"service": "api", "path": "/llm", "turn_end": "turn-ended"}})
    monkeypatch.setattr(app_runner, "app_status", lambda slug: {"services": [
        {"name": "api", "port": 8799, "status": "running"}]})
    _announce("stopped")
    assert posts == [{
        "url": "http://127.0.0.1:8799/llm/turn-ended",
        "headers": {"X-Workshop-Thread": "t-1", "X-Workshop-Thread-Title": "A%20b",
                    "X-Workshop-App": SLUG, "X-Workshop-Turn": "turn-9"},
        "json": {"thread": "t-1", "turn": "turn-9", "outcome": "stopped"},
        "timeout": app_chat_turns.POST_TIMEOUT_S}]


def test_a_turn_its_model_failed_says_so(app_tree, posts, monkeypatch):
    """Only on an `error`: a Stop is the operator's, whatever failed under it."""
    app_tree({"gateway": {"service": "api", "path": "/llm", "turn_end": "turn-ended"}})
    monkeypatch.setattr(app_chat_turns.app_chat_gateway, "gateway_route",
                        lambda thread, turn: ("http://gw", {}))
    for ended in ("error", "stopped"):
        app_chat_turns.announce(THREAD, "turn-9", ended, model_failed=True).join(5)
    assert [p["json"] for p in posts] == [
        {"thread": "t-1", "turn": "turn-9", "outcome": "error", "model_failed": True},
        {"thread": "t-1", "turn": "turn-9", "outcome": "stopped"}]


def test_the_clis_api_error_result_is_read_as_a_model_failure():
    """The CLI's line for a request it gave up on: exit 0, subtype success."""
    line = ('{"type": "result", "subtype": "success", "is_error": true,'
            ' "terminal_reason": "api_error", "api_error_status": 500}')
    kind, payload = build_streaming._parse_stream_event(line)
    assert (kind, payload["api_error"]) == ("result", True)
    ok = build_streaming._parse_stream_event('{"type": "result", "subtype": "success"}')
    assert ok[1]["api_error"] is False


def test_a_gateway_on_another_apps_service_is_reached_at_that_apps_port(
        app_tree, posts, monkeypatch):
    """`gateway.app`: an app with no queue of its own lets another app's
    service hold its chat's requests, and that service hears its turns end."""
    from services import app_links
    app_tree({"gateway": {"app": "queuer", "service": "api", "path": "/llm",
                          "turn_end": "turn-ended"}})
    linked = app_links.linked_project_slug
    monkeypatch.setattr(app_links, "linked_project_slug",
                        lambda slug: "queuer-proj" if slug == "queuer" else linked(slug))
    ports = {"queuer-proj": 8711}
    monkeypatch.setattr(app_runner, "app_status", lambda slug: {"services": [
        {"name": "api", "port": ports.get(slug, 8799), "status": "running"}]})
    _announce("finished")
    assert [p["url"] for p in posts] == ["http://127.0.0.1:8711/llm/turn-ended"]


def test_an_app_that_declares_no_turn_end_is_not_called(app_tree, posts):
    app_tree({"gateway": {"service": "api"}})
    _announce()
    assert posts == []


def test_another_scope_is_not_called(app_tree, posts):
    app_tree({"gateway": {"service": "api", "turn_end": "/turn-ended"}})
    assert _announce(thread={"id": "t-2", "scope": "general"}) is None
    assert posts == []


def test_an_unknown_outcome_starts_nothing(app_tree, posts):
    app_tree({"gateway": {"service": "api", "turn_end": "/turn-ended"}})
    assert _announce("exploded") is None and posts == []


def test_a_gateway_that_fails_costs_nothing(app_tree, monkeypatch):
    app_tree({"gateway": {"service": "api", "turn_end": "/turn-ended"}})

    def boom(*a, **kw):
        raise app_chat_turns.httpx.ConnectError("refused")
    monkeypatch.setattr(app_chat_turns.httpx, "post", boom)
    assert not _announce().is_alive()


class TestEveryEndingIsAnnounced:
    @pytest.fixture()
    def heard(self, monkeypatch):
        got: list[tuple] = []
        monkeypatch.setattr(app_chat_turns, "announce", lambda thread, turn, ended, **kw:
                            got.append(((thread or {}).get("id"), turn, ended)
                                       + ((True,) if kw.get("model_failed") else ())))
        return got

    @pytest.fixture()
    def thread(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
        monkeypatch.setattr(build_streaming, "TURNS_DIR", tmp_path / "turns")
        monkeypatch.setattr(chat_drain, "schedule_auto_resume", lambda *a, **k: None)
        ownership.set_current_owner(1)
        try:
            yield chat_store.create_thread("general")
        finally:
            ownership.clear_current_owner()
            build_streaming.TURNS.clear()

    @pytest.mark.parametrize("kw,expected", [
        ({"rc": 0}, "finished"),
        ({"rc": -15, "stopped": True}, "stopped"),
        ({"rc": -1, "lost": True}, "lost"),
        ({"rc": -1}, "error"),
        ({"rc": 0, "model_failed": True}, "error"),
    ])
    def test_finalize_announces_its_outcome(self, heard, monkeypatch, kw, expected):
        async def commit(*a, **k):
            return None
        monkeypatch.setattr(build_streaming, "_commit_turn", commit)
        monkeypatch.setattr(chat_drain, "drain_soon", lambda *a, **k: None)
        turn = build_streaming.Turn(id="turn-1", thread_id="t-1", agent_slug="a")
        monkeypatch.setattr(build_streaming, "_thread_brief", lambda tid: {"id": tid})
        asyncio.run(build_streaming._finalize_turn(
            turn, {}, timed_out=False, compacted=False, **kw))
        assert heard == [("t-1", "turn-1", expected)
                         + ((True,) if kw.get("model_failed") else ())]

    def test_the_brief_names_the_threads_scope_without_folding_it(self, thread):
        brief = build_streaming._thread_brief(thread["id"])
        assert brief == {"id": thread["id"], "scope": "workshop",
                         "title": thread.get("title") or ""}

    def test_a_turn_the_restart_lost_is_announced(self, thread, heard):
        chat_store.begin_turn(thread["id"], "run-gone")
        build_streaming.reconcile_threads()
        assert heard == [(thread["id"], "run-gone", "lost")]

    def test_a_dead_turn_taken_over_by_a_claim_is_announced(self, thread, heard):
        chat_store.begin_turn(thread["id"], "run-dead")
        assert asyncio.run(build_streaming.claim_turn(thread["id"], "run-new")) is None
        assert heard == [(thread["id"], "run-dead", "lost")]
