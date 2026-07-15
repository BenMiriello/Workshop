"""Usage API — rolling-window token budget for autonomous work.

`GET /usage` returns `services.usage_ledger.status()`: the app-wide 5h window
budget (total/used/remaining) plus a per-project breakdown (used, allowance,
remaining, share_of_app). Read-only. Interactive chat turns are never metered,
so they never appear here; only autonomous sweeps write to the ledger.
"""
from __future__ import annotations

from fastapi import APIRouter

from services import usage_ledger

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("")
async def get_usage() -> dict:
    """Snapshot of the trailing usage window: app-wide totals + per-project
    share. Pure read over `.runtime/usage.jsonl` + `.runtime/usage-budget.json`;
    no side effects."""
    return usage_ledger.status()
