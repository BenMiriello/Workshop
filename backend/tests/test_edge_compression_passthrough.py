"""The public edge must forward the backend's gzip compression to the real
client, not throw it away in the middle.

Before this fix, `_proxy()` read the upstream body with `httpx.Response.aread()`
(which transparently DECODES a gzip Content-Encoding as part of reading) and
then dropped the Content-Encoding response header via `_DROP_RESP`. Net effect:
the backend (phase-2 perf fix, middleware/compression.py) would compress a
response FOR the public visitor's browser, and this proxy would silently
decompress it again before re-serving it plain -- so the loopback hop between
backend and edge got smaller, at real CPU cost, while the actual internet-
facing hop to the browser -- the one this whole change exists to help -- saw
zero benefit. This test drives `_proxy()` end to end (through the real FastAPI
app, with only the upstream transport faked) and asserts the compressed bytes
and the Content-Encoding header both survive the hop unmodified.
"""
from __future__ import annotations

import gzip

import pytest
from fastapi.testclient import TestClient


class _FakeUpstreamResponse:
    """Duck-types the subset of httpx.Response that _proxy() touches:
    .status_code, .headers (a mapping) and async .aiter_raw()/.aclose(). Not a
    real httpx.Response, on purpose -- a real one materializes `content=` eagerly
    and marks its stream consumed, which would hide the exact bug (`.aread()`
    auto-decoding) this test exists to catch. This fake instead hands back
    whatever raw bytes the test gives it, exactly as the wire would."""

    def __init__(self, status_code: int, headers: dict, body: bytes):
        self.status_code = status_code
        self.headers = headers
        self._body = body
        self.aclose_called = False

    async def aiter_raw(self):
        yield self._body

    async def aclose(self):
        self.aclose_called = True


@pytest.fixture
def edge(monkeypatch):
    import workshop_public_app as edge_app

    return edge_app, monkeypatch


def test_a_compressed_backend_response_reaches_the_client_still_compressed(edge):
    edge_app, monkeypatch = edge
    payload = b'{"threads": [' + b",".join([b'"row"'] * 2000) + b"]}"
    compressed = gzip.compress(payload)

    async def fake_send_upstream(request, backend_path, headers, body, *, stream):
        assert not stream
        return _FakeUpstreamResponse(
            200,
            {"content-type": "application/json", "content-encoding": "gzip"},
            compressed,
        )

    monkeypatch.setattr(edge_app, "_send_upstream", fake_send_upstream)

    resp = TestClient(edge_app.app).get(
        "/api/chat/threads", headers={"accept-encoding": "gzip"}
    )

    assert resp.status_code == 200
    # This is the assertion that actually catches the old bug: the test
    # client (httpx underneath) auto-decodes Content-Encoding when reading
    # `.content`, on the OLD (broken) code path AND the fixed one alike --
    # the browser-visible bytes look identical either way. What differs is
    # whether the header -- and the still-compressed bytes it describes --
    # made it across the wire at all, which is exactly what this checks.
    assert resp.headers.get("content-encoding") == "gzip", (
        "the edge dropped Content-Encoding -- a real browser would receive "
        "gzip bytes with no header telling it to decode them"
    )
    assert resp.content == payload  # round-trips once the test client decodes it


def test_an_uncompressed_backend_response_is_unaffected(edge):
    """A response the backend chose not to compress (e.g. below its own
    minimum_size) must pass through with no Content-Encoding, same as always."""
    edge_app, monkeypatch = edge
    payload = b'{"ok": true}'

    async def fake_send_upstream(request, backend_path, headers, body, *, stream):
        return _FakeUpstreamResponse(200, {"content-type": "application/json"}, payload)

    monkeypatch.setattr(edge_app, "_send_upstream", fake_send_upstream)

    resp = TestClient(edge_app.app).get("/api/chat/threads")

    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers
    assert resp.content == payload
