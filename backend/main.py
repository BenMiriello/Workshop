from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from deps_auth import require_loopback
from deps_user import (
    require_user,
    require_operator,
)
from middleware.compression import SSEAwareGZipMiddleware

# Platform-core routers only — the shell. App routers (finance/budget/commute/
# location/birds/health/…) are NOT imported here; they mount from the installed-
# app catalog (services/app_registry) so adding/removing an app never edits this
# file. See docs/apps-are-pluggable.md.
from routes import agents, projects, base_apps, relay, services, events, jobs, push, autonomy, chat, worktrees, auth, voice, voice_stream, knowledge, grants, usage, tasks_all, notes_all, docs_index, inbox, trackers, refs as refs_routes, memory_policy as memory_policy_routes
from routes import mcp as mcp_routes
from routes import stores as stores_routes
from routes import deploys as deploys_routes
from routes import apps as apps_routes
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
    # Job liveness: close Jobs left `running` by a process that died. Its only
    # shell caller never fires (the dispatcher exits early while autonomy is
    # engaged), so a crashed run used to read `running` forever. The backend
    # outlives every run, so it reaps.
    from services import jobs as jobs_svc
    app.state._bg_job_reconcile = asyncio.create_task(jobs_svc.reconcile_loop())
    # Background work loop: make the worker MECHANISM follow the execution
    # INTENT. Arming a scope (services/scope_execution) is the only control;
    # this ensures a resident queue runner exists for every armed scope and
    # brings one back after a reboot, an OOM kill or a stray kill. Nothing runs
    # for a scope left at its reactive default, and the global autonomy override
    # sits above it.
    from services import work_driver
    app.state._bg_work_driver = asyncio.create_task(work_driver.reconcile_loop())
    # Fire-and-continue: a finished background run leaves a continuation seed on
    # the originating thread's queue (services/run_report -> chat_drain), written
    # from the detached worker process that cannot reach this event loop. This is
    # the backend side that notices the seed and starts the thread's own next
    # turn once it is idle, so a chat acts on a run's result without the operator
    # having to prompt it. Only threads holding a seed are touched.
    from services import chat_drain as _chat_drain
    app.state._bg_chat_continuation = asyncio.create_task(_chat_drain.continuation_loop())
    # Dedup vectors: embed the open backlog so filing a ticket does not. `decide`
    # compares a new ticket against every open one, and the embedder is one HTTP
    # call per text, so a cold cache made the File ticket button wait 17.5s
    # (measured 2026-08-16). The cache is a file and survives a restart, so this
    # normally finds everything present and returns immediately.
    from services import task_dedup as _task_dedup
    app.state._bg_dedup_warm = asyncio.create_task(
        asyncio.to_thread(_task_dedup.warm_vectors))
    # Semantic index: keep it fresh from HERE, the long-lived process, and
    # declare this process the owner of that job. The rebuild used to start in
    # whichever process called search() first, which is nearly always a chat
    # turn's own MCP server child: an 8-minute multi-GB batch job inside a
    # turn's process group, charged to that turn. On 2026-08-22 a turn was
    # killed at the 6 GB ceiling for calling search on a cold index. A search
    # now degrades to what is on disk while this loop catches it up.
    from services import semantic_index as _semantic_index
    app.state._bg_index_maintenance = asyncio.create_task(
        _semantic_index.maintenance_loop())
    # Crash/restart resilience: re-attach to any build turn that was still running
    # when the backend went down. Detached claude subprocesses keep working across
    # a restart; this tails their on-disk journals to completion (or finalizes a
    # dead one) so a chat is never left stuck mid-turn.
    #
    # The chat-log migration (one JSON document per thread -> an append-only
    # event log, docs/chat-rebuild-spec-2026-08-09.md) USED to run here on every
    # boot. It is finished: 58 threads migrated, zero legacy documents left on
    # any owner, measured 2026-08-17. The per-owner fold-in stays where it can
    # still matter (chat_store.list_threads / get_thread), so a straggler is
    # still absorbed on first touch without a startup pass over every owner.
    try:
        # Layer 0 filesystem restructure: move the four in-source state files
        # (grants/resource-grants/apps/store-policies.json) under the untracked
        # runtime root, once, before anything reads them. Readers already resolve
        # through wsplatform.paths with a fallback to the old location, so this is
        # the physical move that makes the fallback unnecessary. Idempotent (a
        # file already relocated is skipped) and logs a conservation line per
        # move. See services/state_file_relocate.
        from services import state_file_relocate
        relocated = state_file_relocate.run_once()
        if relocated:
            log.info("state files relocated: %s", "; ".join(relocated))
    except Exception as e:
        log.error("state file relocation failed at startup: %s", e)
    try:
        # The chat store's on-disk `run` vocabulary becomes `turn`, once. This
        # runs BEFORE anything reads a thread, because the keys and the code
        # that fences on them have to change together: a turn in flight under
        # the previous build would stop recognising its own claim. Guarded by a
        # marker file, so later boots cost one stat. See services/chat_log_turn_rename.
        from services import chat_log_turn_rename
        renamed = chat_log_turn_rename.run_once()
        if renamed:
            log.info("chat turn rename: %s", renamed)
    except Exception as e:
        log.error("chat turn rename failed at startup: %s", e)
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
    # FIRST, before anything is cancelled: say that this is a restart, not an
    # abandonment. asyncio.run() cancels every surviving task on the way out,
    # including each turn's drive loop, and that cancellation used to be handled
    # by killing the turn's detached agent process and committing "this turn
    # ended early". A restart is meant to be a pause -- the child is detached and
    # the unit sets KillMode=process precisely so it survives -- and this flag is
    # what keeps that promise from being broken from inside.
    try:
        from services import build_streaming as _bs
        _bs.begin_shutdown()
    except Exception:
        pass
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

# The HTTP contract version, carried in the OpenAPI schema's `info.version`.
# Set because the schema is now a build artifact: `scripts/gen_openapi.py`
# emits it and `frontend/src/api/schema.gen.ts` is generated from it, and
# shipping FastAPI's placeholder "0.1.0" would have that artifact state a
# version this app never chose.
#
# Hand-bumped, deliberately, and deliberately NOT the backend source id.
# `_SOURCE_ID` below already answers "what code is answering" at /healthz and
# changes on every commit; putting that here would rewrite the emitted schema
# on every commit while saying nothing about whether the CONTRACT changed, and
# the whole point of the generated types being committed is that a diff in
# them means something. Bump the minor on an additive change, the major on a
# breaking one.
API_VERSION = "1.0.0"

app = FastAPI(
    title="Workshop",
    version=API_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)


# ADR 0023 clause 3: one error model, from one place, covering all 348
# existing `raise HTTPException` sites with zero edits to any of them. The
# wire shape stays `{"detail": ...}` -- what FastAPI already produces -- and
# `detail` becomes `{"code", "message", "hint"}` here. These are the first
# exception handlers this app has ever registered.
#
# `starlette.exceptions.HTTPException`, not `fastapi.HTTPException`: every
# `fastapi.HTTPException` IS one (it's a subclass), and catching the
# Starlette base also catches the ones the framework raises itself (an
# unmatched route's 404, a wrong-method 405) rather than only the ones this
# codebase wrote by hand.
#
# This does not touch SSE or WebSocket framing. A `StreamingResponse` that
# has already started sending bytes cannot be intercepted by an exception
# handler at all (Starlette can no longer replace a response once
# `http.response.start` has been sent) — this only ever fires for an
# HTTPException raised before a streaming response begins, e.g. an auth
# check, which already produced a plain JSON error and continues to.
# WebSocket routes don't raise HTTPException in the first place (there's no
# HTTP response to carry one); a `WebSocketException` is a different type
# entirely and is untouched by this handler.
from wsplatform.http import error_body, validation_error_body  # noqa: E402


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        {"detail": error_body(exc.status_code, exc.detail)},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        {"detail": validation_error_body(exc.errors())},
        status_code=422,
    )


# Response compression. SSE-safe by construction -- see middleware/compression.py
# for why the stock starlette GZipMiddleware is unsafe for streaming responses
# and must never be swapped in here. minimum_size=500B: don't bother on tiny
# bodies. compresslevel=5: level 9 measured 87ms on a ~4.9MB payload for
# negligible extra ratio; 5 is the right cost/benefit tradeoff.
#
# THE POSITION OF THIS LINE IS LOAD-BEARING. `add_middleware` inserts at the
# front of the stack, so the LAST one registered ends up OUTERMOST. This must
# be registered FIRST, which makes it INNERMOST: closest to the router, where a
# JSONResponse still arrives as one complete `http.response.body` message.
#
# `strip_api_prefix` below is a BaseHTTPMiddleware, and BaseHTTPMiddleware
# re-emits every response it handles as a chunked stream (`more_body=True`).
# This middleware deliberately refuses to compress a chunked body, because it
# has no per-chunk flush and stalling SSE is the worst failure available here.
# So with compression registered LAST it sat outside BaseHTTPMiddleware, saw
# only chunks, and correctly compressed nothing at all: measured 2026-08-14,
# a 4.83MB events response went out uncompressed with the middleware active
# and apparently wired. Registering it here fixes that. If you move it below
# any BaseHTTPMiddleware (including any `@app.middleware("http")` function),
# compression silently switches itself off again and nothing fails loudly.
app.add_middleware(SSEAwareGZipMiddleware, minimum_size=500, compresslevel=5)


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
app.include_router(apps_routes.router, dependencies=_OPERATOR)  # the app entity + its collections (/apps)
app.include_router(inbox.router, dependencies=_OPERATOR)      # agent inbox (/inbox)
app.include_router(docs_index.router, dependencies=_OPERATOR)  # docs index, read + write (/docs)
app.include_router(refs_routes.router, dependencies=_OPERATOR)  # ref resolve + backlinks (/refs)
app.include_router(memory_policy_routes.router, dependencies=_OPERATOR)  # what other chats see (/memory-policy)
app.include_router(agents.router, prefix="/agents", tags=["agents"], dependencies=_OPERATOR)
app.include_router(projects.router, prefix="/projects", tags=["projects"], dependencies=_OPERATOR)
app.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"], dependencies=_OPERATOR)
app.include_router(worktrees.router, prefix="/worktrees", tags=["worktrees"], dependencies=_OPERATOR)
app.include_router(base_apps.router, prefix="/base-apps", tags=["base-apps"], dependencies=_OPERATOR)
# Grants + Stores (Phase 4). /base-apps/grants stays as a thin alias in base_apps
# until Phase 5; this is the real grants router (/grants, /grants/mint,
# /grants/revoke, /stores).
app.include_router(grants.router, dependencies=_OPERATOR)
# The Store catalog + the one door onto a Store's contents (/stores). The operator
# is served ungated here; the gate is on a caller that says it is an agent.
app.include_router(stores_routes.router, dependencies=_OPERATOR)
# Deploys — per-project target bindings + provider catalog + run kicker. Both
# cross-project (/deploys) and per-project (/projects/{slug}/deploys/...) routes
# live in the same router.
app.include_router(deploys_routes.router, dependencies=_OPERATOR)
app.include_router(autonomy.router, dependencies=_OPERATOR)
app.include_router(usage.router, dependencies=_OPERATOR)  # rolling-window token usage (/usage)
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
# OPERATOR-AUTHED, not loopback-gated (changed 2026-08-20). This router is a
# read-only health prober: it has one GET, no ingest, and no unauthenticated
# surface. Gating it on network position was the anti-pattern the public edge's
# own comment says was removed for every other platform router, and it had a
# measured cost: /services is on the edge blocklist, so the endpoint 404'd on
# the origin the operator actually uses, which silently broke the Apps
# launcher's per-app status AND made Commute's settings say the 511 API key was
# "missing" whether or not it was set. Identity gates it on either origin now.
app.include_router(services.router, dependencies=_OPERATOR)
app.include_router(events.router, dependencies=_LOOPBACK)
# Letta tool sandbox runs off-loopback (Docker bridge); internal-only, no
# personal data, never proxied to the public origin.

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
