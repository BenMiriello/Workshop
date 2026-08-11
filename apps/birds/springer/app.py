"""Springer Birds — standalone hardened app (public face, behind cloudflared).

A SEPARATE FastAPI process from the Workshop backend, on 127.0.0.1:8001, reached
only through the Cloudflare tunnel at its own public hostname. It imports ONLY the
bird data layer + auth — no finance/agents/plaid/chat code or creds — so even a
full compromise here can read nothing but bird data.

It serves the REAL birds app (the same React pages as /apps/birds, built as a
birds-only bundle with NO Workshop wrapper) behind a login, and mounts the real
birds API at /api/birds/* gated by the session.

Security model:
  - Anonymous visitors get ONLY the login page; the API answers nothing without a
    valid session, and the SPA is useless until /api/auth/me succeeds.
  - require_session is async so it runs in the request's context and pins the
    per-request visibility viewer (birds_analyze.set_request_viewer) BEFORE the
    endpoint runs — a client cannot widen it with a query arg.
  - /webhook (loopback ingestion) is blocked here.
  - Session cookie is HttpOnly + SameSite=Strict (+ Secure in production).
  - Accounts are admin-provisioned (apps/birds/springer/account.py); no open signup.

NOTE (pre-friend gate): per-endpoint read-scoping + per-device write-authorization
must be completed before a SECOND account is provisioned. Today only the operator
(who owns all data) has a login, so unscoped reads expose nothing they don't own.

Run:
    cd ~/workshop/backend
    nohup .venv/bin/uvicorn springer_app:app --host 127.0.0.1 --port 8001 \
        >> /tmp/springer-app.log 2>&1 &
"""
from __future__ import annotations

# This process lives with the birds app (apps/birds/springer/) rather than in
# backend/, because it is the app's public face, not platform code — the same
# reason the analyzer and the Pi capture live here. It still runs against the
# platform's own modules, so the backend package root goes on sys.path first.
import sys as _sys
from pathlib import Path as _Path

_BACKEND = _Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND) not in _sys.path:
    _sys.path.insert(0, str(_BACKEND))
_HERE = str(_Path(__file__).resolve().parent)
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

import os
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from routes import birds as birds_routes
from services import birds_analyze, paths

import auth as springer_auth

COOKIE = "springer_session"
SECURE_COOKIES = os.environ.get("SPRINGER_SECURE_COOKIES", "1") != "0"
SESSION_MAX_AGE = springer_auth.SESSION_TTL_DAYS * 24 * 3600
# The birds-only production bundle (built by vite.springer.config.ts).
DIST = paths.env_path("SPRINGER_DIST", "frontend", "dist-springer", base="root")
INDEX = DIST / "springer.html"

app = FastAPI(title="Springer Birds", docs_url=None, redoc_url=None, openapi_url=None)


# --- auth plumbing ---------------------------------------------------------

async def require_session(springer_session: str | None = Cookie(default=None)) -> dict:
    """Gate + scope. Async so the contextvar set here propagates to the (async)
    birds endpoints in the same request context."""
    user = springer_auth.session_user(springer_session)
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    birds_analyze.set_request_viewer(user["id"])
    return user


class BlockWebhook(BaseHTTPMiddleware):
    """The /birds/webhook ingestion path is loopback-only (BirdNET-Go); it must
    never be reachable through the public tunnel."""
    async def dispatch(self, request: Request, call_next):
        if request.url.path.rstrip("/").endswith("/webhook"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        response = await call_next(request)
        # iOS Safari gates navigator.geolocation behind this policy.
        response.headers.setdefault("Permissions-Policy", "geolocation=(self)")
        return response


app.add_middleware(BlockWebhook)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/api/auth/login")
def login(request: Request, response: Response,
          email: str = Form(...), password: str = Form(...)) -> dict:
    springer_auth.purge_expired()
    res = springer_auth.login(email, password, user_agent=request.headers.get("user-agent", ""))
    if not res:
        raise HTTPException(status_code=401, detail="invalid email or password")
    response.set_cookie(COOKIE, res["token"], max_age=SESSION_MAX_AGE, httponly=True,
                        secure=SECURE_COOKIES, samesite="strict", path="/")
    return {"ok": True, "user": res["user"]}


@app.post("/api/auth/logout")
def logout(response: Response, springer_session: str | None = Cookie(default=None)) -> dict:
    springer_auth.logout(springer_session)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: dict = Depends(require_session)) -> dict:
    return {"user": user}


@app.get("/api/auth/invite/{token}")
def invite_lookup(token: str) -> dict:
    info = springer_auth.invite_info(token)
    if not info:
        raise HTTPException(status_code=404, detail="invalid or expired invite")
    return info  # {email, name}


@app.post("/api/auth/invite/{token}")
def invite_redeem(token: str, request: Request, response: Response,
                  password: str = Form(...), name: str = Form(None)) -> dict:
    res = springer_auth.redeem_invite(token, password, name=name,
                                      user_agent=request.headers.get("user-agent", ""))
    if not res:
        raise HTTPException(status_code=400, detail="invalid or expired invite")
    response.set_cookie(COOKIE, res["token"], max_age=SESSION_MAX_AGE, httponly=True,
                        secure=SECURE_COOKIES, samesite="strict", path="/")
    return {"ok": True, "user": res["user"]}


# The REAL birds API, gated by the session (which pins the visibility viewer).
# Same router the Workshop backend uses, mounted under /api so the bundle's
# `/api/birds/*` calls resolve here. /webhook is blocked by the middleware above.
app.include_router(birds_routes.router, prefix="/api", dependencies=[Depends(require_session)])


# --- static: the birds-only SPA bundle. index is served to anyone (it is inert
# until /api/auth/me succeeds); assets + client routes resolve to the SPA. ----

@app.get("/")
def index() -> FileResponse:
    return FileResponse(INDEX)


@app.get("/{path:path}")
def spa(path: str) -> FileResponse:
    f = (DIST / path).resolve()
    if f.is_file() and DIST.resolve() in f.parents:
        return FileResponse(f)
    return FileResponse(INDEX)  # SPA fallback for client-side routes
