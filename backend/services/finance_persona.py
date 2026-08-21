"""The finance app's chat assistant and its live snapshots.

Moved out of `services/app_personas.py`, where Plaid's personal-finance-category
bucketization sat in a platform directory. Contributed through
`AppSpec.chat_provider` / `AppSpec.chat_snapshot`.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from services.app_persona import AppPersona
from wsplatform import paths

log = logging.getLogger("workshop.finance_persona")


def _runtime_dir() -> Path:
    """The instance .runtime dir (gitignored per-instance state), home-relative by
    default so nothing is pinned to one operator's checkout path."""
    return paths.runtime_dir()


def _finance_cache_path() -> Path:
    return Path(os.environ.get("WORKSHOP_FINANCE_CACHE", str(_runtime_dir() / "finance-cache.json")))


# ============================================================================ #
# Money formatting
# ============================================================================ #
def _money(x: Any) -> str:
    try:
        return f"${float(x):,.0f}"
    except (TypeError, ValueError):
        return str(x)


def _money_c(x: Any) -> str:
    try:
        return f"${float(x):,.2f}"
    except (TypeError, ValueError):
        return str(x)


# ============================================================================ #
# Finance snapshots (read the warm cache; no Plaid roundtrip)
# ============================================================================ #


def finance_snapshot() -> str:
    """Compact balances summary from the finance cache. Doesn't trigger a Plaid
    roundtrip — only reads the already-warm cache file."""
    cache_file = _finance_cache_path()
    if not cache_file.exists():
        return "LIVE BALANCES: (no finance cache; Plaid likely not warmed up yet)"
    try:
        state = json.loads(cache_file.read_text())
    except (json.JSONDecodeError, OSError):
        return "LIVE BALANCES: (finance cache unreadable)"
    bal = (state.get("balances") or {}).get("data") or {}
    items = bal.get("items") or []
    if not items:
        return "LIVE BALANCES: (no banks connected)"
    lines = ["LIVE BALANCES (from Plaid, cached):"]
    for item in items:
        for acct in item.get("accounts", []) or []:
            mask = acct.get("mask") or "?"
            name = acct.get("name") or acct.get("official_name") or "Account"
            subtype = acct.get("subtype") or acct.get("type") or ""
            cur = (acct.get("balance") or {}).get("current")
            limit = (acct.get("balance") or {}).get("limit")
            if subtype == "credit card":
                lines.append(
                    f"- {name} ··{mask} ({subtype}): owed {_money_c(cur)}"
                    + (f", limit {_money(limit)}" if limit else "")
                )
            else:
                lines.append(f"- {name} ··{mask} ({subtype}): {_money_c(cur)}")
    return "\n".join(lines)


def spend_snapshot(days: int = 30) -> str:
    """Last-N-day spend categorized by Plaid primary category. Reads the cached
    transactions and bucketizes on the fly."""
    cache_file = _finance_cache_path()
    if not cache_file.exists():
        return ""
    try:
        state = json.loads(cache_file.read_text())
    except (json.JSONDecodeError, OSError):
        return ""

    txns_entry = state.get(f"transactions:{days}") or state.get("transactions:30") or {}
    txns_data = txns_entry.get("data")
    if isinstance(txns_data, list):
        txns = txns_data
    elif isinstance(txns_data, dict):
        txns = txns_data.get("transactions") or []
    else:
        txns = []
    if not isinstance(txns, list):
        txns = []
    if not txns:
        return f"LAST {days}D SPEND: (no transactions in cache)"

    by_primary: dict[str, float] = {}
    total_out = 0.0
    excluded = ("LOAN_PAYMENTS", "TRANSFER_IN", "TRANSFER_OUT", "INCOME", "BANK_FEES_OVERDRAFT")
    for t in txns:
        amt = t.get("amount")
        if amt is None or amt <= 0:
            continue
        cat = t.get("category_detailed") or t.get("category") or "UNKNOWN"
        if any(cat.startswith(p) for p in excluded):
            continue
        primary = cat.split("_")[0] if cat else "UNKNOWN"
        if cat.startswith("FOOD_AND_DRINK"):
            primary = "FOOD_AND_DRINK_" + cat.split("_", 3)[3] if len(cat.split("_")) > 3 else "FOOD_AND_DRINK"
        elif cat.startswith("RENT_AND_UTILITIES"):
            primary = "RENT_AND_UTILITIES"
        elif cat.startswith("TRANSPORTATION"):
            primary = "TRANSPORTATION"
        elif cat.startswith("GENERAL_MERCHANDISE"):
            primary = "GENERAL_MERCHANDISE"
        elif cat.startswith("GENERAL_SERVICES"):
            primary = "GENERAL_SERVICES"
        elif cat.startswith("ENTERTAINMENT"):
            primary = "ENTERTAINMENT"
        elif cat.startswith("TRAVEL"):
            primary = "TRAVEL"
        else:
            primary = cat.split("_")[0]
        by_primary[primary] = by_primary.get(primary, 0.0) + float(amt)
        total_out += float(amt)

    rows = sorted(by_primary.items(), key=lambda kv: -kv[1])
    lines = [f"LAST {days}D SPEND (Plaid categories, transfers/income/payments excluded):"]
    lines.append(f"  Total: {_money_c(total_out)}")
    for cat, amt in rows[:12]:
        lines.append(f"  - {cat}: {_money_c(amt)}")
    return "\n".join(lines)


def _read_finance_cache_raw() -> tuple[dict, dict]:
    """Direct read of the finance cache file — sync, no Plaid call. Returns
    (balances_response, transactions_response) ready for budget_engine.compute."""
    cache_file = _finance_cache_path()
    if not cache_file.exists():
        return {"items": []}, {"transactions": []}
    try:
        state = json.loads(cache_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {"items": []}, {"transactions": []}
    bal = (state.get("balances") or {}).get("data") or {"items": []}
    txn_entry = state.get("transactions:30") or {}
    txn_data = txn_entry.get("data")
    if isinstance(txn_data, list):
        txn_data = {"transactions": txn_data}
    elif not isinstance(txn_data, dict):
        txn_data = {"transactions": []}
    return bal, txn_data


# ============================================================================ #
# Commute + location snapshots
# ============================================================================ #


class FinanceContextProvider(AppPersona):
    key = "finance"

    def live_context(self) -> str:
        return finance_snapshot() + "\n\n" + spend_snapshot()
