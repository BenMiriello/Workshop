from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from deps_auth import require_loopback, require_internal_caller
from deps_user import (
    require_user,
    require_operator,
)

# Platform-core routers only — the shell. App routers (finance/budget/commute/
# location/birds/health/…) are NOT imported here; they mount from the installed-
# app catalog (services/app_registry) so adding/removing an app never edits this
# file. See docs/apps-are-pluggable.md.
from routes import agents, projects, base_apps, relay, services, events, jobs, backlog, auto_pr, push, redaction, tools, autonomy, chat, worktrees, auth, voice, knowledge, skills, grants
from services.supervisor import AgentSupervisor
from services import global_memory, app_registry, installed_apps
from letta_legacy_client import LettaClient
import asyncio
import logging
import os

log = logging.getLogger("workshop.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.letta = LettaClient()
    app.state.supervisor = AgentSupervisor()
    # Ensure Workshop global memory block exists in Letta (and value matches disk).
    # Non-fatal on failure — agents will still create; block sync can be retried.
    try:
        await global_memory.ensure_block()
    except Exception:
        pass
    await app.state.supervisor.start()
    # Installed apps: run each installed app's startup hooks + background loops
    # (finance prewarm/categorize, birds env-refresh/atlas). A fresh instance with
    # no apps installed starts none of these. Adding an app never edits this file.
    app.state._app_bg = []
    for spec in app_registry.installed_specs():
        for init in spec.on_startup:
            try:
                await init(app)
            except Exception as e:
                log.warning("app %s startup hook failed: %s", spec.id, e)
        for loop in spec.background:
            app.state._app_bg.append(asyncio.create_task(loop()))
    # App platform: keep desired-running multi-service apps alive (restart crashed
    # services with backoff) and reconcile the port registry each pass.
    from services import app_monitor
    app.state._bg_app_monitor = asyncio.create_task(app_monitor.monitor_loop())
    # Crash/restart resilience: re-attach to any build turn that was still running
    # when the backend went down. Detached claude subprocesses keep working across
    # a restart; this tails their on-disk journals to completion (or finalizes a
    # dead one) so a chat is never left stuck mid-turn.
    try:
        from services import build_streaming
        build_streaming.reattach_running_turns()
    except Exception:
        pass
    yield
    for t in [*getattr(app.state, "_app_bg", []), getattr(app.state, "_bg_app_monitor", None)]:
        if t:
            t.cancel()
    await app.state.supervisor.stop()
    await app.state.letta.aclose()


app = FastAPI(title="Workshop", lifespan=lifespan)


# Tailscale Serve mounts the backend directly at :443/, so paths from the
# launcher/Owntracks come in as /location/... — but the Workshop and Commute
# vite proxies use /api/... and strip that prefix. Accept both so callers
# don't have to care which entry point they used.
@app.middleware("http")
async def strip_api_prefix(request, call_next):
    path = request.scope.get("path", "")
    if path == "/api" or path.startswith("/api/"):
        new_path = path[4:] or "/"
        request.scope["path"] = new_path
        request.scope["raw_path"] = new_path.encode("ascii", errors="ignore")
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:5174", "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gating model (docs/auth-redesign/): identity, never network position.
#   PLATFORM control      -> require_operator (the admin role)
#   per-user personal data -> require_user (cookie / device token), owner-scoped
#   single-dataset domains -> require_domain_owner("finance"|"commute") (owned data)
#   low-sensitivity + ingest -> require_user_or_service(scope)
#   internal machine infra  -> require_loopback (no personal data; not public)
#   ingest webhooks         -> require_service(scope) / Basic bound to an owner
_OPERATOR = [Depends(require_operator)]
_USER = [Depends(require_user)]
_LOOPBACK = [Depends(require_loopback)]

# Platform / infra control surface — operator (admin) only.
app.include_router(jobs.router, dependencies=_OPERATOR)  # top-level Job feed (/jobs)
app.include_router(agents.router, prefix="/agents", tags=["agents"], dependencies=_OPERATOR)
app.include_router(projects.router, prefix="/projects", tags=["projects"], dependencies=_OPERATOR)
app.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"], dependencies=_OPERATOR)
app.include_router(skills.router, prefix="/skills", tags=["skills"], dependencies=_OPERATOR)
app.include_router(worktrees.router, prefix="/worktrees", tags=["worktrees"], dependencies=_OPERATOR)
app.include_router(base_apps.router, prefix="/base-apps", tags=["base-apps"], dependencies=_OPERATOR)
# Grants + Stores (Phase 4). /base-apps/grants stays as a thin alias in base_apps
# until Phase 5; this is the real grants router (/grants, /grants/mint,
# /grants/revoke, /stores).
app.include_router(grants.router, dependencies=_OPERATOR)
app.include_router(backlog.router, dependencies=_OPERATOR)
app.include_router(autonomy.router, dependencies=_OPERATOR)
app.include_router(redaction.router, dependencies=_OPERATOR)
app.include_router(redaction.restore_router, dependencies=_OPERATOR)

# Platform per-user primitives — owner-scoped to the authenticated user. These
# are the shell (chat, voice, push), NOT apps: apps build on them, so they mount
# unconditionally.
app.include_router(chat.router, prefix="/chat", tags=["chat"], dependencies=_USER)
# Voice mode — local STT/TTS for the chat surface, per-owner prefs.
app.include_router(voice.router, tags=["voice"], dependencies=_USER)
app.include_router(push.router, dependencies=_USER)

# Installed apps — finance/budget/commute/location/birds/health/… mount from the
# built-in catalog (services/app_registry), gated by this instance's installed-app
# registry. A fresh clone installs nothing and mounts none of these; each app
# carries its own auth (declared in its AppSpec). Adding/removing an app is an
# installed-apps edit, never a change to this file. See docs/apps-are-pluggable.md.
app_registry.ensure_identity_manifests()
for _spec in app_registry.installed_specs():
    for _m in _spec.mounts:
        app.include_router(
            _m.router,
            prefix=_m.prefix,
            tags=list(_m.tags) or None,
            dependencies=list(_m.deps),
        )

# Internal machine-to-machine infra — loopback only, no personal data, not public.
app.include_router(relay.router, dependencies=_LOOPBACK)
app.include_router(services.router, dependencies=_LOOPBACK)
app.include_router(events.router, dependencies=_LOOPBACK)
app.include_router(auto_pr.router, dependencies=_LOOPBACK)
# Letta tool sandbox runs off-loopback (Docker bridge); internal-only, no
# personal data, never proxied to the public origin.
app.include_router(tools.router, dependencies=[Depends(require_internal_caller)])

# Public auth entry (register/login/logout/me) — no gate; it establishes identity.
app.include_router(auth.router)


# Browser-facing origin for the Workshop SPA (e.g. a Tailscale Serve HTTPS URL).
# Per-deployment; empty by default so a fresh clone never bounces callers to
# another instance's hostname. clawd-a sets WORKSHOP_CANONICAL_ORIGIN on its
# service to preserve the existing tailnet redirect.
CANONICAL_ORIGIN = os.environ.get("WORKSHOP_CANONICAL_ORIGIN", "").rstrip("/")


@app.get("/")
async def root_redirect():
    """The backend can also be reached at a site root (e.g. mounted at :443/ by
    Tailscale Serve so /api/* works for external callers). Bounce the bare root
    to the browser-facing Workshop SPA origin when one is configured
    (WORKSHOP_CANONICAL_ORIGIN); with none set there is nothing to redirect to,
    so just report liveness."""
    if CANONICAL_ORIGIN:
        return RedirectResponse(url=CANONICAL_ORIGIN + "/", status_code=302)
    return {"ok": True, "service": "workshop-backend"}


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/agents/supervisor/status")
async def supervisor_status(request: Request, user: dict = Depends(require_operator)):
    # Operator-gated (was unauthed): identity, not origin, controls access — so it
    # is safe on the public origin and no edge block is needed.
    return request.app.state.supervisor.status()
