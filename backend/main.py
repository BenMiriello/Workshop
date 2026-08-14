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
from routes import agents, projects, base_apps, relay, services, events, jobs, backlog, auto_pr, push, redaction, tools, autonomy, chat, worktrees, auth, voice, voice_stream, knowledge, skills, grants, usage, tasks_all, notes_all, docs_index, inbox, trackers
from routes import mcp as mcp_routes
from routes import stores as stores_routes
from routes import deploys as deploys_routes
from services import app_registry
import asyncio
import datetime as _dt
import logging
import os

log = logging.getLogger("workshop.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    # App platform: free the ports of processes that have died. Apps themselves are
    # kept alive by docker's restart policy, not by us.
    from services import port_registry
    app.state._bg_port_reconcile = asyncio.create_task(port_registry.reconcile_loop())
    # Deploy watcher: auto-deploy autonomous Deploys when their source branch
    # moves forward at the git remote. Respects both autonomy overrides.
    from services import deploy_watcher
    app.state._bg_deploy_watcher = asyncio.create_task(deploy_watcher.watcher_loop())
    # Job liveness: close Jobs left `running` by a process that died. Its two
    # shell callers never fire (the dispatcher exits early while autonomy is
    # engaged, and liveness_watchdog.sh is not in crontab), so a crashed run used
    # to read `running` forever. The backend outlives every run, so it reaps.
    from services import jobs as jobs_svc
    app.state._bg_job_reconcile = asyncio.create_task(jobs_svc.reconcile_loop())
    # Crash/restart resilience: re-attach to any build turn that was still running
    # when the backend went down. Detached claude subprocesses keep working across
    # a restart; this tails their on-disk journals to completion (or finalizes a
    # dead one) so a chat is never left stuck mid-turn.
    # Chat threads moved from one JSON document each to an append-only event log
    # (docs/chat-rebuild-spec-2026-08-09.md). One shot, idempotent, non-
    # destructive: a thread already on the log has no legacy file left to find,
    # and a migration that fails leaves the original where it was.
    try:
        from services import chat_log
        chat_log.migrate_all_owners()
    except Exception as e:
        log.error("chat log migration failed at startup: %s", e)
    try:
        from services import build_streaming
        build_streaming.reattach_running_turns()
        # Second pass, after the live journals are re-registered: any thread
        # still pointing at a run nobody is driving is closed out as lost, so a
        # chat is never left waiting on something that will never answer.
        build_streaming.reconcile_threads()
    except Exception:
        pass
    try:
        # Cache each thread's resolved context cut. Threads cleared before the
        # cache existed carry a boundary and no count, and the sync endpoint
        # answers from meta alone — so without this their divider would vanish
        # while the prompt kept honouring the cut. One log read per thread, once.
        from services import chat_store as _cs
        n = _cs.backfill_cuts()
        if n:
            log.info("resolved the context cut for %d thread(s)", n)
    except Exception as e:
        log.error("context-cut backfill failed at startup: %s", e)
    try:
        # ...and then send anything that was waiting behind a turn this restart
        # killed. The queue is on the thread, so it survived; nothing else would
        # ever notice it, because the browser that queued those messages may
        # never open that chat again. Import here, and after the reconcile, so a
        # thread whose dead run has just been closed out reads as idle.
        # routes.chat registers the starter, so import it first.
        from routes import chat as _chat_routes  # noqa: F401
        from services import chat_drain
        app.state._bg_queue_drain = asyncio.create_task(chat_drain.drain_all())
    except Exception as e:
        log.error("queued chat messages could not be drained at startup: %s", e)
    yield
    for t in [*getattr(app.state, "_app_bg", []),
              getattr(app.state, "_bg_port_reconcile", None),
              getattr(app.state, "_bg_deploy_watcher", None),
              getattr(app.state, "_bg_job_reconcile", None),
              getattr(app.state, "_bg_queue_drain", None)]:
        if t:
            t.cancel()


# Interactive API docs (/docs, /redoc, /openapi.json) disclose the full route map.
# Off by default; opt in with WORKSHOP_ENABLE_DOCS=1 for local dev.
# (security-audit-2026-07-18 L3)
_DOCS = os.environ.get("WORKSHOP_ENABLE_DOCS", "0").strip().lower() in {"1", "true", "yes"}
app = FastAPI(
    title="Workshop",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)


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
app.include_router(tasks_all.router, dependencies=_OPERATOR)  # cross-scope task feed (/tasks)
app.include_router(trackers.router, dependencies=_OPERATOR)  # external tracker connection + triage (/trackers)
app.include_router(notes_all.router, dependencies=_OPERATOR)  # cross-scope notes feed (/notes)
app.include_router(inbox.router, dependencies=_OPERATOR)      # agent inbox (/inbox)
app.include_router(docs_index.router, dependencies=_OPERATOR)  # read-only docs index (/docs)
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
# The Store catalog + the one door onto a Store's contents (/stores). The operator
# is served ungated here; the gate is on a caller that says it is an agent.
app.include_router(stores_routes.router, dependencies=_OPERATOR)
app.include_router(backlog.router, dependencies=_OPERATOR)
# Deploys — per-project target bindings + provider catalog + run kicker. Both
# cross-project (/deploys) and per-project (/projects/{slug}/deploys/...) routes
# live in the same router.
app.include_router(deploys_routes.router, dependencies=_OPERATOR)
app.include_router(autonomy.router, dependencies=_OPERATOR)
app.include_router(usage.router, dependencies=_OPERATOR)  # rolling-window token usage (/usage)
app.include_router(redaction.router, dependencies=_OPERATOR)
app.include_router(redaction.restore_router, dependencies=_OPERATOR)
# Settings -> MCP: which MCP servers exist, whether they connect, what they
# cost. Machine-wide facts, so it hangs off settings rather than a chat.
app.include_router(mcp_routes.router, dependencies=_OPERATOR)

# Platform per-user primitives — owner-scoped to the authenticated user. These
# are the shell (chat, voice, push), NOT apps: apps build on them, so they mount
# unconditionally.
app.include_router(chat.router, prefix="/chat", tags=["chat"], dependencies=_USER)
# Voice mode — local STT/TTS for the chat surface, per-owner prefs.
app.include_router(voice.router, tags=["voice"], dependencies=_USER)
# The voice socket authenticates inside the handler: _USER is typed for an
# HTTP Request and a WebSocket is not one. Same auth_store, same fail-closed
# rule, plus an Origin check (a WebSocket is not covered by the same-origin
# policy but does carry cookies).
app.include_router(voice_stream.router, tags=["voice"])
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
# another instance's hostname. An install sets WORKSHOP_CANONICAL_ORIGIN on its
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


def _backend_source_id() -> str:
    """The id of the source THIS process is running, computed once at import.

    scripts/apply.sh compares it against a fresh `scripts/backend_source_id.sh`
    to decide whether a restart is owed. Shelling out to that script rather than
    reimplementing the hash here is deliberate: two definitions of "what counts
    as backend source" would drift the moment one of them learned about a new
    input, and drift between the checker and the thing it checks is worse than
    no checker at all (the argument is spelled out in ui_source_id.sh).

    Measured at 34ms over 354 files, paid once at startup. Any failure yields
    "unknown", which apply.sh reports as undecidable rather than treating as a
    match -- never silently as "in sync".
    """
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "backend_source_id.sh"
    try:
        out = subprocess.run(
            ["bash", str(script)], capture_output=True, text=True, timeout=30
        )
        return (out.stdout or "").strip() or "unknown"
    except Exception as exc:  # pragma: no cover - diagnostic path
        log.warning("backend source id unavailable: %s", exc)
        return "unknown"


_SOURCE_ID = _backend_source_id()
_STARTED_AT = _dt.datetime.now(_dt.timezone.utc).isoformat()


@app.get("/healthz")
async def healthz():
    """Liveness, plus what code is answering.

    `ok` is unchanged for existing callers. `source_id` is what lets a restart be
    VERIFIED instead of assumed: before this, nothing the backend served said
    which version it was, so "did my change land" could only be guessed at from
    uptime -- and guessing wrong is how backend work has been reported shipped
    while the old code kept serving.
    """
    return {
        "ok": True,
        "source_id": _SOURCE_ID,
        "started_at": _STARTED_AT,
        "pid": os.getpid(),
    }
