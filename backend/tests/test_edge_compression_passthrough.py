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

Phase 2b (this file's second half) covers the edge app's OWN two gaps, found
while auditing it for the same fix: it carried no compression middleware at
all (so the built SPA -- 706KB of JS, 3.3x smaller gzipped -- shipped raw on
every page load, the single biggest thing making the site feel slow), and its
`/assets` mount had no Cache-Control, so the content-hashed, safe-to-cache-
forever bundle was refetched every visit anyway.
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


# ---------------------------------------------------------------------------
# Phase 2b: the edge app's OWN responses (the static SPA it serves directly,
# not just what it proxies) must also compress, and its hashed assets must
# cache immutably. These drive the REAL built frontend/dist -- no fakes --
# since a fake would hide the exact bug this file's own history warns about:
# `FileResponse`'s default 64KB chunk_size sends a 700KB bundle as multiple
# ASGI messages, and SSEAwareGZipMiddleware deliberately never compresses a
# multi-chunk response it doesn't recognize (see middleware/compression.py),
# so wiring in the middleware ALONE silently failed to compress the exact
# asset motivating this change. `_ImmutableStaticFiles` in workshop_public_app
# raises chunk_size past any real asset to fix that; these tests would catch
# a regression of either half.
# ---------------------------------------------------------------------------

def _require_built_assets(edge_app):
    assets_dir = edge_app.DIST / "assets"
    if not assets_dir.is_dir():
        pytest.skip("no built frontend/dist on this checkout (run the frontend build first)")
    files = sorted(assets_dir.iterdir())
    if not files:
        pytest.skip("frontend/dist/assets is empty on this checkout")
    return files


def test_a_large_static_asset_is_served_gzipped(edge):
    edge_app, _ = edge
    files = _require_built_assets(edge_app)
    largest = max(files, key=lambda p: p.stat().st_size)
    raw = largest.read_bytes()
    assert len(raw) > 65536, "test asset should exceed FileResponse's 64KB chunk_size to be a real regression check"

    resp = TestClient(edge_app.app).get(f"/assets/{largest.name}", headers={"accept-encoding": "gzip"})

    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"
    assert resp.content == raw  # httpx decodes on read; must round-trip exactly
    wire_size = int(resp.headers["content-length"])
    assert wire_size < len(raw), "the response actually sent over the wire must be smaller than the file on disk"


def test_assets_response_carries_immutable_cache_control(edge):
    edge_app, _ = edge
    files = _require_built_assets(edge_app)

    resp = TestClient(edge_app.app).get(f"/assets/{files[0].name}")

    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "public, max-age=31536000, immutable"


def test_index_html_still_no_cache_and_not_immutable(edge):
    edge_app, _ = edge
    if not (edge_app.DIST / "index.html").is_file():
        pytest.skip("no built frontend/dist on this checkout")

    resp = TestClient(edge_app.app).get("/")

    assert resp.status_code == 200
    cache_control = resp.headers.get("cache-control", "")
    assert cache_control == "no-cache", (
        f"index.html must keep revalidating on every load so it always points at "
        f"the current hashed bundle -- got {cache_control!r}"
    )
    assert "immutable" not in cache_control


def test_sse_proxy_response_still_streams_uncompressed(edge):
    """Wiring SSEAwareGZipMiddleware into the edge app must not touch the SSE
    proxy path (`_proxy_stream`): no Content-Encoding, no buffering into a
    single-shot compressed body, content-type preserved. That the middleware
    forwards a streaming app's chunks one-for-one, unmerged, with no flush()
    hazard is proven once at the unit level in test_compression_middleware.py
    against the exact same class -- httpx's in-process ASGI test transport
    coalesces same-tick sends before a `TestClient.stream()` reader observes
    them, so re-asserting chunk boundaries through that transport here would
    test the test transport, not this app. This test covers what's specific
    to the edge integration: that the SSE proxy response reaching a real
    client is still the untouched plaintext stream, byte for byte."""
    edge_app, monkeypatch = edge
    frames = [b"event: changed\ndata: 1\n\n", b"event: changed\ndata: 2\n\n"]

    class _FakeStreamUpstream:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def aiter_raw(self):
            for f in frames:
                yield f

        async def aclose(self):
            pass

    async def fake_send_upstream(request, backend_path, headers, body, *, stream):
        assert stream
        return _FakeStreamUpstream()

    monkeypatch.setattr(edge_app, "_send_upstream", fake_send_upstream)

    resp = TestClient(edge_app.app).get(
        "/api/chat/threads/1/stream",
        headers={"accept-encoding": "gzip", "accept": "text/event-stream"},
    )

    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers
    assert resp.headers.get("content-type", "").startswith("text/event-stream")
    assert resp.content == b"".join(frames)
