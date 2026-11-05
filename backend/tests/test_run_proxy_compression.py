"""The app proxy passes compression through.

It used to send every app `Accept-Encoding: identity` (for an HTML overlay it
no longer injects) and drop `Content-Encoding` from answers, so an app's large
answers crossed the network uncompressed: 58 MB instead of 12 MB to open a
graph editor. Now the browser's Accept-Encoding reaches the app, and a body the
app compressed arrives compressed with its header.
"""
from __future__ import annotations

import gzip

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import base_apps
from services import app_runner

TEXT = b'{"node": "' + b"x" * 5000 + b'"}'


def _proxy(monkeypatch):
    seen: dict = {}

    def app_status(slug):
        return {"services": [{"name": "api", "port": 9, "status": "running"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["accept"] = request.headers.get("accept-encoding")
        packed = "gzip" in (seen["accept"] or "")
        body = gzip.compress(TEXT) if packed else TEXT
        headers = {"content-type": "application/json", "content-length": str(len(body))}
        if packed:
            headers["content-encoding"] = "gzip"
        return httpx.Response(200, stream=httpx.ByteStream(body), headers=headers)

    real = httpx.AsyncClient
    monkeypatch.setattr(app_runner, "app_status", app_status)
    monkeypatch.setattr(base_apps.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    app = FastAPI()
    app.include_router(base_apps.router, prefix="/base-apps")
    return TestClient(app), seen


def test_the_browsers_accept_encoding_reaches_the_app_and_gzip_comes_back(monkeypatch):
    tc, seen = _proxy(monkeypatch)
    with tc:
        r = tc.get("/base-apps/run/x/proxy/api/object_info",
                   headers={"accept-encoding": "gzip, deflate"})
    assert seen["accept"] == "gzip, deflate"
    assert r.headers.get("content-encoding") == "gzip"
    assert r.content == TEXT


def test_a_browser_that_asks_for_nothing_gets_it_plain(monkeypatch):
    tc, seen = _proxy(monkeypatch)
    with tc:
        r = tc.get("/base-apps/run/x/proxy/api/object_info",
                   headers={"accept-encoding": "identity"})
    assert seen["accept"] == "identity"
    assert "content-encoding" not in r.headers
    assert r.content == TEXT
