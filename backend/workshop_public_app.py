"""Public edge app for workshop.benmiriello.com (port 8002).

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
from fastapi.responses import FileResponse, JSONResponse
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
_BLOCKED_PREFIXES = (
    # internal / loopback-trusted machine endpoints
    "/tools", "/relay", "/services", "/events", "/auto",
    # operator-only platform control plane (code-exec-adjacent; never public)
    "/agents", "/projects", "/worktrees", "/base-apps", "/backlog",
    "/autonomy", "/redaction", "/agent-runs", "/agent_runs",
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

app = FastAPI(title="Workshop (public edge)")
_client = httpx.AsyncClient(base_url=BACKEND, timeout=120.0)


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
    upstream = await _client.request(
        request.method, backend_path, headers=headers, content=body,
        params=request.query_params,
    )
    resp_headers = [(k, v) for k, v in upstream.headers.items() if k.lower() not in _DROP_RESP]
    return Response(content=upstream.content, status_code=upstream.status_code, headers=dict(resp_headers))


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
        # request like `/../../../home/clawd-a/.config/workshop/secrets.env` would
        # exfiltrate secrets off disk. Containment check, then serve.
        if full_path:
            candidate = (DIST / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(_DIST_ROOT):
                return FileResponse(str(candidate))
        index = _DIST_ROOT / "index.html"
        if index.is_file():
            return FileResponse(str(index))
        return JSONResponse({"detail": "frontend not built"}, status_code=503)
