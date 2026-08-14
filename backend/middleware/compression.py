"""SSE-safe gzip compression middleware.

The backend shipped with no compression middleware at all, so every response
went out raw. Measured wins on real payloads (see the phase-2 perf ticket):
a chat thread's event log dropped from 4.8MB to 1.6MB (3.0x), a thread list
from 33.7KB to 7.9KB (4.2x), a task list from 58.1KB to 11.8KB (4.9x).

DO NOT replace this with starlette's stock `GZipMiddleware`
(starlette/middleware/gzip.py). Its responder is unsafe for a streaming
(`more_body=True`) response: it writes each chunk into a `gzip.GzipFile` and
reads back `gzip_buffer.getvalue()` without ever calling `.flush()`. Python's
DEFLATE writer buffers internally, so a stream of small frames -- exactly what
Server-Sent Events looks like ("event: changed\ndata: 3\n\n", ~20 bytes) --
sits in the compressor's internal window and is not emitted until enough
bytes accumulate, or never, for a turn that idles between tokens. That would
silently stall live chat streaming, the working indicator, and voice: the
single worst regression available in this change. SSE endpoints in this repo
(backend/routes/chat.py's thread-change and turn streams, and anything using
`media_type="text/event-stream"`) MUST keep streaming incrementally.

This middleware sidesteps the whole hazard by never attempting incremental
(chunk-by-chunk) compression at all:

  - A response whose Content-Type is announced up front as `text/event-stream`,
    or that already carries a Content-Encoding (already compressed upstream,
    e.g. a proxied app response -- see base_apps.py), is forwarded untouched
    from the very first message.
  - Any OTHER response is classified by its first body message: if it arrives
    complete in one shot (`more_body` false/absent, the normal case for a
    FastAPI/Starlette `Response` or `JSONResponse`), it is compressed with a
    single `gzip.compress()` call -- no incremental writer, so no flush to
    forget. If instead it turns out to be a multi-chunk stream we didn't
    already recognize by content-type (e.g. a proxied StreamingResponse of
    unknown shape in base_apps.py's dev proxy), it is left uncompressed rather
    than risk the same stall. Nothing that streams is ever encoded here.

So compression is strictly opt-in per response: single-shot body, gzip
supported by the client, big enough to be worth it. Everything else -- by
content-type, by Content-Encoding, or by simply being chunked -- passes
through byte-for-byte.
"""

from __future__ import annotations

import gzip

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_SKIP_CONTENT_TYPES = ("text/event-stream",)


class SSEAwareGZipMiddleware:
    """Gzip single-shot responses; leave streaming/SSE responses untouched.

    minimum_size: bodies smaller than this aren't worth the CPU -- gzip's own
    framing overhead can erase the saving on a tiny response.
    compresslevel: 5, not gzip's default of 9. Level 9 measured 87ms on a
    ~4.9MB JSON payload for only a couple points of extra ratio over 5-6;
    that is latency a user would feel for a saving they would not.
    """

    def __init__(self, app: ASGIApp, minimum_size: int = 500, compresslevel: int = 5) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            if "gzip" in headers.get("accept-encoding", ""):
                responder = _Responder(self.app, self.minimum_size, self.compresslevel)
                await responder(scope, receive, send)
                return
        await self.app(scope, receive, send)


class _Responder:
    """One instance per request. Holds the `http.response.start` message
    until it can decide, from either the announced headers or the shape of
    the first body chunk, whether this response is eligible for compression.
    Once decided, every later message is forwarded through unmodified --
    there is no per-chunk compression path in this middleware at all."""

    def __init__(self, app: ASGIApp, minimum_size: int, compresslevel: int) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel
        self.send: Send = _unattached_send
        self.initial_message: Message = {}
        self.decided = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.send = send
        await self.app(scope, receive, self._send_wrapper)

    async def _send_wrapper(self, message: Message) -> None:
        message_type = message["type"]

        if message_type == "http.response.start":
            self.initial_message = message
            headers = Headers(raw=message["headers"])
            content_type = headers.get("content-type", "")
            never_touch = (
                any(content_type.startswith(ct) for ct in _SKIP_CONTENT_TYPES)
                or "content-encoding" in headers
            )
            if never_touch:
                self.decided = True
                await self.send(message)
            # Otherwise hold it: we don't yet know if the body is complete in
            # one message or a stream, and that's what decides compression.
            return

        if self.decided:
            # Already classified, either as "never touch" above, or as a
            # stream below (first chunk had more_body=True). Every later
            # chunk of a streaming response also lands here.
            await self.send(message)
            return

        # First (and possibly only) http.response.body message of a response
        # we hadn't classified from its headers.
        self.decided = True
        body = message.get("body", b"")
        more_body = message.get("more_body", False)

        if more_body:
            # A multi-chunk response we didn't recognize by content-type.
            # Compressing it would need per-chunk flush() semantics this
            # middleware deliberately does not implement (see module
            # docstring) -- leave it alone rather than risk a stall.
            await self.send(self.initial_message)
            await self.send(message)
            return

        if len(body) < self.minimum_size:
            await self.send(self.initial_message)
            await self.send(message)
            return

        compressed = gzip.compress(body, compresslevel=self.compresslevel)
        headers = MutableHeaders(raw=self.initial_message["headers"])
        headers["Content-Encoding"] = "gzip"
        headers["Content-Length"] = str(len(compressed))
        headers.add_vary_header("Accept-Encoding")
        message["body"] = compressed
        await self.send(self.initial_message)
        await self.send(message)


async def _unattached_send(message: Message) -> None:  # pragma: no cover
    raise RuntimeError("send() awaited before this responder was attached to a scope")
