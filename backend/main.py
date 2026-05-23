from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

from services.landing import LANDING_HTML

from routes import agents, projects, finance, plaid_webhook, relay, services, events, agent_runs, backlog, auto_pr, commute, location, owntracks
from services.supervisor import AgentSupervisor
from services import global_memory, finance_cache
from letta_client import LettaClient
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


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    """Workshop launcher. Served at https://debian.tail38e62e.ts.net/ via
    Tailscale Serve mount :443 → :8000.

    If someone hit us at the Tailscale IP form over HTTP (insecure context,
    no geolocation), redirect to the canonical HTTPS hostname so bookmarks
    self-correct."""
    host = request.headers.get("host", "")
    # Anything other than the tail38e62e magicdns name (with optional port)
    # gets bounced. Match by suffix so app ports (8443/8444/8445) are fine too.
    if "tail38e62e.ts.net" not in host:
        return RedirectResponse(url="https://debian.tail38e62e.ts.net/", status_code=302)
    return HTMLResponse(LANDING_HTML)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/agents/supervisor/status")
async def supervisor_status(request: Request):
    return request.app.state.supervisor.status()
