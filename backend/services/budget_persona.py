"""The budget app's chat assistant, its plan context, and the plan-patch applier.

Moved out of `services/app_personas.py`. Contributed through
`AppSpec.chat_provider`.

Budget reads the FINANCE app's transaction cache, which it declares
(`AppSpec.identity.needs = ["finance.read"]`). That read is call-time and
degrades to empty when finance is not installed, so the budget assistant answers
about the plan rather than failing to build.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from services.app_persona import AppPersona
from wsplatform import paths

log = logging.getLogger("workshop.budget_persona")


def _runtime_dir() -> Path:
    from services import stores as _stores

    return _stores._runtime_root()


def _read_finance_cache_raw() -> tuple[dict, dict]:
    """Balances and transactions from the finance app, or empty when it is absent."""
    try:
        from services import finance_persona
    except Exception:
        return {}, {}
    try:
        return finance_persona._read_finance_cache_raw()
    except Exception:
        log.debug("budget: finance cache unavailable", exc_info=True)
        return {}, {}


def _money_c(x: Any) -> str:
    """Cents-precision currency. Six lines, duplicated rather than imported from
    finance: a cross-app import at call time to format a number would make the
    budget assistant's output depend on whether another app is installed."""
    try:
        return f"${float(x):,.2f}"
    except (TypeError, ValueError):
        return str(x)


_PATCH_BLOCK = re.compile(r"```plan-patch\s*\n(.*?)```", re.DOTALL)

# Shared Plaid-lookup method heuristics appended to every persona that has the
# transaction-lookup tools. METHOD only — operator-specific facts live in the
# per-project memory store, not here.
# The Plaid LOOKUP METHOD paragraph used to live here and be concatenated onto
# two personas. It now sits in `agents/apps/budget.md` and `agents/apps/finance.md`
# in full. That is a duplicated paragraph of prose, and it is the right trade: a
# persona file has to be readable on its own, and one that ended in a reference
# to a Python constant would put the operator back to reading code to find out
# what the assistant was told.


def _budget_full_context() -> str:
    """Plan + balances + computed actuals + per-bucket pacing."""
    try:
        from services import budget_store, budget_engine

        plan = budget_store.load_plan()
        balances, txns_resp = _read_finance_cache_raw()
        computed = budget_engine.compute(plan, balances, txns_resp, window_days=30)
    except Exception as e:
        log.warning("budget context build failed: %s", e, exc_info=True)
        return build_budget_context()

    out: list[str] = ["CURRENT PLAN (editable):"]
    inc = plan.get("income", {})
    out.append(
        f"- Income: {_money(inc.get('monthly'))}/mo "
        f"({_money(inc.get('per_paycheck'))} x {inc.get('paychecks_per_month')} paychecks)"
    )
    nec = [n for n in plan.get("necessities", []) if n.get("monthly")]
    if nec:
        out.append("- Necessities: " + ", ".join(
            f"{n['name']} {_money(n['monthly'])} (id:{n['id']})" for n in nec
        ))
    else:
        out.append("- Necessities: none set (rent/utilities/phone/insurance all $0)")
    for d in plan.get("debts", []):
        if d.get("balance") or d.get("planned_monthly"):
            extra = f", promo until {d['promo_until']}" if d.get("promo_until") else ""
            out.append(
                f"- Debt {d['name']} (id:{d['id']}): balance {_money(d.get('balance'))}, "
                f"paying {_money(d.get('planned_monthly'))}/mo{extra}"
            )
    for s in plan.get("savings", []):
        if s.get("target") or s.get("current") or s.get("planned_monthly"):
            out.append(
                f"- Savings {s['name']} (id:{s['id']}): {_money(s.get('current'))} / "
                f"{_money(s.get('target'))} target, saving {_money(s.get('planned_monthly'))}/mo"
            )
    var = [v for v in plan.get("variable", []) if v.get("target")]
    if var:
        out.append("- Variable targets: " + ", ".join(
            f"{v['name']} {_money(v['target'])} (id:{v['id']})" for v in var
        ))
    else:
        ids = [v["id"] for v in plan.get("variable", [])]
        out.append("- Variable spend targets: none set yet (ids: " + ", ".join(ids) + ")")

    out.append("")
    out.append(finance_snapshot())

    out.append("")
    out.append("LAST 30D ACTUAL SPEND PER BUCKET (categorized, transfers excluded):")
    for v in computed.get("variable", []):
        target_s = _money(v.get("target", 0)) if v.get("target") else "no target"
        pct = v.get("pct")
        pct_s = f" ({pct*100:.0f}%)" if isinstance(pct, (int, float)) else ""
        flag = " OVER" if v.get("over") else ""
        out.append(
            f"  - {v['name']} (id:{v['id']}): actual {_money_c(v.get('actual', 0))} / "
            f"target {target_s}{pct_s}{flag}"
        )
    actuals = computed.get("actuals", {})
    out.append(
        f"  Necessities (rent/utilities autodetected): {_money_c(actuals.get('necessities', 0))}; "
        f"unclassified spend: {_money_c(actuals.get('unclassified', 0))}"
    )

    s = computed.get("summary", {})
    out.append("")
    out.append("COMPUTED ALLOCATION (waterfall):")
    out.append(
        f"  Income {_money(s.get('income'))}/mo  -  "
        f"necessities {_money(s.get('necessities_planned'))}  -  "
        f"debt {_money(s.get('debt_planned'))}  -  "
        f"savings {_money(s.get('savings_planned'))}  -  "
        f"variable targets {_money(s.get('variable_target'))}"
    )
    out.append(
        f"  = committed {_money(s.get('committed'))}, "
        f"discretionary {_money(s.get('discretionary'))}, "
        f"leftover {_money(s.get('leftover'))}"
    )
    if s.get("shortfall"):
        out.append("  SHORTFALL: planned allocations exceed income.")
    return "\n".join(out)


def build_budget_context() -> str:
    """Compact snapshot of the editable plan. Plan-only fallback when the engine
    isn't reachable; the main path goes through _budget_full_context."""
    from services import budget_store

    plan = budget_store.load_plan()
    lines = ["CURRENT PLAN (editable):"]
    inc = plan.get("income", {})
    lines.append(
        f"- Income: {_money(inc.get('monthly'))}/mo "
        f"({_money(inc.get('per_paycheck'))} x {inc.get('paychecks_per_month')} paychecks)"
    )
    nec = [n for n in plan.get("necessities", []) if n.get("monthly")]
    if nec:
        lines.append("- Necessities: " + ", ".join(
            f"{n['name']} {_money(n['monthly'])} (id:{n['id']})" for n in nec
        ))
    else:
        lines.append("- Necessities: none set (rent/utilities/phone/insurance all $0)")
    for d in plan.get("debts", []):
        if d.get("balance") or d.get("planned_monthly"):
            extra = f", promo until {d['promo_until']}" if d.get("promo_until") else ""
            lines.append(
                f"- Debt {d['name']} (id:{d['id']}): balance {_money(d.get('balance'))}, "
                f"paying {_money(d.get('planned_monthly'))}/mo{extra}"
            )
    for s in plan.get("savings", []):
        if s.get("target") or s.get("current") or s.get("planned_monthly"):
            lines.append(
                f"- Savings {s['name']} (id:{s['id']}): {_money(s.get('current'))} / "
                f"{_money(s.get('target'))} target, saving {_money(s.get('planned_monthly'))}/mo"
            )
    var = [v for v in plan.get("variable", []) if v.get("target")]
    if var:
        lines.append("- Variable targets: " + ", ".join(
            f"{v['name']} {_money(v['target'])} (id:{v['id']})" for v in var
        ))
    else:
        lines.append("- Variable spend targets: none set yet (ids: "
                     + ", ".join(v["id"] for v in plan.get("variable", [])) + ")")
    return "\n".join(lines)


def _set_path(plan: dict, path: str, value: Any) -> tuple[bool, str]:
    """Resolve a dotted path (list items keyed by `id`) and set the leaf."""
    parts = [p for p in path.split(".") if p]
    if not parts:
        return False, f"{path} (empty path)"
    node: Any = plan
    trail: list[str] = []
    for i, seg in enumerate(parts):
        last = i == len(parts) - 1
        if isinstance(node, dict):
            if last:
                if seg not in node:
                    return False, f"{path} (no field '{seg}')"
                node[seg] = value
                return True, f"{'.'.join(trail + [seg])} = {value}"
            if seg not in node:
                return False, f"{path} (no field '{seg}')"
            node = node[seg]
            trail.append(seg)
        elif isinstance(node, list):
            match = next((it for it in node if isinstance(it, dict) and it.get("id") == seg), None)
            if match is None:
                return False, f"{path} (no item id '{seg}')"
            if last:
                return False, f"{path} (point at a field of '{seg}')"
            node = match
            trail.append(seg)
        else:
            return False, f"{path} (cannot descend into '{seg}')"
    return False, f"{path} (unresolved)"


# ============================================================================ #
# The app persona providers
# ============================================================================ #


class BudgetContextProvider(AppPersona):
    key = "budget"

    def live_context(self) -> str:
        return _budget_full_context()

    def apply_actions(self, reply: str) -> tuple[str, list[str]]:
        from services import budget_store

        notes: list[str] = []
        m = _PATCH_BLOCK.search(reply)
        if not m:
            return reply, notes

        raw = m.group(1).strip()
        try:
            patch = json.loads(raw)
            ops = patch.get("set", []) if isinstance(patch, dict) else []
        except json.JSONDecodeError:
            return reply, ["(could not parse a plan edit. The plan is unchanged)"]

        if not ops:
            cleaned = _PATCH_BLOCK.sub("", reply).strip()
            return cleaned, notes

        plan = budget_store.load_plan()
        applied: list[str] = []
        failed: list[str] = []
        for op in ops:
            path = op.get("path", "")
            value = op.get("value")
            ok, label = _set_path(plan, path, value)
            (applied if ok else failed).append(label)

        if applied:
            budget_store.save_plan(plan)
            notes.append("Updated plan: " + "; ".join(applied))
        for f in failed:
            notes.append(f"Could not apply: {f}")

        cleaned = _PATCH_BLOCK.sub("", reply).strip()
        return cleaned, notes
