from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from deps_auth import require_loopback, require_internal_caller
from deps_user import (
    require_user,
    require_operator,
    require_domain_owner,
)

from routes import agents, projects, base_apps, finance, budget, plaid_webhook, relay, services, events, agent_runs, backlog, auto_pr, commute, location, owntracks, push, redaction, tools, health, autonomy, chat, birds, environment, worktrees, auth, voice, knowledge, skills
from services.supervisor import AgentSupervisor
from services import global_memory, finance_cache, categorizer, ownership, auth_store
from services import environment as environment_svc
from services import birds_analyze
from services import finance_plaid as plaid_bridge
from letta_legacy_client import LettaClient
import asyncio
import logging
import time

log = logging.getLogger("workshop.main")

# Keep the finance cache warm so a human visit basically always hits warm cache
# and stale-while-revalidate serves recent (not hours-old) numbers. Under the
# 10-min balances TTL so the served data is never meaningfully stale.
_PREWARM_INTERVAL_S = 9 * 60
# Run the Sonnet categorizer once a day (end of day, Pacific). The loop checks
# every ~30 min and fires when it's past the hour and hasn't run yet today.
_CATEGORIZE_HOUR_LOCAL = 23


async def _prewarm_finance_cache(realtime: bool = False):
    """Pre-fetch balances + transactions so the next browser load is already hot.
    realtime=True uses accountsBalanceGet (live bank connection, ~4s). Used on
    startup so a fresh restart doesn't serve Plaid's own stale snapshot. The
    periodic loop uses realtime=False (fast accountsGet). Background, best-effort.

    Finance is per-owner now, so prewarm the OPERATOR's cache (the box's primary
    finance user). The owner must be pinned for the per-owner cache + Plaid store."""
    oid = auth_store.operator_id()
    if oid is None:
        return
    ownership.set_current_owner(oid)
    try:
        await finance_cache.get_or_fetch(
            "balances",
            lambda: plaid_bridge.balances(realtime=realtime),
            force_refresh=realtime,
        )
        await finance_cache.get_or_fetch("transactions:30", lambda: plaid_bridge.transactions(days=30))
    except Exception:
        pass


async def _prewarm_loop():
    """Periodically re-prewarm so the cache never goes cold between visits."""
    while True:
        await asyncio.sleep(_PREWARM_INTERVAL_S)
        await _prewarm_finance_cache(realtime=False)


async def _categorize_loop():
    """Once-a-day Sonnet categorization of uncategorized/transfer transactions."""
    last_run_day = None
    while True:
        await asyncio.sleep(30 * 60)
        lt = time.localtime()
        today = (lt.tm_year, lt.tm_yday)
        if lt.tm_hour >= _CATEGORIZE_HOUR_LOCAL and today != last_run_day:
            try:
                oid = auth_store.operator_id()
                if oid is None:
                    continue
                ownership.set_current_owner(oid)  # categorize the operator's txns
                summary = await categorizer.run(days=30)
                log.info("nightly categorizer: %s", summary)
            except Exception as e:
                log.warning("nightly categorizer failed: %s", e)
            last_run_day = today


async def _env_refresh_loop():
    """Keep the Birds weather/astro spine fresh: stdlib sun/moon + Open-Meteo
    (recent actuals + near-future forecast). Refreshes on startup then every
    6h. Off-thread (sync sqlite/httpx) and best-effort — never blocks the loop
    or startup, degrades to a no-op if egress is unavailable."""
    while True:
        try:
            n = await asyncio.to_thread(environment_svc.refresh)
            log.info("env refresh: %s", "egress blocked" if n == -1 else f"{n} rows")
        except Exception as e:
            log.warning("env refresh failed: %s", e)
        # Score any forecast predictions whose hour has passed (the self-measuring
        # loop). Best-effort; never blocks the refresh cadence.
        try:
            from services import birds_forecast
            graded = await asyncio.to_thread(birds_forecast.score_due)
            if graded:
                log.info("forecast calibration: graded %s hours", graded)
        except Exception as e:
            log.warning("forecast scoring failed: %s", e)
        await asyncio.sleep(6 * 60 * 60)


async def _atlas_rebuild_loop():
    """Keep the Soundscape Atlas current: embeddings are captured live as detections
    are stored, but the UMAP/HDBSCAN projection only refreshes on a build. Run that
    build nightly, gated on enough new embeddings to be worth the cost (the gate
    lives in birds_analyze.maybe_auto_rebuild). Heavy ML runs detached in the
    analyzer venv; this just triggers it. Best-effort, off-thread, never blocks."""
    await asyncio.sleep(5 * 60)  # let startup settle before the first check
    while True:
        try:
            res = await asyncio.to_thread(birds_analyze.maybe_auto_rebuild)
            if res.get("triggered"):
                log.info("atlas auto-rebuild: triggered (%s embedded, %s new)",
                         res.get("embedded"), res.get("new"))
        except Exception as e:
            log.warning("atlas auto-rebuild check failed: %s", e)
        await asyncio.sleep(24 * 60 * 60)


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
    # Re-prewarm after any cache invalidate (webhook OR manual /cache/invalidate)
    # so the next browser request hits warm cache. Registered here to avoid a
    # circular import (finance_cache shouldn't reach back into plaid_bridge).
    finance_cache.set_invalidate_hook(_prewarm_finance_cache)
    # Fire-and-forget cache prewarm so startup isn't blocked on Plaid latency,
    # plus background loops to keep it warm and categorize nightly.
    asyncio.create_task(_prewarm_finance_cache(realtime=True))
    app.state._bg_prewarm = asyncio.create_task(_prewarm_loop())
    app.state._bg_categorize = asyncio.create_task(_categorize_loop())
    app.state._bg_env = asyncio.create_task(_env_refresh_loop())
    app.state._bg_atlas = asyncio.create_task(_atlas_rebuild_loop())
    # App platform: keep desired-running multi-service apps alive (restart crashed
    # services with backoff) and reconcile the port registry each pass.
    from services import app_monitor
    app.state._bg_app_monitor = asyncio.create_task(app_monitor.monitor_loop())
    yield
    for t in (getattr(app.state, "_bg_prewarm", None), getattr(app.state, "_bg_categorize", None), getattr(app.state, "_bg_env", None), getattr(app.state, "_bg_atlas", None), getattr(app.state, "_bg_app_monitor", None)):
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
app.include_router(agent_runs.router, dependencies=_OPERATOR)  # before agents so /agents/runs wins over /agents/{agent_id}
app.include_router(agents.router, prefix="/agents", tags=["agents"], dependencies=_OPERATOR)
app.include_router(projects.router, prefix="/projects", tags=["projects"], dependencies=_OPERATOR)
app.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"], dependencies=_OPERATOR)
app.include_router(skills.router, prefix="/skills", tags=["skills"], dependencies=_OPERATOR)
app.include_router(worktrees.router, prefix="/worktrees", tags=["worktrees"], dependencies=_OPERATOR)
app.include_router(base_apps.router, prefix="/base-apps", tags=["base-apps"], dependencies=_OPERATOR)
app.include_router(backlog.router, dependencies=_OPERATOR)
app.include_router(autonomy.router, dependencies=_OPERATOR)
app.include_router(redaction.router, dependencies=_OPERATOR)
app.include_router(redaction.restore_router, dependencies=_OPERATOR)

# Finance + budget are now PER-USER: each account links its own banks (Plaid) and
# has its own budget plan, partitioned under .runtime/users/<owner_id>/. require_user
# pins the owner; a user with no linked banks sees empty finance/budget.
app.include_router(finance.router, prefix="/finance", tags=["finance"], dependencies=_USER)
app.include_router(budget.router, prefix="/budget", tags=["budget"], dependencies=_USER)
# Commute remains a single operator dataset (location-derived) for now.
app.include_router(commute.router, dependencies=[Depends(require_domain_owner("commute"))])

# Per-user personal data — owner-scoped to the authenticated user.
app.include_router(chat.router, prefix="/chat", tags=["chat"], dependencies=_USER)
# Voice mode — local STT/TTS for the chat surface, per-owner prefs.
app.include_router(voice.router, tags=["voice"], dependencies=_USER)
app.include_router(location.router, dependencies=_USER)
app.include_router(push.router, dependencies=_USER)
app.include_router(environment.router, dependencies=_USER)
# Birds: yard detections are already published publicly, so reads need any
# authenticated user; the BirdNET-Go ingest uses an ingest:birds service token.
app.include_router(birds.router, dependencies=_USER)
# Birds ingest (BirdNET-Go webhook + yard-mic analyze/live) — loopback/LAN only,
# carries its own device token in-body, never proxied to the public origin.
app.include_router(birds.ingest_router, dependencies=[Depends(require_internal_caller)])

# Ingest webhooks — their own credential, bound to an owner inside the handler.
app.include_router(owntracks.router)   # HTTP Basic -> owner (in handler)
app.include_router(health.router)      # /ingest: HTTP Basic -> owner; reads: require_user (per-endpoint)
app.include_router(plaid_webhook.router, prefix="/plaid/webhook", tags=["plaid-webhook"])  # Plaid-signed

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


@app.get("/")
async def root_redirect(request: Request):
    """Tailscale Serve mounts the backend at :443/ so /api/* works for
    Owntracks etc. The bare URL `https://debian.tail38e62e.ts.net/` (no
    port) just redirects to Workshop at :8443 — each app lives at its
    own port like a normal self-hosted service.

    If someone hits us at the raw Tailscale IP over HTTP (insecure
    context), bounce them to the canonical magicdns HTTPS URL first."""
    host = request.headers.get("host", "")
    if "tail38e62e.ts.net" not in host:
        return RedirectResponse(url="https://debian.tail38e62e.ts.net:8443/", status_code=302)
    return RedirectResponse(url="https://debian.tail38e62e.ts.net:8443/", status_code=302)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/agents/supervisor/status")
async def supervisor_status(request: Request, user: dict = Depends(require_operator)):
    # Operator-gated (was unauthed): identity, not origin, controls access — so it
    # is safe on the public origin and no edge block is needed.
    return request.app.state.supervisor.status()
