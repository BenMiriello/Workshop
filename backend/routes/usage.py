"""Usage API — rolling-window token budget for autonomous work.

`GET /usage` returns `services.usage_ledger.status()`: the app-wide 5h window
budget (total/used/remaining) plus a per-project breakdown (used, allowance,
remaining, share_of_app). Read-only.

THE HEADLINE IS ADMISSION-RELEVANT SPEND. Interactive chat turns and compaction
are metered but are never gated, so they are reported under `excluded` rather
than added to `used`. Reported together they read as a blown budget: 265M
against a 5M pool on 2026-08-23, 53x the cap, while the only figure any guard
was reading was the 1.97M underneath it.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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



class ProjectUsage(BaseModel):
    """One project's slice. `allowance` and `remaining` are NULL on an uncapped
    window rather than 0: the longer lookbacks are reporting-only and have no
    allowance to report, and zero would claim they had exhausted one."""

    used: int
    allowance: int | None = None
    remaining: int | None = None
    share_of_app: float


class ExcludedUsage(BaseModel):
    """The spend the admission guard does not count, kept separate and named.

    Chat and compaction ARE metered (that is the point: the operator should see
    what a conversation cost) and are excluded from admission, because chat is
    declared unmetered and is never gated. Folding them into one `used` total is
    what made this page read 265M against a 5M pool while the guard that decides
    anything was looking at 1.97M."""

    total: int
    by_role: dict[str, int]
    roles: list[str]


class UsageStatus(BaseModel):
    """A trailing window. Only the 5h window carries a cap (`capped: true`);
    the rest are reporting-only lookbacks.

    `app_used` and every project's `used` are ADMISSION-RELEVANT spend: the same
    figure `can_admit` reads, so the number on the screen is the number the
    decision used. Everything the guard ignores is in `excluded`, with its roles
    named."""

    window_s: int
    capped: bool
    app_total: int | None = None
    app_used: int
    app_remaining: int | None = None
    excluded: ExcludedUsage
    projects: dict[str, ProjectUsage]


class UsagePoint(BaseModel):
    """One interval of the usage chart. `t` is the only fixed key: the remaining
    columns are PROJECT SLUGS mapped to that interval's token count, so they
    differ per instance and per window, and `UsageTimeseries.projects` is what
    names them.

    Declared as a floor rather than left open because `t` is the x-axis and a
    client that cannot see it cannot draw anything. `extra="allow"` carries the
    dynamic slug columns through untouched."""

    model_config = ConfigDict(extra="allow")

    t: str | None = None


class UsageTimeseries(BaseModel):
    """Per-interval per-project tokens. Each point is `{t: <iso>, <slug>: <int>,
    ...}`, so the project columns are dynamic and the point is left open;
    `projects` names them and `bin_s` is returned so the client can label the
    rate rather than deriving it."""

    window_s: int
    bin_s: int
    projects: list[str]
    points: list[UsagePoint]


class ChatTimeseries(BaseModel):
    """Per-interval per-chat tokens over the last 5h. Same shape as
    UsageTimeseries but grouped by the originating chat thread ID rather than
    project slug. Entries without a chat field are grouped under '_unbound'."""

    window_s: int
    bin_s: int
    chats: list[str]
    points: list[UsagePoint]


@router.get("", response_model=UsageStatus, response_model_exclude_unset=True)
async def get_usage(window: str = "5h") -> dict:
    """Snapshot of a trailing usage window: app-wide totals + per-project share.
    Pure read over `.runtime/usage.jsonl` + `.runtime/usage-budget.json`; no side
    effects. Only the 5h window carries a cap (`capped: true`); the longer
    lookbacks are reporting-only and report totals with no allowance."""
    return usage_ledger.status(window_s=_resolve_window(window))


@router.get("/timeseries", response_model=UsageTimeseries, response_model_exclude_unset=True)
async def get_usage_timeseries(window: str = "5h") -> dict:
    """Per-interval per-project token usage across a trailing window, for the
    chart. Pure read; no side effects.

    Admission-relevant rows only, matching `GET /usage`'s headline. The chart and
    the totals it sits under have to be one measurement; chat spend has its own
    view (`/usage/chat-timeseries`), where it is legible instead of swamping
    every other series."""
    return usage_ledger.timeseries(
        window_s=_resolve_window(window),
        exclude_roles=usage_ledger.ADMISSION_EXCLUDED_ROLES)


@router.get("/chat-timeseries", response_model=ChatTimeseries, response_model_exclude_unset=True)
async def get_chat_timeseries() -> dict:
    """Per-interval per-chat token usage for the last 5h, for the By Chat view.
    Always the standard 5h window; no budget cap applies here."""
    return usage_ledger.chat_timeseries(window_s=5 * 3600)


class BudgetPatch(BaseModel):
    app_total: int | None = Field(default=None, ge=1)
    autonomy_budget_fraction: float | None = Field(default=None, ge=0.1, le=1.0)


@router.put("/budget", response_model=UsageStatus, response_model_exclude_unset=True)
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
