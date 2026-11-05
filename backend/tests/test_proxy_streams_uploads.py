"""An upload passes through the app proxy and the public edge as it arrives.

Both hops used to `await request.body()` before forwarding, so a multi-GB
upload to an app sat whole in the backend's memory (and the edge's) first.
A body over `_BUFFER_MAX`, or one sent without a length, is now streamed;
small bodies keep the buffered path, and the bytes arrive intact either way.
"""
from __future__ import annotations

import asyncio
import types

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

import workshop_public_app as edge
from routes import base_apps
from services import app_runner

BIG = b"x" * (3 << 20)


def _proxy(monkeypatch):
    seen: dict = {}

    def app_status(slug):
        return {"services": [{"name": "api", "port": 9, "status": "running"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        seen["length"] = request.headers.get("content-length")
        return httpx.Response(200, stream=httpx.ByteStream(b"{}"),
                              headers={"content-type": "application/json"})

    real = httpx.AsyncClient
    monkeypatch.setattr(app_runner, "app_status", app_status)
    monkeypatch.setattr(base_apps.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    app = FastAPI()
    app.include_router(base_apps.router, prefix="/base-apps")
    return TestClient(app), seen


def test_a_big_upload_is_streamed_through_the_app_proxy(monkeypatch):
    tc, seen = _proxy(monkeypatch)

    async def no_collect(self):
        raise AssertionError("the proxy collected the whole upload")
    monkeypatch.setattr(Request, "body", no_collect)
    with tc:
        r = tc.post("/base-apps/run/x/proxy/api/upload", content=BIG)
    assert r.status_code == 200
    assert seen["body"] == BIG and seen["length"] == str(len(BIG))


def test_a_small_body_still_arrives_whole(monkeypatch):
    tc, seen = _proxy(monkeypatch)
    with tc:
        assert tc.post("/base-apps/run/x/proxy/api/a", json={"a": 1}).status_code == 200
    assert seen["body"] == b'{"a": 1}'


def test_the_edge_relays_a_big_upload_without_collecting_it(monkeypatch):
    got: dict = {}

    async def stream():
        for i in range(0, len(BIG), 1 << 20):
            yield BIG[i:i + (1 << 20)]

    async def body():
        raise AssertionError("the edge collected the whole upload")

    req = types.SimpleNamespace(
        method="POST", headers=httpx.Headers({"content-length": str(len(BIG))}),
        query_params=httpx.QueryParams(), body=body, stream=stream)

    class Upstream:
        status_code = 200
        headers = httpx.Headers({"content-type": "application/json"})

        async def aiter_raw(self):
            yield b"{}"

        async def aclose(self):
            pass

    async def handle(r: httpx.Request):
        got["body"] = b"".join([c async for c in r.stream])
        got["length"] = r.headers.get("content-length")
        return Upstream()

    monkeypatch.setattr(edge._transport, "handle_async_request", handle)
    resp = asyncio.run(edge._proxy(req, "/base-apps/run/x/proxy/api/upload"))
    assert resp.status_code == 200
    assert got["body"] == BIG and got["length"] == str(len(BIG))
