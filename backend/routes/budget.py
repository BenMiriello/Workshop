from fastapi import APIRouter, HTTPException, Query

from services import finance_cache, budget_store, budget_engine, categorizer
from services import finance_plaid as plaid_bridge

router = APIRouter()


# Fields the inline editors are allowed to mutate, scoped per section. Anything
# else (id, match[], note, account_mask, etc.) is structural and only the full
# Edit-plan form should touch it. This keeps PATCH small + safe.
_PATCH_FIELDS = {
    "income": {"monthly", "per_paycheck", "paychecks_per_month", "source"},
    "necessities": {"name", "monthly", "note"},
    "debts": {"name", "balance", "planned_monthly", "apr", "promo_until", "strategy", "priority", "note", "account_mask"},
    "savings": {"name", "target", "current", "planned_monthly", "target_date", "term", "priority", "note", "account_mask"},
    # Variable categories: target is the primary editable; note added for parity.
    "variable": {"name", "target", "note"},
}


async def _live_data(days: int, refresh: bool = False):
    """Balances + transactions from the shared finance cache (same warm cache
    the Finance app and Apps grid use — no extra Plaid roundtrips on hot load).

    Returns (balances, txns, cache_meta) where cache_meta tells the UI whether
    it's looking at stale data that a background refresh is replacing, so it can
    re-poll and swap in the fresh numbers without a blocking spinner."""
    balances, bmeta = await finance_cache.get_or_fetch(
        "balances",
        lambda: plaid_bridge.balances(realtime=refresh),
        force_refresh=refresh,
    )
    txns, tmeta = await finance_cache.get_or_fetch(
        f"transactions:{days}",
        lambda: plaid_bridge.transactions(days=days),
        force_refresh=refresh,
    )
    cache_meta = {
        "stale": bool(bmeta.get("stale") or tmeta.get("stale")),
        "age_s": round(max(bmeta.get("age_s", 0.0), tmeta.get("age_s", 0.0)), 1),
        "balances": bmeta,
        "transactions": tmeta,
    }
    return balances, txns, cache_meta


@router.get("")
async def get_budget(days: int = Query(30, ge=1, le=730), refresh: bool = Query(False)):
    """Full computed budget: plan + live balances merged + pacing + actuals.

    Hot loads return instantly off cache. If the cache was stale a background
    refresh is already running and `cache.stale` is True — the UI should re-poll
    shortly to swap in fresh numbers. `?refresh=1` forces a blocking live pull."""
    try:
        plan = budget_store.load_plan()
        overrides = budget_store.load_overrides()
        balances, txns, cache_meta = await _live_data(days, refresh=refresh)
        out = budget_engine.compute(plan, balances, txns, window_days=days, overrides=overrides)
        out["cache"] = cache_meta
        return out
    except Exception as e:
        raise HTTPException(502, f"budget compute failed: {e}")


@router.get("/plan")
async def get_plan():
    """Raw editable plan (no computation) — what the edit form loads."""
    return budget_store.load_plan()


@router.get("/overrides")
async def get_overrides():
    """The per-transaction category overrides the daily categorizer has written."""
    return budget_store.load_overrides()


@router.post("/categorize")
async def run_categorize(days: int = Query(30, ge=1, le=730)):
    """Run the Sonnet categorizer now over uncategorized/transfer txns in the
    window and merge results into the override store. Also runs nightly."""
    try:
        return await categorizer.run(days=days)
    except Exception as e:
        raise HTTPException(502, f"categorize failed: {e}")


@router.put("/plan")
async def put_plan(plan: dict):
    """Persist an edited plan. Whole-document replace; the UI sends the full plan."""
    if not isinstance(plan, dict) or "version" not in plan:
        raise HTTPException(400, "invalid plan payload")
    return budget_store.save_plan(plan)


@router.patch("/plan")
async def patch_plan(patch: dict):
    """Partial update for inline edits. Three shapes accepted:

      {"section": "income", "field": "monthly", "value": 10500}
        - mutates plan["income"][field]

      {"section": "debts", "id": "citi", "field": "planned_monthly", "value": 950}
        - mutates the row with matching id in plan[section]

      {"section": "variable", "id": "groceries", "fields": {"target": 600, "name": "Food"}}
        - bulk-update multiple fields on a single row

    The allowed-field whitelist (_PATCH_FIELDS) keeps callers from touching
    structural attributes (id, match[], account_mask, etc.) via this endpoint;
    use PUT /plan for those.
    """
    if not isinstance(patch, dict):
        raise HTTPException(400, "invalid patch payload")
    section = patch.get("section")
    if section not in _PATCH_FIELDS:
        raise HTTPException(400, f"unknown section: {section!r}")
    allowed = _PATCH_FIELDS[section]

    # Normalize {field, value} -> {fields: {field: value}}
    fields = patch.get("fields")
    if fields is None and "field" in patch:
        fields = {patch["field"]: patch.get("value")}
    if not isinstance(fields, dict) or not fields:
        raise HTTPException(400, "missing fields to update")
    bad = [k for k in fields if k not in allowed]
    if bad:
        raise HTTPException(400, f"fields not patchable on {section}: {bad}")

    plan = budget_store.load_plan()

    if section == "income":
        plan.setdefault("income", {}).update(fields)
        # Keep monthly consistent if the operator edited per_paycheck or paychecks
        # without simultaneously editing monthly (the inline editor only sends
        # one field at a time).
        inc = plan["income"]
        if "monthly" not in fields and ("per_paycheck" in fields or "paychecks_per_month" in fields):
            inc["monthly"] = (inc.get("per_paycheck") or 0) * (inc.get("paychecks_per_month") or 0)
    else:
        row_id = patch.get("id")
        if not row_id:
            raise HTTPException(400, "missing row id")
        rows = plan.get(section) or []
        match = next((r for r in rows if r.get("id") == row_id), None)
        if match is None:
            raise HTTPException(404, f"no row in {section} with id {row_id!r}")
        match.update(fields)

    return budget_store.save_plan(plan)
