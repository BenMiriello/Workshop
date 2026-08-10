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

import os
import posixpath
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

BACKEND = os.environ.get("WORKSHOP_BACKEND", "http://127.0.0.1:8000")
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
    # unauthenticated / Basic ingest webhooks
    "/plaid/webhook",
    "/location/owntracks",
    "/health/ingest",
    "/birds/webhook", "/birds/live/publish",
)

# Paths blocked EXACTLY (not as a prefix), so authed sibling routes still work.
# `/birds/analyze` is the loopback ingest POST; `/birds/analyze/settings`,
# `/birds/analyze/recompute`, etc. are require_user reads the SPA legitimately uses.
_BLOCKED_EXACT = frozenset({"/birds/analyze"})

# Hop-by-hop headers we must not forward verbatim.
_DROP_REQ = {"host", "content-length", "connection", "keep-alive", "transfer-encoding"}
_DROP_RESP = {"content-length", "connection", "keep-alive", "transfer-encoding", "content-encoding"}

# No interactive docs on the internet-facing edge. (security-audit-2026-07-18 L3)
_DOCS = os.environ.get("WORKSHOP_ENABLE_DOCS", "0").strip().lower() in {"1", "true", "yes"}
app = FastAPI(
    title="Workshop (public edge)",
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)

# SECURITY: use a raw transport, NOT httpx.AsyncClient. An AsyncClient keeps a
# persistent cookie jar: it auto-stores every upstream `Set-Cookie` and replays
# matching cookies on its NEXT request. Because ONE shared client served every
# visitor, the first successful login through the proxy cached that session in
# the shared jar, and every later visitor (cookie-less or not) silently
# inherited it — anyone could load the app fully authenticated with no login.
# httpx.AsyncHTTPTransport has no cookie jar and no per-caller state: each proxied
# request carries ONLY the cookies the visitor themselves forwarded. NEVER
# reintroduce a shared AsyncClient here. (Incident 2026-07-12.)
_transport = httpx.AsyncHTTPTransport()


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
    if p in _BLOCKED_EXACT:
        return True
    return any(p == b or p.startswith(b + "/") for b in _BLOCKED_PREFIXES)


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

    req = _build_upstream_request(request, backend_path, headers, body, stream=False)
    upstream = await _transport.handle_async_request(req)
    try:
        content = await upstream.aread()
    finally:
        await upstream.aclose()
    resp_headers = [(k, v) for k, v in upstream.headers.items() if k.lower() not in _DROP_RESP]
    return Response(content=content, status_code=upstream.status_code, headers=dict(resp_headers))


async def _proxy_stream(request: Request, backend_path: str, headers: dict, body: bytes) -> Response:
    """Reverse-proxy a streaming (SSE) response chunk-by-chunk, keeping the
    upstream connection open for the life of the turn (no read timeout)."""
    req = _build_upstream_request(request, backend_path, headers, body, stream=True)
    upstream = await _transport.handle_async_request(req)  # unread stream; no read-timeout
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


# Static SPA. Mounted last so /api wins. html=True serves index.html at "/".
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

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
