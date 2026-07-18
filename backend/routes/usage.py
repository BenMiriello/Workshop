"""Usage API — rolling-window token budget for autonomous work.

`GET /usage` returns `services.usage_ledger.status()`: the app-wide 5h window
budget (total/used/remaining) plus a per-project breakdown (used, allowance,
remaining, share_of_app). Read-only. Interactive chat turns are never metered,
so they never appear here; only autonomous sweeps write to the ledger.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import usage_ledger

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("")
async def get_usage() -> dict:
    """Snapshot of the trailing usage window: app-wide totals + per-project
    share. Pure read over `.runtime/usage.jsonl` + `.runtime/usage-budget.json`;
    no side effects."""
    return usage_ledger.status()


class BudgetPatch(BaseModel):
    app_total: int | None = Field(default=None, ge=1)
    autonomy_budget_fraction: float | None = Field(default=None, ge=0.1, le=1.0)


@router.put("/budget")
async def put_budget(body: BudgetPatch) -> dict:
    """Edit the two operator-tunable budget knobs (app-wide cap, autonomy
    fraction) and return the refreshed window snapshot. Operator-gated at the
    router mount."""
    try:
        usage_ledger.update_config(
            app_total=body.app_total,
            autonomy_budget_fraction=body.autonomy_budget_fraction,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return usage_ledger.status()
