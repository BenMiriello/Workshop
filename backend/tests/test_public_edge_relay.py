"""The public edge must RELAY media, not collect it.

`_proxy` used to `await upstream.aread()` for everything that was not SSE, so
the edge downloaded a whole response into this process before the browser saw a
byte. For JSON that is invisible; for the media a base app serves it defeats
progressive playback entirely and adds the file's own transfer time to every
picture. These tests pin the fork: a ranged or media response is streamed, an
ordinary JSON response still takes the collected path, and the read timeout
that protects the edge from a stalled upstream is still set on both.
"""
from __future__ import annotations

import asyncio
import types

import httpx
import pytest
from starlette.responses import StreamingResponse

import workshop_public_app as edge


def _request(method: str = "GET", headers: dict | None = None) -> types.SimpleNamespace:
    """The bits of a Starlette Request that `_proxy` actually touches."""
    async def body() -> bytes:
        return b""
    return types.SimpleNamespace(
        method=method,
        headers=httpx.Headers(headers or {}),
        query_params=httpx.QueryParams(),
        body=body,
    )


class _Upstream:
    """A canned httpx response whose body is only produced when iterated."""

    def __init__(self, status: int, headers: dict, chunks: list[bytes]) -> None:
        self.status_code = status
        self.headers = httpx.Headers(headers)
        self._chunks = chunks
        self.read_called = False
        self.iterated = False

    async def aread(self) -> bytes:
        self.read_called = True
        return b"".join(self._chunks)

    async def aiter_raw(self):
        self.iterated = True
        for c in self._chunks:
            yield c

    async def aclose(self) -> None:
        pass


def _install(monkeypatch, upstream: _Upstream) -> dict:
    seen: dict = {}

    async def handle(req: httpx.Request):
        seen["timeout"] = req.extensions.get("timeout")
        return upstream

    monkeypatch.setattr(edge._transport, "handle_async_request", handle)
    return seen


def _run(coro):
    return asyncio.run(coro)


def test_partial_content_is_relayed_not_collected(monkeypatch):
    up = _Upstream(206, {"content-type": "video/mp4",
                         "content-range": "bytes 0-1023/1048576"},
                   [b"a" * 512, b"b" * 512])
    _install(monkeypatch, up)
    resp = _run(edge._proxy(_request(), "/api/base-apps/run/x/proxy/api/media/1/file"))

    assert isinstance(resp, StreamingResponse)
    assert resp.status_code == 206
    # The whole point: the body was never pulled into this process up front.
    assert up.read_called is False
    assert resp.headers["content-range"] == "bytes 0-1023/1048576"
    assert resp.headers["x-accel-buffering"] == "no"


def test_video_without_a_range_is_still_relayed(monkeypatch):
    up = _Upstream(200, {"content-type": "video/mp4", "content-length": "40"},
                   [b"x" * 40])
    _install(monkeypatch, up)
    resp = _run(edge._proxy(_request(), "/api/base-apps/run/x/proxy/api/media/1/file"))
    assert isinstance(resp, StreamingResponse)
    assert up.read_called is False


def test_a_big_body_of_any_type_is_relayed(monkeypatch):
    big = str(edge._RELAY_BYTES + 1)
    up = _Upstream(200, {"content-type": "application/octet-stream",
                         "content-length": big}, [b"z" * 16])
    _install(monkeypatch, up)
    resp = _run(edge._proxy(_request(), "/api/base-apps/run/x/proxy/api/blob"))
    assert isinstance(resp, StreamingResponse)


def test_ordinary_json_still_takes_the_collected_path(monkeypatch):
    up = _Upstream(200, {"content-type": "application/json", "content-length": "13"},
                   [b'{"ok": true}\n'])
    _install(monkeypatch, up)
    resp = _run(edge._proxy(_request(), "/api/projects"))

    assert not isinstance(resp, StreamingResponse)
    # Collected from the raw stream, not `.aread()`, so a gzip body stays gzip.
    assert up.read_called is False
    assert resp.body == b'{"ok": true}\n'


def test_a_relayed_request_still_has_a_read_timeout(monkeypatch):
    """A stalled upstream must not hold the edge open forever. Only SSE waives
    this, and a video is not SSE."""
    up = _Upstream(206, {"content-type": "video/mp4",
                         "content-range": "bytes 0-9/100"}, [b"0123456789"])
    seen = _install(monkeypatch, up)
    _run(edge._proxy(_request(), "/api/base-apps/run/x/proxy/api/media/1/file"))
    assert seen["timeout"]["read"] == 120.0


def test_relayed_body_is_byte_identical(monkeypatch):
    chunks = [b"head", b"middle", b"tail"]
    up = _Upstream(206, {"content-type": "video/mp4",
                         "content-range": "bytes 0-13/14"}, list(chunks))
    _install(monkeypatch, up)
    resp = _run(edge._proxy(_request(), "/api/base-apps/run/x/proxy/api/media/1/file"))

    async def drain():
        out = b""
        async for c in resp.body_iterator:
            out += c
        return out

    assert _run(drain()) == b"".join(chunks)


def test_a_relayed_range_keeps_its_length(monkeypatch):
    """Without it the range goes out chunked, of unknown size, which Safari's
    player handles badly."""
    up = _Upstream(206, {"content-type": "video/mp4", "content-length": "10",
                         "content-range": "bytes 0-9/100"}, [b"0123456789"])
    _install(monkeypatch, up)
    resp = _run(edge._proxy(_request(), "/api/base-apps/run/x/proxy/api/media/1/file"))
    assert resp.headers["content-length"] == "10"
    assert "transfer-encoding" not in resp.headers
