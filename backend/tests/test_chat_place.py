"""Where a chat is working (services/chat_place.py): an app's `chat.place`
declaration, and the answers of the apps a chat reaches, newest first.

Pinned: a place must name `{thread}` or it is ignored; an app that answers no
path, an error or nothing in time is left out; the most recently worked place
comes first.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx

from services import app_chat, chat_apps, chat_place
from tests.test_app_chat_and_local_harness import SLUG, app_tree  # noqa: F401


def test_a_place_must_name_the_thread(app_tree):
    app_tree({"mcp": {"service": "api"}, "place": "/api/chat-place/{thread}"})
    assert app_chat.declaration(SLUG).place == "/api/chat-place/{thread}"
    app_tree({"mcp": {"service": "api"}, "place": "/api/chat-place"})
    assert app_chat.declaration(SLUG).place is None
    app_tree({"mcp": {"service": "api"}})
    assert app_chat.declaration(SLUG).place is None


def _decl(slug: str, place: str | None):
    base = app_chat.ChatDeclaration(app_slug=slug, project=slug, root=None, label=slug.title(),  # type: ignore[arg-type]
                                    persona_file=None, mcp_service="app", mcp_path="/mcp", model=None)
    return replace(base, place=place)


def test_places_are_asked_of_each_app_and_come_newest_first(monkeypatch):
    decls = [_decl("tabs", "/api/chat-place/{thread}"), _decl("pics", "/place/{thread}"),
             _decl("quiet", "/p/{thread}"), _decl("plain", None)]
    monkeypatch.setattr(chat_place, "own_slug", lambda t: "tabs")
    monkeypatch.setattr(chat_place.chat_store, "get_thread_app_access", lambda t: {"pics": [], "quiet": [], "plain": []})
    monkeypatch.setattr(chat_apps, "grantable", lambda: decls)
    ports = {"tabs": 9001, "pics": 9002, "quiet": 9003, "plain": 9004}
    monkeypatch.setattr(app_chat, "service_port", lambda project, service: ports[project])
    asked = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.port == 9001:
            return httpx.Response(200, json={"label": "example.com", "path": "/apps/tabs/ui/app#tab=a", "at": 100.0})
        if request.url.port == 9002:
            return httpx.Response(200, json={"label": "cat.png", "path": "/apps/pics/ui/web#item=7", "at": 200.0})
        return httpx.Response(200, json={})

    real = httpx.AsyncClient
    monkeypatch.setattr(chat_place.httpx, "AsyncClient", lambda: real(transport=httpx.MockTransport(answer)))
    places = asyncio.run(chat_place.places("t-1"))
    assert [p["app"] for p in places] == ["pics", "tabs"]
    assert places[1] == {"app": "tabs", "app_label": "Tabs", "path": "/apps/tabs/ui/app#tab=a",
                         "label": "example.com", "title": "", "at": 100.0}
    assert sorted(asked) == ["http://127.0.0.1:9001/api/chat-place/t-1", "http://127.0.0.1:9002/place/t-1",
                             "http://127.0.0.1:9003/p/t-1"]


def test_an_app_that_fails_is_left_out(monkeypatch):
    monkeypatch.setattr(chat_place, "_asks", lambda t: [(_decl("tabs", "/p/{thread}"), "http://127.0.0.1:9001/p/t")])

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    real = httpx.AsyncClient
    monkeypatch.setattr(chat_place.httpx, "AsyncClient", lambda: real(transport=httpx.MockTransport(broken)))
    assert asyncio.run(chat_place.places("t")) == []
