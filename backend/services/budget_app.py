"""This app's declaration: what it is, what it mounts, what it runs.

Moved out of `services/app_registry.py` on 2026-09-01, where one hardcoded
factory per app made the platform's catalog module know every app by name. The
registry now discovers declarations and names none. Behaviour is unchanged: the
spec below is the same object, moved verbatim.
"""
from __future__ import annotations

from fastapi import Depends

from deps_user import require_user
from services.app_spec import AppSpec, RouterMount, owner_file


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
    )
    spec.card["route"] = f"/apps/{spec.id}"
    return spec
