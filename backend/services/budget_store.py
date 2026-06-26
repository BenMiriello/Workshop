"""Budget plan persistence.

The budget plan is the operator's *intent* layer on top of live Plaid data:
what to pay off (and by when), what to save for (and by when), what the fixed
necessities are, and target spend per variable category. Live balances and
transactions come from Plaid; this file holds only the editable plan.

Single-user system — no per-user namespacing, no auth gates here (the route
sits behind the backend's _SECURE dependency like the rest of finance).

Plan file: ~/workshop/.runtime/budget.json
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from services import ownership

# The budget plan + transaction overrides are personal data, partitioned per
# owner: .runtime/users/<owner_id>/budget.json and .../txn-overrides.json. The
# owner is the authenticated user (pinned in the ownership contextvar); a request
# without one raises OwnerUnset (fail-closed). Each user has their own budget.
def _plan_file() -> Path:
    return ownership.user_runtime_dir() / "budget.json"


# Per-transaction category overrides written by the daily Sonnet categorizer
# (services/categorizer.py). The engine consults these BEFORE Plaid's PFC rules,
# so a model judgement ("this Venmo is rent", "this is the late June utility")
# sticks across reloads. Schema: { txn_id: {bucket, reason, ts, source} } where
# `bucket` is a variable id, a necessity id, "necessities", or "exclude".
def _overrides_file() -> Path:
    return ownership.user_runtime_dir() / "txn-overrides.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Seed grounded in the data we actually observed (2026-06):
#   - Regular Veritus paycheck $5,036.84 x2/mo  -> $10,073.68/mo (bonus check excluded)
#   - Citi Simplicity 0% card ··6821 at $8,722, promo ~12mo out -> clear by 2027-05
#   - Chase savings ··9172 ~$2,000 seeds the emergency fund
# Everything with amount 0 / balance 0 is a placeholder the operator fills in
# (especially the "Repay Dad" debt, which needs joint-account reconstruction).
# `account_mask` lets the engine override balance/current from live Plaid.
def default_plan() -> dict:
    return {
        "version": 1,
        "updated_at": _now_iso(),
        "income": {
            "monthly": 10073.68,
            "paychecks_per_month": 2,
            "per_paycheck": 5036.84,
            "pay_days": [15, 30],
            "source": "Veritus payroll — regular (bonus check excluded)",
        },
        # Fixed, unavoidable monthly costs — "locked down" first in the waterfall.
        "necessities": [
            {"id": "rent", "name": "Rent", "monthly": 0, "note": ""},
            {"id": "utilities", "name": "Utilities (Venmo)", "monthly": 0, "note": "paid via Venmo"},
            {"id": "phone", "name": "Phone", "monthly": 0, "note": ""},
            {"id": "insurance", "name": "Insurance", "monthly": 0, "note": ""},
        ],
        # Debts to pay off, in priority order. clear_by_promo derives the
        # required monthly from balance / months-until-promo-expiry.
        "debts": [
            {
                "id": "citi", "name": "Citi Simplicity (0%)", "priority": 1,
                "balance": 8722, "account_mask": "6821",
                "apr": 0, "promo_until": "2027-05-01", "strategy": "clear_by_promo",
                "planned_monthly": 800,
                "note": "0% promo — clear before it expires or rate jumps to ~25%",
            },
            {
                "id": "chase_9899", "name": "Chase card ··9899", "priority": 2,
                "balance": 0, "account_mask": "9899",
                "apr": None, "promo_until": None, "strategy": "fixed",
                "planned_monthly": 0,
                "note": "revolving (not 0%) — carries interest; balance merged live from ··9899",
            },
            {
                "id": "dad", "name": "Repay Dad (joint)", "priority": 3,
                "balance": 0, "account_mask": None,
                "apr": 0, "promo_until": None, "strategy": "fixed",
                "planned_monthly": 0,
                "note": "reconstruct total owed from joint-account ··8202 history",
            },
        ],
        # Savings buckets, priority order. Emergency fund is the contingency
        # plan — top priority until target met. term: contingency|short|med|long.
        "savings": [
            {
                "id": "emergency", "name": "Emergency fund", "priority": 1, "term": "contingency",
                "target": 15000, "current": 2000, "account_mask": "9172",
                "target_date": None, "planned_monthly": 1000,
                "note": "contingency if income stops — aggressive until target met",
            },
            {
                "id": "short", "name": "Short-term goals", "priority": 2, "term": "short",
                "target": 0, "current": 0, "account_mask": None,
                "target_date": None, "planned_monthly": 0, "note": "",
            },
            {
                "id": "med", "name": "Mid-term goals", "priority": 3, "term": "med",
                "target": 0, "current": 0, "account_mask": None,
                "target_date": None, "planned_monthly": 0, "note": "",
            },
            {
                "id": "long", "name": "Long-term / invest", "priority": 4, "term": "long",
                "target": 0, "current": 0, "account_mask": None,
                "target_date": None, "planned_monthly": 0, "note": "",
            },
        ],
        # Variable spend categories. `target` is the monthly budget; `match`
        # lists Plaid detailed-category substrings the engine sums actuals from.
        "variable": [
            {"id": "groceries", "name": "Groceries", "target": 0,
             "match": ["FOOD_AND_DRINK_GROCERIES"]},
            {"id": "eating_out", "name": "Eating out", "target": 0,
             "match": ["FOOD_AND_DRINK_RESTAURANT", "FOOD_AND_DRINK_FAST_FOOD",
                       "FOOD_AND_DRINK_COFFEE", "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR",
                       "FOOD_AND_DRINK_OTHER"]},
            {"id": "transport", "name": "Transport", "target": 0,
             "match": ["TRANSPORTATION", "TRAVEL"]},
            {"id": "shopping", "name": "Shopping", "target": 0,
             "match": ["GENERAL_MERCHANDISE"]},
            {"id": "subscriptions", "name": "Subscriptions & services", "target": 0,
             "match": ["GENERAL_SERVICES", "RENT_AND_UTILITIES_INTERNET",
                       "PERSONAL_CARE"]},
            {"id": "fun", "name": "Fun & entertainment", "target": 0,
             "match": ["ENTERTAINMENT"]},
            {"id": "medical", "name": "Medical & dental", "target": 0,
             "match": ["MEDICAL"]},
        ],
    }


def load_plan() -> dict:
    plan_file = _plan_file()
    if not plan_file.exists():
        plan = default_plan()
        save_plan(plan)
        return plan
    try:
        return json.loads(plan_file.read_text())
    except (json.JSONDecodeError, OSError):
        # Corrupt plan — reseed rather than 500 the whole app.
        plan = default_plan()
        save_plan(plan)
        return plan


def save_plan(plan: dict) -> dict:
    plan["updated_at"] = _now_iso()
    plan_file = _plan_file()
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = plan_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan, indent=2) + "\n")
    tmp.replace(plan_file)
    return plan


def load_overrides() -> dict:
    """Per-transaction category overrides. {} if none yet."""
    overrides_file = _overrides_file()
    if not overrides_file.exists():
        return {}
    try:
        data = json.loads(overrides_file.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_overrides(overrides: dict) -> dict:
    overrides_file = _overrides_file()
    overrides_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = overrides_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(overrides, indent=2) + "\n")
    tmp.replace(overrides_file)
    return overrides


def merge_overrides(new: dict) -> dict:
    """Merge categorizer output into the stored overrides and persist. New keys
    win (re-categorization corrects an earlier call). Returns the merged dict."""
    cur = load_overrides()
    cur.update(new)
    return save_overrides(cur)
