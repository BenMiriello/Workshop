from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from routes import agents, projects, finance, plaid_webhook, relay, services, events, agent_runs, backlog, auto_pr
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
    # Fire-and-forget cache prewarm so startup isn't blocked on Plaid latency.
    asyncio.create_task(_prewarm_finance_cache())
    yield
    await app.state.supervisor.stop()
    await app.state.letta.aclose()


app = FastAPI(title="Workshop", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/agents/supervisor/status")
async def supervisor_status(request: Request):
    return request.app.state.supervisor.status()
