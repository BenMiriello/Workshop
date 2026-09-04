"""This app's declaration: what it is, what it mounts, what it runs.

Moved out of `services/app_registry.py` on 2026-09-01. The registry used to
hold one hardcoded factory per app plus every app-specific background loop, so
the platform's catalog module knew all eight apps by name. It now discovers
declarations and names none; this file is this app's half of that split.

Nothing here changed behaviour: the spec, the auth on each mount, the plaid MCP
launch spec, and the two background loops are the same objects that were in the
registry, moved verbatim.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from fastapi import Depends, FastAPI

from deps_user import require_user
from services.app_spec import AppSpec, RouterMount, owner_file

log = logging.getLogger("workshop.apps")


# --------------------------------------------------------------------------- #
# The plaid MCP server the finance & budget chat assistants use for on-demand
# transaction lookup.
#
# The source lives IN-TREE at backend/mcp/plaid/ (final-plan PM.4): it used to
# be sourced from outside the repo, which made it undiffable and tied a
# first-class platform capability to another program's home directory.
# Repo-relative, so a fresh clone gets it after one `npm install` in that dir;
# override with the env var.
#
# NOT moved: the secrets it reads. The credentials and the per-bank access
# tokens keep their host-level paths — server.js reads them from there
# directly. Code moves into the repo; secrets never do.
#
# Budget imports both of these. That is an honest cross-app dependency, the
# same one it already declares as `needs: ["finance.read"]`: the two apps share
# one bank connection, and this app owns it.
# --------------------------------------------------------------------------- #
PLAID_MCP = {
    "plaid": {
        "type": "stdio",
        "command": "node",
        "args": [os.environ.get(
            "WORKSHOP_PLAID_MCP",
            str(Path(__file__).resolve().parents[1] / "mcp" / "plaid" / "server.js"),
        )],
    },
}

# Provider interface for the plaid MCP server (Phase 4). Every MCP provider must
# declare an `export` op naming the Store it dumps; plaid exports this app's
# plaid-items Store.
PLAID_MCP_PROVIDER = {
    "plaid": {
        "export": {"store_uri": "workshop://store/finance/plaid-items"},
    },
}


_PREWARM_INTERVAL_S = 9 * 60


async def _prewarm_cache(realtime: bool = False) -> None:
    """Pre-fetch balances + transactions so the next browser load is already hot.
    This app is per-owner; prewarm the operator's cache. Best-effort."""
    from services import auth_store, ownership, finance_cache
    from services import finance_plaid as plaid_bridge

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


async def _prewarm_loop() -> None:
    while True:
        await asyncio.sleep(_PREWARM_INTERVAL_S)
        await _prewarm_cache(realtime=False)


async def _on_startup(app: FastAPI) -> None:
    """Wire the cache-invalidate prewarm hook and fire an initial realtime warm."""
    from services import finance_cache

    finance_cache.set_invalidate_hook(_prewarm_cache)
    asyncio.create_task(_prewarm_cache(realtime=True))


# Route modules this app needs; the catalog drops the app when they are not
# shipped. See `app_registry.APP_REQUIRES_ATTR`.
REQUIRES = ("finance", "finance_plaid_webhook",)


def app_spec() -> AppSpec:
    from routes import finance, finance_plaid_webhook as plaid_webhook
    from services import finance_events, finance_persona, finance_store_adapter
    spec = AppSpec(
        id="finance",
        chat_provider=finance_persona.FinanceContextProvider,
        chat_snapshot=finance_persona.finance_snapshot,
        card={
            "title": "Finance",
            "tagline": "Balances, accounts, recent transactions",
            "icon": "Banknote",
        },
        identity={
            "sensitivity": "private",
            "api_prefixes": ["/finance", "/plaid"],
            "provides": ["finance.read"],
            "needs": [],
        },
        mounts=(
            RouterMount(finance.router, prefix="/finance", tags=("finance",), deps=(Depends(require_user),)),
            # Plaid webhook — Plaid-signed, no user gate.
            RouterMount(plaid_webhook.router, prefix="/plaid/webhook", tags=("plaid-webhook",)),
        ),
        mcp=PLAID_MCP,
        mcp_provider=PLAID_MCP_PROVIDER,
        on_startup=(_on_startup,),
        background=(_prewarm_loop,),
        event_sources=("plaid",),
        event_verifiers={"plaid": finance_events.verify_webhook},
        event_dedup={"plaid": finance_events.dedup_key},
        edge_blocked_prefixes=("/plaid/webhook",),
        secret_env=("PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV", "PLAID_ENV_URL"),
        stores=(
            ("plaid-items", lambda: owner_file("plaid-items.json"), "Finance", True),
            ("transactions", lambda: owner_file("finance-cache.json"), "Finance", True),
        ),
        store_adapters=(finance_store_adapter.FinanceAdapter(),),
    )
    spec.card["route"] = f"/apps/{spec.id}"
    return spec
