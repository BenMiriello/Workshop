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


# Selectable reporting windows. "5h" is the budget window (the only one with a
# cap); the rest are reporting-only lookbacks. "all" spans the whole ledger.
_WINDOWS: dict[str, int | None] = {
    "5h": 5 * 3600,
    "24h": 24 * 3600,
    "7d": 7 * 24 * 3600,
    "30d": 30 * 24 * 3600,
    "all": None,  # resolved per-request from the oldest ledger entry
}


def _resolve_window(window: str) -> int:
    if window not in _WINDOWS:
        raise HTTPException(400, f"unknown window: {window} (want one of {', '.join(_WINDOWS)})")
    return _WINDOWS[window] or usage_ledger.full_window_s()


@router.get("")
async def get_usage(window: str = "5h") -> dict:
    """Snapshot of a trailing usage window: app-wide totals + per-project share.
    Pure read over `.runtime/usage.jsonl` + `.runtime/usage-budget.json`; no side
    effects. Only the 5h window carries a cap (`capped: true`); the longer
    lookbacks are reporting-only and report totals with no allowance."""
    return usage_ledger.status(window_s=_resolve_window(window))


@router.get("/timeseries")
async def get_usage_timeseries(window: str = "5h") -> dict:
    """Per-interval per-project token usage across a trailing window, for the
    chart. Pure read; no side effects."""
    return usage_ledger.timeseries(window_s=_resolve_window(window))


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
