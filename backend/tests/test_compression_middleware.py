"""Compression middleware: SSE must stream through untouched, and buffered
non-streaming responses must compress and round-trip.

Driven straight at the ASGI interface (no server, no ports) so it proves what
actually matters: whether each `http.response.body` message the middleware
SENDS reflects the calling app's own chunk boundaries, rather than something
merged, delayed, or garbled by an internal buffer. That is the property the
stock starlette GZipMiddleware breaks for streaming responses -- see
middleware/compression.py's module docstring for the flush() hazard -- so this
test exists to make sure the replacement never regresses to the same bug.
"""
from __future__ import annotations

import asyncio
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from middleware.compression import SSEAwareGZipMiddleware  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _http_scope(accept_encoding: str = "gzip, deflate, br") -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": "/whatever",
        "headers": [(b"accept-encoding", accept_encoding.encode())],
    }


async def _drive(app, scope) -> list[dict]:
    """Run `app` through the middleware and collect every message it sent
    onward, in order."""
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = SSEAwareGZipMiddleware(app, minimum_size=500, compresslevel=5)
    await middleware(scope, receive, send)
    return sent


def _headers_dict(message) -> dict:
    return dict(message["headers"])


# ---------------------------------------------------------------------------
# SSE must never be buffered
# ---------------------------------------------------------------------------

def test_sse_chunks_are_forwarded_one_by_one_uncompressed():
    """Each chunk the SSE generator yields must arrive as its own
    `http.response.body` message, byte-for-byte, with no Content-Encoding. If
    the middleware ever buffered these into a gzip.GzipFile without a
    flush(), this test's per-message bodies would stop matching the frames
    the app actually sent (some empty, some merged) -- the live-chat-stalls-
    until-enough-bytes-accumulate bug this module exists to prevent."""
    frames = [
        b"event: changed\ndata: 1\n\n",
        b": keepalive\n\n",
        b"event: changed\ndata: 2\n\n",
    ]

    async def sse_app(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        })
        for i, frame in enumerate(frames):
            await send({
                "type": "http.response.body",
                "body": frame,
                "more_body": i < len(frames) - 1,
            })

    sent = _run(_drive(sse_app, _http_scope()))

    start = sent[0]
    assert start["type"] == "http.response.start"
    assert b"content-encoding" not in _headers_dict(start)

    body_messages = [m for m in sent if m["type"] == "http.response.body"]
    assert len(body_messages) == len(frames)
    for expected, actual in zip(frames, body_messages):
        assert actual["body"] == expected  # untouched: not merged, not gzipped


def test_sse_is_skipped_even_without_client_accept_encoding():
    """An SSE stream is exempt on content-type alone, at the same layer as a
    client that never asked for gzip -- both must produce byte-identical
    passthrough."""
    async def sse_app(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        })
        await send({"type": "http.response.body", "body": b"event: x\ndata: 1\n\n", "more_body": False})

    sent = _run(_drive(sse_app, _http_scope(accept_encoding="")))
    body_messages = [m for m in sent if m["type"] == "http.response.body"]
    assert body_messages[0]["body"] == b"event: x\ndata: 1\n\n"


# ---------------------------------------------------------------------------
# Buffered (non-streaming) responses compress and round-trip
# ---------------------------------------------------------------------------

def test_large_json_response_is_compressed_and_round_trips():
    payload = ('{"items": [' + ",".join(f'"row-{i}"' for i in range(2000)) + "]}").encode()
    assert len(payload) > 2000  # comfortably over minimum_size

    async def json_app(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": payload, "more_body": False})

    sent = _run(_drive(json_app, _http_scope()))

    headers = _headers_dict(next(m for m in sent if m["type"] == "http.response.start"))
    assert headers[b"content-encoding"] == b"gzip"

    compressed = next(m for m in sent if m["type"] == "http.response.body")["body"]
    assert compressed.startswith(b"\x1f\x8b")  # gzip magic
    assert len(compressed) < len(payload)
    assert gzip.decompress(compressed) == payload
    assert headers[b"content-length"] == str(len(compressed)).encode()


def test_small_response_is_left_uncompressed():
    payload = b'{"ok": true}'

    async def small_app(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": payload, "more_body": False})

    sent = _run(_drive(small_app, _http_scope()))
    headers = _headers_dict(next(m for m in sent if m["type"] == "http.response.start"))
    assert b"content-encoding" not in headers
    body = next(m for m in sent if m["type"] == "http.response.body")["body"]
    assert body == payload


def test_already_encoded_response_is_left_alone():
    """A response that already carries Content-Encoding (e.g. a proxied app
    response forwarded by base_apps.py) must never be re-encoded."""
    payload = gzip.compress(b"x" * 5000)

    async def pre_encoded_app(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/octet-stream"),
                        (b"content-encoding", b"gzip")],
        })
        await send({"type": "http.response.body", "body": payload, "more_body": False})

    sent = _run(_drive(pre_encoded_app, _http_scope()))
    body = next(m for m in sent if m["type"] == "http.response.body")["body"]
    assert body == payload  # not double-gzipped


def test_unrecognized_streaming_response_is_not_compressed():
    """A multi-chunk response that isn't announced as text/event-stream (e.g.
    base_apps.py's dev proxy, which streams arbitrary content-types from a
    sub-app) must still be left alone -- compressing it would need the same
    per-chunk flush() this middleware deliberately does not implement."""
    chunks = [b"chunk-one" * 100, b"chunk-two" * 100]

    async def streaming_app(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        for i, c in enumerate(chunks):
            await send({"type": "http.response.body", "body": c, "more_body": i < len(chunks) - 1})

    sent = _run(_drive(streaming_app, _http_scope()))
    headers = _headers_dict(next(m for m in sent if m["type"] == "http.response.start"))
    assert b"content-encoding" not in headers
    body_messages = [m for m in sent if m["type"] == "http.response.body"]
    assert [m["body"] for m in body_messages] == chunks


# --- the ordering contract -------------------------------------------------
#
# These two tests exist because the middleware was correct, wired, and still
# compressed absolutely nothing in production for several hours on 2026-08-14.
# `add_middleware` inserts at the FRONT of the stack, so the last registration
# is the OUTERMOST. Compression was registered last, which put it outside
# `strip_api_prefix` -- a BaseHTTPMiddleware, and BaseHTTPMiddleware re-emits
# every response as a chunked stream. This middleware refuses to compress a
# chunked body on purpose (no per-chunk flush; stalling SSE is the worst
# failure available here), so it saw only chunks and did nothing, silently.
# A 4.83MB events response went out raw with compression "on".
#
# Nothing about that failure is loud: no error, no warning, just a big response.
# So the position is pinned here instead.

def test_compression_is_registered_innermost_in_the_real_app():
    """Compression must sit CLOSEST to the router, so it sees a JSONResponse
    as the single complete body message it is. `user_middleware` is ordered
    outermost-first, so innermost is the LAST entry."""
    import main

    names = [m.cls.__name__ for m in main.app.user_middleware]
    assert names[-1] == "SSEAwareGZipMiddleware", (
        "SSEAwareGZipMiddleware must be registered FIRST (making it innermost). "
        f"Current order, outermost first: {names}. If it sits outside a "
        "BaseHTTPMiddleware, every response reaches it pre-chunked and "
        "compression silently turns itself off."
    )


def test_a_base_http_middleware_outside_compression_would_disable_it():
    """The mechanism itself, so the reason the order matters stays legible.

    Same app, same middleware, only the registration order differs: one
    compresses, one cannot. If this ever stops holding, the ordering
    requirement above is obsolete and can be revisited."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from starlette.testclient import TestClient

    payload = {"rows": ["lorem ipsum dolor sit amet " * 20 for _ in range(200)]}

    def build(gzip_registered_first: bool) -> FastAPI:
        app = FastAPI()
        if gzip_registered_first:
            app.add_middleware(SSEAwareGZipMiddleware, minimum_size=500, compresslevel=5)

        @app.middleware("http")
        async def _passthrough(request, call_next):
            return await call_next(request)

        if not gzip_registered_first:
            app.add_middleware(SSEAwareGZipMiddleware, minimum_size=500, compresslevel=5)

        @app.get("/payload")
        async def _payload():
            return JSONResponse(payload)

        return app

    inner = TestClient(build(True)).get("/payload", headers={"Accept-Encoding": "gzip"})
    outer = TestClient(build(False)).get("/payload", headers={"Accept-Encoding": "gzip"})

    assert inner.headers.get("content-encoding") == "gzip"
    assert outer.headers.get("content-encoding") is None
