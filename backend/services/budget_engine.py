"""Budget computation engine.

Pure functions. Takes the editable plan (budget_store) plus live Plaid
balances and transactions, and produces everything the UI renders:

  - debts/savings with live balances merged in + goal pacing (required vs planned)
  - the monthly allocation waterfall (income -> necessities -> debt -> savings
    -> variable targets -> leftover/shortfall) and safe-to-spend
  - per-variable-category actual spend (this window) vs target
  - the same numbers halved for the per-paycheck view

No I/O, no Plaid calls — the route fetches and passes data in.
"""
from __future__ import annotations

from datetime import date, datetime


def _months_until(date_str: str | None) -> float | None:
    """Whole-ish months from today to date_str (YYYY-MM-DD). Floored at ~0.3
    so a near/past deadline yields a large-but-finite required monthly rather
    than a divide-by-zero."""
    if not date_str:
        return None
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None
    today = date.today()
    months = (target.year - today.year) * 12 + (target.month - today.month)
    months += (target.day - today.day) / 30.0
    return max(months, 0.3)


def _mask_to_current(balances: dict) -> dict[str, float]:
    """mask -> current balance. For credit accounts `current` is the amount
    owed (what you pay down); for depository it's the cash balance."""
    out: dict[str, float] = {}
    for item in balances.get("items", []) or []:
        for acct in item.get("accounts", []) or []:
            mask = acct.get("mask")
            cur = (acct.get("balance") or {}).get("current")
            if mask and cur is not None:
                out[str(mask)] = float(cur)
    return out


# Plaid detailed/primary categories that are internal money movement, not
# real spend — excluded from variable-category actuals so credit-card payments
# and transfers between your own accounts don't double-count.
_EXCLUDED_PREFIXES = (
    "LOAN_PAYMENTS", "TRANSFER_IN", "TRANSFER_OUT", "INCOME",
    "BANK_FEES_OVERDRAFT",
)


def _is_excluded(cat: str) -> bool:
    return any(cat.startswith(p) for p in _EXCLUDED_PREFIXES)


def _spend_by_category(
    plan: dict, txns: list[dict], overrides: dict | None = None
) -> tuple[dict[str, float], float, float]:
    """Returns (per-bucket actual spend, necessities actual, unclassified spend).
    Only positive amounts (outflows) count. Detailed category preferred, with
    primary as fallback.

    `overrides` ({txn_id: {bucket, ...}}) is consulted BEFORE Plaid's PFC rules.
    An override `bucket` of "exclude" drops the txn (internal transfer / not real
    spend); "necessities" (or any necessity id) counts toward necessities; a
    variable-category id counts toward that bucket. This is how the daily Sonnet
    categorizer corrects mislabels and resolves transfers (e.g. a Venmo that is
    actually rent) that the rule path would otherwise exclude or mis-bucket."""
    overrides = overrides or {}
    buckets = {v["id"]: 0.0 for v in plan.get("variable", [])}
    matchers = [(v["id"], v.get("match", [])) for v in plan.get("variable", [])]
    necessity_ids = {n.get("id") for n in plan.get("necessities", [])}
    necessities_actual = 0.0
    unclassified = 0.0

    for t in txns:
        amt = t.get("amount")
        if amt is None or amt <= 0:  # <=0 is inflow/refund — ignore for spend
            continue

        ov = overrides.get(t.get("id"))
        if ov:
            bucket = ov.get("bucket")
            if bucket == "exclude":
                continue
            if bucket in buckets:
                buckets[bucket] += amt
            elif bucket == "necessities" or bucket in necessity_ids:
                necessities_actual += amt
            else:
                unclassified += amt
            continue

        cat = t.get("category_detailed") or t.get("category") or ""
        if _is_excluded(cat):
            continue
        if cat.startswith("RENT_AND_UTILITIES") and not cat.startswith("RENT_AND_UTILITIES_INTERNET"):
            necessities_actual += amt
            continue
        placed = False
        for bid, subs in matchers:
            if any(s in cat for s in subs):
                buckets[bid] += amt
                placed = True
                break
        if not placed:
            unclassified += amt
    return buckets, necessities_actual, unclassified


def _debt_view(plan: dict, mask_cur: dict[str, float]) -> list[dict]:
    out = []
    for d in plan.get("debts", []):
        bal = d.get("balance", 0) or 0
        mask = d.get("account_mask")
        if mask and str(mask) in mask_cur:
            bal = mask_cur[str(mask)]
        planned = d.get("planned_monthly", 0) or 0
        required = None
        months = None
        if d.get("strategy") == "clear_by_promo":
            months = _months_until(d.get("promo_until"))
            if months:
                required = round(bal / months, 2)
        on_pace = None
        if required is not None:
            on_pace = planned >= required
        out.append({
            "id": d["id"], "name": d["name"], "priority": d.get("priority", 99),
            "balance": round(bal, 2), "apr": d.get("apr"),
            "promo_until": d.get("promo_until"), "strategy": d.get("strategy"),
            "planned_monthly": planned, "required_monthly": required,
            "months_remaining": round(months, 1) if months else None,
            "on_pace": on_pace, "note": d.get("note", ""),
            "account_mask": mask,
        })
    out.sort(key=lambda x: x["priority"])
    return out


def _savings_view(plan: dict, mask_cur: dict[str, float]) -> list[dict]:
    out = []
    for s in plan.get("savings", []):
        cur = s.get("current", 0) or 0
        mask = s.get("account_mask")
        if mask and str(mask) in mask_cur:
            cur = mask_cur[str(mask)]
        target = s.get("target", 0) or 0
        planned = s.get("planned_monthly", 0) or 0
        months = _months_until(s.get("target_date"))
        required = None
        if months and target > cur:
            required = round((target - cur) / months, 2)
        progress = round(cur / target, 4) if target else None
        on_pace = None
        if required is not None:
            on_pace = planned >= required
        elif target:
            on_pace = planned > 0 or cur >= target
        out.append({
            "id": s["id"], "name": s["name"], "priority": s.get("priority", 99),
            "term": s.get("term"), "target": target, "current": round(cur, 2),
            "target_date": s.get("target_date"), "planned_monthly": planned,
            "required_monthly": required, "months_remaining": round(months, 1) if months else None,
            "progress": progress, "on_pace": on_pace, "note": s.get("note", ""),
            "account_mask": mask,
        })
    out.sort(key=lambda x: x["priority"])
    return out


def compute(plan: dict, balances: dict, txns_resp: dict, window_days: int = 30, overrides: dict | None = None) -> dict:
    mask_cur = _mask_to_current(balances)
    txns = txns_resp.get("transactions", []) or []

    debts = _debt_view(plan, mask_cur)
    savings = _savings_view(plan, mask_cur)
    spend, necessities_actual, unclassified = _spend_by_category(plan, txns, overrides)

    income = float(plan.get("income", {}).get("monthly", 0) or 0)
    necessities_planned = sum(float(n.get("monthly", 0) or 0) for n in plan.get("necessities", []))
    debt_planned = sum(d["planned_monthly"] for d in debts)
    savings_planned = sum(s["planned_monthly"] for s in savings)
    variable_target = sum(float(v.get("target", 0) or 0) for v in plan.get("variable", []))

    committed = necessities_planned + debt_planned + savings_planned
    discretionary = income - committed          # safe-to-spend on variable+fun
    leftover = discretionary - variable_target  # truly unallocated / extra
    shortfall = committed + variable_target > income

    variable = []
    for v in plan.get("variable", []):
        actual = round(spend.get(v["id"], 0.0), 2)
        target = float(v.get("target", 0) or 0)
        variable.append({
            "id": v["id"], "name": v["name"], "target": target, "actual": actual,
            "over": target > 0 and actual > target,
            "pct": round(actual / target, 4) if target else None,
        })

    waterfall = [
        {"key": "necessities", "label": "Necessities", "amount": round(necessities_planned, 2)},
        {"key": "debt", "label": "Debt payoff", "amount": round(debt_planned, 2)},
        {"key": "savings", "label": "Savings", "amount": round(savings_planned, 2)},
        {"key": "variable", "label": "Variable spend", "amount": round(variable_target, 2)},
        {"key": "leftover", "label": "Leftover" if leftover >= 0 else "Shortfall",
         "amount": round(leftover, 2)},
    ]

    return {
        "income": plan.get("income", {}),
        "debts": debts,
        "savings": savings,
        "variable": variable,
        "necessities": plan.get("necessities", []),
        "actuals": {
            "necessities": round(necessities_actual, 2),
            "unclassified": round(unclassified, 2),
            "window_days": window_days,
        },
        "summary": {
            "income": round(income, 2),
            "necessities_planned": round(necessities_planned, 2),
            "debt_planned": round(debt_planned, 2),
            "savings_planned": round(savings_planned, 2),
            "variable_target": round(variable_target, 2),
            "committed": round(committed, 2),
            "discretionary": round(discretionary, 2),
            "leftover": round(leftover, 2),
            "shortfall": shortfall,
            "per_paycheck": plan.get("income", {}).get("per_paycheck"),
            "paychecks_per_month": plan.get("income", {}).get("paychecks_per_month", 2),
        },
        "waterfall": waterfall,
    }
