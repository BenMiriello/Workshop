"""This app's declaration: what it is, what it mounts, what it runs.

Moved out of `services/app_registry.py` on 2026-09-01, where one hardcoded
factory per app made the platform's catalog module know every app by name. The
registry now discovers declarations and names none. Behaviour is unchanged: the
spec below is the same object, moved verbatim.
"""
from __future__ import annotations

import asyncio
import logging
import time

from fastapi import Depends

from deps_user import require_user
from services.app_spec import AppSpec, RouterMount, owner_file

log = logging.getLogger("workshop.apps")

# Local hour after which the nightly categorization may run.
_CATEGORIZE_HOUR_LOCAL = 23


async def _categorize_loop() -> None:
    """Once-a-day Sonnet categorization of uncategorized/transfer transactions.

    THIS LIVES IN BUDGET BECAUSE THE WORK DOES. It was declared on the finance
    app's `background` tuple, while the function it calls is
    `services/budget_categorizer.py` and everything it writes is budget's store.
    Two apps' code in one declaration is the shape this whole separation exists
    to remove: nothing in finance's own tree explained why finance would run it,
    and a reader tracing the nightly write from `budget.json` backwards landed
    in the wrong app.
    """
    from services import auth_store, ownership, budget_categorizer as categorizer

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
                ownership.set_current_owner(oid)
                summary = await categorizer.run(days=30)
                log.info("nightly categorizer: %s", summary)
            except Exception as e:
                log.warning("nightly categorizer failed: %s", e)
            last_run_day = today


# Route modules this app needs; the catalog drops the app when they are not
# shipped. See `app_registry.APP_REQUIRES_ATTR`.
REQUIRES = ("budget",)


def app_spec() -> AppSpec:
    from routes import budget
    from services import budget_persona
    # The plaid MCP belongs to the app that owns the bank connection; this app
    # shares it, which is the same dependency it declares as needs=["finance.read"].
    from services.finance_app import PLAID_MCP, PLAID_MCP_PROVIDER
    spec = AppSpec(
        id="budget",
        chat_model_tier="sonnet",  # plan-patch needs careful path generation
        chat_provider=budget_persona.BudgetContextProvider,
        card={
            "title": "Budget",
            "tagline": "Payoff plan, savings goals, projections",
            "icon": "PiggyBank",
        },
        identity={
            "sensitivity": "private",
            "api_prefixes": ["/budget"],
            "provides": [],
            "needs": ["finance.read"],
        },
        mounts=(
            RouterMount(budget.router, prefix="/budget", tags=("budget",), deps=(Depends(require_user),)),
        ),
        mcp=PLAID_MCP,
        mcp_provider=PLAID_MCP_PROVIDER,
        stores=(("plan", lambda: owner_file("budget.json"), "Budget", True),),
        background=(_categorize_loop,),
    )
    spec.card["route"] = f"/apps/{spec.id}"
    return spec
