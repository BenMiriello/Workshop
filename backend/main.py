from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from deps_auth import require_token

from routes import agents, projects, finance, budget, plaid_webhook, relay, services, events, agent_runs, backlog, auto_pr, commute, location, owntracks, redaction, tools, health, autonomy, chat, birds
from services.supervisor import AgentSupervisor
from services import global_memory, finance_cache, categorizer
from letta_legacy_client import LettaClient
import asyncio
import logging
import time
import plaid_bridge

log = logging.getLogger("workshop.main")

# Keep the finance cache warm so a human visit basically always hits warm cache
# and stale-while-revalidate serves recent (not hours-old) numbers. Under the
# 10-min balances TTL so the served data is never meaningfully stale.
_PREWARM_INTERVAL_S = 9 * 60
# Run the Sonnet categorizer once a day (end of day, Pacific). The loop checks
# every ~30 min and fires when it's past the hour and hasn't run yet today.
_CATEGORIZE_HOUR_LOCAL = 23


async def _prewarm_finance_cache():
    """Pre-fetch balances + transactions so the next browser load is already hot.
    Uses Plaid's fast cached balances (accountsGet) — not the slow per-bank
    real-time call. Background, best-effort; on Plaid trouble the UI just hits
    the normal cache path."""
    try:
        await finance_cache.get_or_fetch("balances", plaid_bridge.balances)
        await finance_cache.get_or_fetch("transactions:30", lambda: plaid_bridge.transactions(days=30))
    except Exception:
        pass


async def _prewarm_loop():
    """Periodically re-prewarm so the cache never goes cold between visits."""
    while True:
        await asyncio.sleep(_PREWARM_INTERVAL_S)
        await _prewarm_finance_cache()


async def _categorize_loop():
    """Once-a-day Sonnet categorization of uncategorized/transfer transactions."""
    last_run_day = None
    while True:
        await asyncio.sleep(30 * 60)
        lt = time.localtime()
        today = (lt.tm_year, lt.tm_yday)
        if lt.tm_hour >= _CATEGORIZE_HOUR_LOCAL and today != last_run_day:
            try:
                summary = await categorizer.run(days=30)
                log.info("nightly categorizer: %s", summary)
            except Exception as e:
                log.warning("nightly categorizer failed: %s", e)
            last_run_day = today


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
    asyncio.create_task(_prewarm_finance_cache())
    app.state._bg_prewarm = asyncio.create_task(_prewarm_loop())
    app.state._bg_categorize = asyncio.create_task(_categorize_loop())
    yield
    for t in (getattr(app.state, "_bg_prewarm", None), getattr(app.state, "_bg_categorize", None)):
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
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sensitive routers (finance data + agent control) gate off-host callers via
# require_token; loopback (browser-via-Vite, cron, masini, self-calls) is trusted.
_SECURE = [Depends(require_token)]
app.include_router(agent_runs.router, dependencies=_SECURE)  # MUST be before agents router so /agents/runs wins over /agents/{agent_id}
app.include_router(agents.router, prefix="/agents", tags=["agents"], dependencies=_SECURE)
app.include_router(projects.router, prefix="/projects", tags=["projects"], dependencies=_SECURE)
app.include_router(finance.router, prefix="/finance", tags=["finance"], dependencies=_SECURE)
app.include_router(budget.router, prefix="/budget", tags=["budget"], dependencies=_SECURE)
app.include_router(chat.router, prefix="/chat", tags=["chat"], dependencies=_SECURE)
# Birds read API is tailnet-private (full fidelity: audio, exact times). The
# /birds/webhook ingest inside it is loopback-only in practice (BirdNET-Go posts
# from 127.0.0.1, which require_token already trusts).
app.include_router(birds.router, dependencies=_SECURE)
app.include_router(plaid_webhook.router, prefix="/plaid/webhook", tags=["plaid-webhook"])
app.include_router(relay.router)
app.include_router(services.router)
app.include_router(events.router)
app.include_router(backlog.router, dependencies=_SECURE)
app.include_router(auto_pr.router)
app.include_router(commute.router)
app.include_router(location.router)
app.include_router(owntracks.router)
app.include_router(health.router)
app.include_router(redaction.router)
app.include_router(redaction.restore_router)
app.include_router(tools.router)
app.include_router(autonomy.router, dependencies=_SECURE)


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
async def supervisor_status(request: Request):
    return request.app.state.supervisor.status()
