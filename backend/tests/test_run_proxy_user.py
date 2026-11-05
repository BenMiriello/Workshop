"""The app proxy tells the app who is asking.

Every request forwarded to an app service carries `X-Workshop-User`, the
signed-in user's id, so the app can keep each user's data apart. The proxy
sets it itself: a copy the browser sent never reaches the app.
"""
from __future__ import annotations

import httpx
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import deps_user
from routes import base_apps
from services import app_runner


def _proxy(monkeypatch, *, signed_in: bool = True):
    seen: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get_list("x-workshop-user"))
        return httpx.Response(200, stream=httpx.ByteStream(b"{}"),
                              headers={"content-type": "application/json"})

    real = httpx.AsyncClient
    monkeypatch.setattr(app_runner, "service_target", lambda slug, svc: ("running", 9))
    monkeypatch.setattr(app_runner, "wants_running", lambda slug: False)
    monkeypatch.setattr(base_apps.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(deps_user.auth_store, "session_user",
                        lambda c: {"id": 7, "is_operator": True} if c == "op" else None)
    app = FastAPI()
    deps = [Depends(deps_user.require_operator)] if signed_in else []
    app.include_router(base_apps.router, prefix="/base-apps", dependencies=deps)
    tc = TestClient(app)
    tc.cookies.set("workshop_session", "op")
    return tc, seen


def test_the_signed_in_users_id_reaches_the_app(monkeypatch):
    tc, seen = _proxy(monkeypatch)
    with tc:
        assert tc.get("/base-apps/run/x/proxy/api/api/entries").status_code == 200
        r = tc.post("/base-apps/run/x/proxy/api/api/entries", json={"text": "t"},
                    headers={"x-workshop-client": "1"})
        assert r.status_code == 200
    assert seen == [["7"], ["7"]]


def test_a_copy_sent_with_the_request_is_replaced(monkeypatch):
    tc, seen = _proxy(monkeypatch)
    with tc:
        tc.get("/base-apps/run/x/proxy/api/", headers={"X-Workshop-User": "1"})
    assert seen == [["7"]]


def test_with_no_user_resolved_none_is_sent_and_a_forged_one_is_dropped(monkeypatch):
    tc, seen = _proxy(monkeypatch, signed_in=False)
    with tc:
        tc.get("/base-apps/run/x/proxy/api/", headers={"x-workshop-user": "1"})
    assert seen == [[]]
