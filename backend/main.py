from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from routes import agents, projects, finance, plaid_webhook, relay, services, events, agent_runs, backlog, auto_pr, commute, location, owntracks, redaction, tools, health
from services.supervisor import AgentSupervisor
from services import global_memory, finance_cache
from letta_legacy_client import LettaClient
import asyncio
import plaid_bridge


async def _prewarm_finance_cache():
    """Pre-fetch balances + transactions at startup so the very first browser
    load is already hot (cold Plaid roundtrip is ~4s). Runs in the background
    so it doesn't block startup; if Plaid is slow or down, the UI just hits
    the cache miss path on first request as before."""
    try:
        await finance_cache.get_or_fetch("balances", plaid_bridge.balances)
        await finance_cache.get_or_fetch("transactions:30", lambda: plaid_bridge.transactions(days=30))
    except Exception:
        pass


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
    # Fire-and-forget cache prewarm so startup isn't blocked on Plaid latency.
    asyncio.create_task(_prewarm_finance_cache())
    yield
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

app.include_router(agent_runs.router)  # MUST be before agents router so /agents/runs wins over /agents/{agent_id}
app.include_router(agents.router, prefix="/agents", tags=["agents"])
app.include_router(projects.router, prefix="/projects", tags=["projects"])
app.include_router(finance.router, prefix="/finance", tags=["finance"])
app.include_router(plaid_webhook.router, prefix="/plaid/webhook", tags=["plaid-webhook"])
app.include_router(relay.router)
app.include_router(services.router)
app.include_router(events.router)
app.include_router(backlog.router)
app.include_router(auto_pr.router)
app.include_router(commute.router)
app.include_router(location.router)
app.include_router(owntracks.router)
app.include_router(health.router)
app.include_router(redaction.router)
app.include_router(redaction.restore_router)
app.include_router(tools.router)


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
