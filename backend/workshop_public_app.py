"""Public edge app — the origin this install serves publicly (default port 8002).

Sits behind the Cloudflare tunnel and in front of the main backend (:8000). It
holds NO auth logic of its own — the backend authenticates the user (session
cookie / device token) and enforces ownership on every request. This process is
just two things:

  1. serve the built Workshop SPA (frontend/dist), and
  2. transparently reverse-proxy the PERSONAL + AUTH API surface to :8000,
     forwarding the cookie and the CSRF header so the backend can enforce identity.

CRITICAL: it proxies ONLY a safe surface. The backend's internal/ingest endpoints
(/tools, /relay, /services, /events, /plaid/webhook, and every */webhook,
*/ingest, /location/owntracks, /birds/analyze, /birds/live/publish) are gated by
require_loopback / require_internal_caller. Because this proxy reaches the backend
FROM loopback, forwarding those paths would let a public visitor inherit loopback
trust. So they are explicitly BLOCKED here (404) and never reach the backend. The
backend remains the single source of enforcement; this allowlist is defence in
depth for the network-trusted internal endpoints only.
"""
from __future__ import annotations

import asyncio
import logging
import os
import posixpath
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from middleware.compression import SSEAwareGZipMiddleware

log = logging.getLogger("workshop.public_edge")

BACKEND = os.environ.get("WORKSHOP_BACKEND", "http://127.0.0.1:8000")
# The same target as BACKEND, expressed as a ws:// origin for the upgrade path.
_BACKEND_WS = BACKEND.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
DIST = Path(os.environ.get("WORKSHOP_DIST", str(Path(__file__).resolve().parents[1] / "frontend" / "dist")))
_DIST_ROOT = DIST.resolve()

# Backend paths (AFTER the /api prefix is stripped) that must NEVER be reachable
# from the public origin: they are loopback/internal-trusted, unauthenticated
# ingest, or the operator-only PLATFORM control plane (agents/projects/etc.).
# The operator administers the platform over the tailnet (:8000), never the
# public origin, so the edge proxies ONLY the personal + /auth surface. Matched
# as path prefixes against the NORMALIZED stripped path.
# What stays blocked: endpoints that trust the NETWORK (loopback / internal /
# unauthenticated ingest) and would otherwise inherit the proxy's loopback trust.
# The platform routers (/agents, /projects, /worktrees, ...) are NOT blocked: they
# are require_operator-authed, so identity gates them on either origin — blocking
# them by origin would be the network-position anti-pattern this whole redesign
# removed, and it broke the operator using those pages on the public web.
_BLOCKED_PREFIXES = (
    # internal / loopback-trusted machine endpoints (no user auth). NOTE the router
    # prefix is "/auto-pr" (routes/auto_pr.py); "/auto" alone did NOT match it since
    # blocking is segment-exact after normalize, so /api/auto-pr was proxied through
    # and inherited loopback trust. Fixed 2026-07-18 (security-audit-2026-07-18 H1).
    "/tools", "/relay", "/services", "/events", "/auto-pr",
)

# An app's own unauthenticated ingest paths are declared on its AppSpec
# (edge_blocked_prefixes / edge_blocked_exact) and blocked only while the app is
# installed. Listing them here made the edge's threat surface a property of the
# platform rather than of what is actually running, and every new app meant
# editing this file to stay safe — which is how one gets forgotten.
#
# Fails CLOSED. If the installed set cannot be read, every backend path is
# refused and the edge serves the static SPA alone: an edge that cannot tell
# what to block must not guess that the answer is "nothing".
_BLOCK_ALL = "\x00block-all"


def _app_edge_blocks() -> tuple[frozenset[str], frozenset[str]]:
    try:
        from services import app_registry

        prefixes, exact = app_registry.installed_edge_blocks()
        return frozenset(prefixes), frozenset(exact)
    except Exception:
        log.exception("edge: cannot read installed apps; blocking all backend paths")
        return frozenset({_BLOCK_ALL}), frozenset()


# Hop-by-hop headers we must not forward verbatim.
_DROP_REQ = {"host", "content-length", "connection", "keep-alive", "transfer-encoding"}
# NOTE: content-encoding is deliberately NOT here. The backend (phase-2 perf
# fix, middleware/compression.py) now gzips its buffered JSON/HTML responses
# when the caller's Accept-Encoding allows it, and this proxy forwards the
# browser's own Accept-Encoding upstream unmodified (it isn't in _DROP_REQ
# either) -- so the backend is compressing FOR the real client, not for us.
# Dropping content-encoding here would have thrown that away for nothing: see
# _proxy() below for why keeping it requires reading the RAW upstream bytes
# rather than httpx's auto-decoded `.aread()`.
_DROP_RESP = {"content-length", "connection", "keep-alive", "transfer-encoding"}

# No interactive docs on the internet-facing edge. (security-audit-2026-07-18 L3)
_DOCS = os.environ.get("WORKSHOP_ENABLE_DOCS", "0").strip().lower() in {"1", "true", "yes"}
app = FastAPI(
    title="Workshop (public edge)",
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)

# This is the process the operator's browser actually talks to (the built SPA
# origin), and until now it shipped every response -- the static bundle
# included -- uncompressed. Reuse the SAME SSE-safe middleware as the backend
# (middleware/compression.py) rather than starlette's stock GZipMiddleware:
# this app also owns the SSE proxy path (_proxy_stream above), whose chunks
# would sit unflushed in a stock GzipFile writer and stall live chat/voice
# streaming. This middleware never attempts incremental compression, so that
# hazard doesn't apply, and it already skips any response that arrives with
# Content-Encoding set -- which is exactly the case where the backend gzipped
# a response and _proxy() forwarded it raw (see test_edge_compression_passthrough.py)
# -- so a backend-compressed body is never gzipped a second time here.
app.add_middleware(SSEAwareGZipMiddleware, minimum_size=500, compresslevel=5)

# SECURITY: use a raw transport, NOT httpx.AsyncClient. An AsyncClient keeps a
# persistent cookie jar: it auto-stores every upstream `Set-Cookie` and replays
# matching cookies on its NEXT request. Because ONE shared client served every
# visitor, the first successful login through the proxy cached that session in
# the shared jar, and every later visitor (cookie-less or not) silently
# inherited it — anyone could load the app fully authenticated with no login.
# httpx.AsyncHTTPTransport has no cookie jar and no per-caller state: each proxied
# request carries ONLY the cookies the visitor themselves forwarded. NEVER
# reintroduce a shared AsyncClient here. (Incident 2026-07-12.)
#
# THE POOL'S IDLE TIMEOUT MUST BE SHORTER THAN THE BACKEND'S, or the proxy hands
# out sockets the backend is closing at that same moment.
#
# httpx defaults `keepalive_expiry` to 5.0s and uvicorn defaults
# `timeout_keep_alive` to 5s. With both ends expiring an idle connection at the
# same instant, a request that arrives on the boundary gets written to a socket
# the backend has already closed, and httpx (`retries=0` by default) surfaces
# `RemoteProtocolError: Server disconnected without sending a response`. The
# backend logs NOTHING, because it never saw the request — which is literally
# what the chat then told the operator: "this message never reached the server".
# Observed on the chat send path and on GET /queue/runner (2026-08-02, 2026-08-09,
# neither near a restart).
#
# Halving the expiry means this proxy never reuses a connection the backend
# could be about to reap. `retries` covers the other end of it: a connection that
# cannot be ESTABLISHED at all, which is the one-second window a backend restart
# opens (32 restarts in the 7 days to 2026-08-11).
_KEEPALIVE_EXPIRY = 2.5
_transport = httpx.AsyncHTTPTransport(
    retries=2,
    limits=httpx.Limits(max_keepalive_connections=20, keepalive_expiry=_KEEPALIVE_EXPIRY),
)

# Transport failures that mean THE BACKEND NEVER PROCESSED THIS REQUEST, so
# sending it again is not sending it twice.
#
#   ConnectError         no connection was established at all.
#   RemoteProtocolError  the connection closed before a single response byte.
#
# Both are the stale-socket and restart races above, and both are what every
# HTTP client (browsers, Go, curl) already retries on a REUSED connection. A
# ReadError or ReadTimeout is deliberately NOT here: those can mean the backend
# read the request and was working on it, and this proxy carries writes.
_RETRYABLE = (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError)


def _build_upstream_request(
    request: Request, backend_path: str, headers: dict, body: bytes, *, stream: bool
) -> httpx.Request:
    # Per-request, no client, no cookie jar. Query params are carried explicitly
    # (multi_items preserves repeated keys). Timeout rides in request.extensions
    # since the transport, unlike a client, has no configured timeout of its own.
    timeout = httpx.Timeout(30.0, read=None) if stream else httpx.Timeout(120.0)
    return httpx.Request(
        request.method,
        BACKEND + backend_path,
        params=list(request.query_params.multi_items()),
        headers=headers,
        content=body,
        extensions={"timeout": timeout.as_dict()},
    )


def _normalize(stripped_path: str) -> str:
    """Collapse `.`/`..`/`//` so a crafted path can't slip past the blocklist
    (e.g. `/finance/../tools`). posixpath.normpath also strips a trailing slash;
    re-add a leading slash if normpath ate it."""
    n = posixpath.normpath(stripped_path)
    if not n.startswith("/"):
        n = "/" + n
    return n


def _is_blocked(stripped_path: str) -> bool:
    p = _normalize(stripped_path)
    app_prefixes, app_exact = _app_edge_blocks()
    if _BLOCK_ALL in app_prefixes:
        return True
    if p in app_exact:
        return True
    for b in (*_BLOCKED_PREFIXES, *app_prefixes):
        if p == b or p.startswith(b + "/"):
            return True
    return False


async def _send_upstream(
    request: Request, backend_path: str, headers: dict, body: bytes, *, stream: bool
) -> httpx.Response:
    """Send one proxied request upstream, retrying once when the failure proves
    the backend never saw it (see `_RETRYABLE`).

    The request is REBUILT for the retry rather than replayed: an httpx.Request
    whose content stream has been consumed cannot be handed to the transport a
    second time."""
    try:
        req = _build_upstream_request(request, backend_path, headers, body, stream=stream)
        return await _transport.handle_async_request(req)
    except _RETRYABLE as e:
        log.info("edge: %s %s failed on a dead connection (%s); retrying once",
                 request.method, backend_path, type(e).__name__)
    req = _build_upstream_request(request, backend_path, headers, body, stream=stream)
    return await _transport.handle_async_request(req)


def _unreachable(method: str, backend_path: str, e: Exception) -> Response:
    """The backend could not be reached. 502, NOT the 500 an uncaught httpx error
    used to produce.

    The status is the whole message here: the client maps 502/503/504 to "the
    server is restarting or unreachable, press Retry in a moment" and a 500 to
    "the server failed to start this turn", which sent the operator looking for a
    bug in a turn that was never begun."""
    log.warning("edge: %s %s could not reach the backend: %s: %s",
                method, backend_path, type(e).__name__, e)
    return JSONResponse({"detail": "the workshop backend is unreachable"}, status_code=502)


async def _proxy(request: Request, backend_path: str) -> Response:
    # backend_path is the full path the backend expects, e.g. "/api/finance/cache".
    stripped = backend_path[4:] if backend_path.startswith("/api") else backend_path
    if _is_blocked(stripped):
        return JSONResponse({"detail": "not found"}, status_code=404)

    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQ}
    body = await request.body()

    # Server-Sent Events (the build-agent turn stream) MUST pass through
    # incrementally. Buffering the whole body — `await _client.request(...)` then
    # `upstream.content` — never returns for a long-lived stream (it blocks until
    # the turn ends or the read timeout fires), so on the public edge the chat
    # never streamed and kept "reconnecting". Stream these through with no read
    # timeout, and disable proxy buffering.
    if backend_path.endswith("/stream") or "text/event-stream" in request.headers.get("accept", ""):
        return await _proxy_stream(request, backend_path, headers, body)

    try:
        upstream = await _send_upstream(request, backend_path, headers, body, stream=False)
    except httpx.HTTPError as e:
        return _unreachable(request.method, backend_path, e)
    try:
        # RAW bytes, not `upstream.aread()`. httpx's `.aread()`/`.content`
        # transparently DECODE a gzip Content-Encoding as part of reading the
        # body — that's convenient for code that wants the decoded JSON, but
        # it means a naive read-then-forward here would silently strip
        # compression the backend put on FOR THE BROWSER, while still
        # reporting content-encoding: gzip on the passed-through headers
        # (mismatched framing) or, previously, dropping that header via
        # _DROP_RESP and serving the now-decoded plaintext body -- either way
        # the public origin's actual client never saw a single compressed
        # byte. `aiter_raw()` yields the untouched wire bytes, so whatever
        # encoding the backend applied rides through to the real client.
        content = b"".join([chunk async for chunk in upstream.aiter_raw()])
    finally:
        await upstream.aclose()
    resp_headers = [(k, v) for k, v in upstream.headers.items() if k.lower() not in _DROP_RESP]
    return Response(content=content, status_code=upstream.status_code, headers=dict(resp_headers))


async def _proxy_stream(request: Request, backend_path: str, headers: dict, body: bytes) -> Response:
    """Reverse-proxy a streaming (SSE) response chunk-by-chunk, keeping the
    upstream connection open for the life of the turn (no read timeout)."""
    try:
        # unread stream; no read-timeout
        upstream = await _send_upstream(request, backend_path, headers, body, stream=True)
    except httpx.HTTPError as e:
        # The turn stream is re-opened by the client on any failure (its own
        # reconnect budget), so this only has to be honest and finite. Uncaught,
        # it was a 500 that the EventSource treated as a dead endpoint.
        return _unreachable(request.method, backend_path, e)
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _DROP_RESP}
    resp_headers["cache-control"] = "no-cache, no-transform"
    resp_headers["x-accel-buffering"] = "no"  # defeat any buffering proxy in front

    async def body_iter():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type", "text/event-stream"),
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_api(path: str, request: Request) -> Response:
    return await _proxy(request, f"/api/{path}")


# --------------------------------------------------------------- websockets
# httpx cannot proxy an Upgrade, so a WebSocket route needs its own path: accept
# here, dial the backend, and pump both directions until either end goes.
#
# Voice mode runs entirely over one of these. Without this route the operator's
# actual origin (the BUILT one on :8002) would fail the upgrade and voice would
# be broken there while working perfectly on the Vite dev origin, which is the
# exact shape of "it works for you and not for me" this edge exists to prevent.
@app.websocket("/api/{path:path}")
async def proxy_ws(websocket: WebSocket, path: str) -> None:
    import websockets
    from websockets.exceptions import ConnectionClosed

    stripped = _normalize("/" + path)
    if _is_blocked(stripped):
        await websocket.close(code=4404)
        return

    # THIS is the origin boundary: the browser's socket terminates here, so this
    # is the only place that knows the host it was actually opened against.
    # Check it here, and forward that host so the backend can check it too.
    # Without `x-forwarded-host` the backend compares the browser's Origin
    # against its own `127.0.0.1:8000` and refuses every real connection.
    host = websocket.headers.get("host", "")
    origin = websocket.headers.get("origin")
    if origin and host and origin.split("//", 1)[-1] != host:
        await websocket.close(code=4403)
        return

    headers = [
        (k, v) for k, v in websocket.headers.items()
        if k.lower() in {"cookie", "authorization", "origin", "user-agent"}
    ]
    if host:
        headers.append(("x-forwarded-host", host))
    upstream_url = f"{_BACKEND_WS}{stripped}"
    if websocket.url.query:
        upstream_url += f"?{websocket.url.query}"

    try:
        upstream = await websockets.connect(
            upstream_url,
            additional_headers=headers,
            open_timeout=10,
            ping_interval=20,
            # Audio frames are small; the default 1 MiB cap is plenty, and a
            # bound keeps a wedged client from growing this process.
            max_size=2 * 1024 * 1024,
        )
    except Exception:                                   # noqa: BLE001
        # The backend refused (bad credential, blocked origin) or is down. Either
        # way there is nothing to proxy, and the client's reconnect handles it.
        await websocket.close(code=1011)
        return

    await websocket.accept()

    async def to_upstream() -> None:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if (data := msg.get("bytes")) is not None:
                await upstream.send(data)
            elif (text := msg.get("text")) is not None:
                await upstream.send(text)

    async def to_client() -> None:
        async for msg in upstream:
            if isinstance(msg, bytes):
                await websocket.send_bytes(msg)
            else:
                await websocket.send_text(msg)

    a = asyncio.create_task(to_upstream())
    b = asyncio.create_task(to_client())
    try:
        done, pending = await asyncio.wait({a, b}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    except (WebSocketDisconnect, ConnectionClosed):
        pass
    finally:
        await upstream.close()
        try:
            await websocket.close()
        except RuntimeError:
            pass                                        # already closed


class _ImmutableStaticFiles(StaticFiles):
    """Serve /assets with a far-future, immutable Cache-Control.

    Safe ONLY because every file Vite emits under dist/assets is content-hashed
    into its filename (e.g. index-BcnhBBb8.js) -- verified against a real build
    (73/73 files matched `name-HASH.ext`, checked 2026-08-14) rather than
    assumed. A changed file gets a new name, so a stale cached copy can never
    shadow a new deploy; there is nothing to invalidate. This must NOT be
    applied to index.html (it isn't served from this mount -- see the `spa`
    route below, which deliberately keeps `Cache-Control: no-cache` on it):
    index.html is the one file whose content does NOT change name when its
    content changes, since it's what points the browser at the hashed
    bundles. Caching it immutably would pin visitors on a dead deploy forever.
    """

    # FileResponse's default chunk_size (64 KiB) sends anything bigger as
    # MULTIPLE `http.response.body` ASGI messages (more_body=True until the
    # last one). SSEAwareGZipMiddleware treats any multi-chunk response it
    # doesn't recognize by content-type as a stream and deliberately leaves it
    # uncompressed (see middleware/compression.py's module docstring) -- which
    # would silently defeat compression for exactly the asset motivating this
    # change: the ~700KB main JS bundle is well over 64KB. Raise chunk_size
    # past the largest asset in a real build (~1.1MB, checked 2026-08-14) so a
    # normal file is read and sent as ONE message, making it eligible for the
    # middleware's single-shot gzip path like any other buffered response.
    _CHUNK_SIZE = 16 * 1024 * 1024

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        if isinstance(response, FileResponse):
            response.chunk_size = self._CHUNK_SIZE
        return response


# Static SPA. Mounted last so /api wins. html=True serves index.html at "/".
if DIST.is_dir():
    app.mount("/assets", _ImmutableStaticFiles(directory=str(DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> Response:
        # Serve a real file if it exists (favicon, manifest, etc.), else the SPA
        # shell so client-side routes (/apps/budget, …) load index.html.
        #
        # SECURITY: `full_path` is attacker-controlled and may contain `..` or
        # percent-encoded `..` (uvicorn does not normalize dot-segments). Resolve
        # the candidate and serve it ONLY if it stays inside DIST — otherwise a
        # request like `/../../../.config/workshop/secrets.env` would
        # exfiltrate secrets off disk. Containment check, then serve.
        if full_path:
            candidate = (DIST / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(_DIST_ROOT):
                return FileResponse(str(candidate))
        # The SPA shell MUST always revalidate. Its <script> points at a
        # content-hashed bundle; if a browser (or Cloudflare) heuristically caches
        # this HTML, a deploy never reaches the client — they keep loading an old
        # bundle. `no-cache` = "revalidate every time"; the ETag makes that a cheap
        # 304 when unchanged. The hashed /assets are immutable by filename, so they
        # stay long-cacheable and are untouched by this. This is the fix for the
        # recurring "you deployed but I still see the old version".
        index = _DIST_ROOT / "index.html"
        if index.is_file():
            return FileResponse(str(index), headers={"Cache-Control": "no-cache"})
        return JSONResponse({"detail": "frontend not built"}, status_code=503)
