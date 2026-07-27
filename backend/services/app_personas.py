"""Built-in app chat personas — the app-owned half of the chat surface.

Each built-in app that has a chat assistant defines its ContextProvider here
(persona, live context, tool policy, apply-actions) plus the snapshot helpers it
needs. `chat_context` owns only the PLATFORM chat plumbing (the ContextProvider
base, the general + new-project + project/build providers, the role preamble,
dispatch); it hardcodes NO app. These personas are wired into the chat surface
through each app's AppSpec (`app_registry`: `chat_provider` / `chat_snapshot`),
so a persona appears only when its app is installed and adding a new app's
assistant never edits `chat_context`. See docs/apps-are-pluggable.md.

Import-safe on any instance: app services (budget_store, budget_engine, location)
are imported lazily inside methods, so defining these classes never requires an
app's code to be present — an absent app is simply never registered.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from services import llm_router as _llm
from services.chat_context import ContextProvider

log = logging.getLogger("workshop.app_personas")


def _runtime_dir() -> Path:
    """The instance .runtime dir (gitignored per-instance state), home-relative by
    default so nothing is pinned to one operator's checkout path."""
    return Path(os.environ.get("WORKSHOP_RUNTIME", str(Path.home() / "workshop" / ".runtime")))


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
        return "LIVE BALANCES: (no finance cache — Plaid likely not warmed up yet)"
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
def commute_snapshot() -> str:
    """Latest commute decision + tick state from the commute runtime dir."""
    runtime = Path(os.environ.get("WORKSHOP_COMMUTE_RUNTIME", str(_runtime_dir() / "commute")))
    if not runtime.exists():
        return "COMMUTE: (no commute runtime dir)"
    latest_files = sorted(runtime.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    lines: list[str] = ["COMMUTE (latest snapshot):"]
    found_any = False
    for p in latest_files[:3]:
        try:
            d = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        ts = d.get("ts") or "?"
        agency = d.get("agency") or ""
        feeds = d.get("feeds") or {}
        trip_count = (feeds.get("trip_updates") or {}).get("entity_count")
        vehicle_count = (feeds.get("vehicle_positions") or {}).get("entity_count")
        lines.append(f"- {p.name} ts={ts} agency={agency} trips={trip_count} vehicles={vehicle_count}")
        found_any = True
        break
    if not found_any:
        lines.append("(no parseable snapshots)")

    sub_file = _runtime_dir() / "agent-subscriptions.json"
    if sub_file.exists():
        try:
            subs = json.loads(sub_file.read_text())
            if isinstance(subs, dict):
                real_sources = [k for k in subs if not k.startswith("_")]
                lines.append(f"  Subscribed sources: {len(real_sources)} ({', '.join(real_sources[:6])})")
        except (json.JSONDecodeError, OSError):
            pass
    return "\n".join(lines)


def location_snapshot() -> str:
    """Latest position with an explicit freshness verdict.

    The phone tracker can silently stop posting for hours, so the presence of a
    'latest' ping is NOT proof of current whereabouts. Anything older than the
    staleness window is labeled stale."""
    from datetime import datetime, timezone

    STALE_AFTER_MIN = 30  # matches commute LOCATION_STALE_AFTER_S and the UI

    try:
        from services import location as location_svc  # local import: avoid cycle
        d = location_svc.latest()
    except Exception:  # noqa: BLE001 — no owner pinned / unreadable => unknown
        d = None
    if not d:
        return ("LOCATION: unknown — no recent location ping available. Do NOT "
                "guess or imply where the operator is.")

    ts = d.get("ts")
    age_min: float | None = None
    if ts:
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_min = (datetime.now(timezone.utc) - t).total_seconds() / 60
        except (ValueError, TypeError):
            age_min = None

    if age_min is None:
        return (f"LOCATION: last ping at {ts}, age unknown — treat as possibly "
                f"stale; do not assume it is the operator's current location.")

    if age_min < 60:
        age = f"{age_min:.0f} min ago"
    elif age_min < 1440:
        age = f"{age_min / 60:.1f} h ago"
    else:
        age = f"{age_min / 1440:.1f} d ago"

    coords = f"lat {d.get('lat')}, lng {d.get('lng')}, src={d.get('source','?')}"
    if age_min > STALE_AFTER_MIN:
        return (
            f"LOCATION (STALE — last KNOWN position from {age}, NOT necessarily "
            f"where the operator is now): {coords}. The phone tracker may have "
            f"stopped updating. Do NOT state or imply this is his current "
            f"location — call it the last known fix from {age} and say live "
            f"location is unavailable."
        )
    return f"LOCATION (live, {age}): {coords}."


# ============================================================================ #
# Budget context builders + plan-patch path resolution
# ============================================================================ #
_PATCH_BLOCK = re.compile(r"```plan-patch\s*\n(.*?)```", re.DOTALL)

# Shared Plaid-lookup method heuristics appended to every persona that has the
# transaction-lookup tools. METHOD only — operator-specific facts live in the
# per-project memory store, not here.
_PLAID_LOOKUP_METHOD = (
    "\n\nLOOKUP METHOD: (1) TRANSFER ATTRIBUTION — to attribute a transfer to a "
    "person or entity, read the counterparty / source account number: an inflow "
    "from an account that is not one of the operator's own accounts came from "
    "someone else. Don't infer the source from amount or memo text alone. "
    "(2) CUMULATIVE LOOKBACK — when reconstructing a total since an event (a debt "
    "owed, deposits since a date), set the lookback window to span the entire "
    "period; never rely on the 90-day search default — pass an explicit `days` "
    "wide enough to cover from the event to today."
)


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
class BudgetContextProvider(ContextProvider):
    key = "budget"
    label = "Budget assistant"
    allowed_tools = [
        "mcp__plaid__search_transactions",
        "mcp__plaid__list_transactions",
    ]
    mcp_servers = ["plaid"]
    max_turns = 4
    default_model = _llm.TIER_SONNET

    def persona(self) -> str:
        return (
            "You are the operator's budgeting assistant, embedded inside the Workshop "
            "Budget app. The operator's model is a priority allocation waterfall: income → "
            "necessities → prioritized debt payoffs → prioritized savings buckets (emergency "
            "fund first) → variable spend targets → leftover.\n\n"
            "Each turn you receive: (1) the editable plan, (2) live Plaid account balances, "
            "(3) recent-30-day spending actuals already categorized by bucket. USE THESE. The "
            "operator's repeated correction: don't invent numbers you can readily see in the "
            "context. If they ask 'fill in realistic targets', base them on the actual recent "
            "spend totals shown to you, not made-up defaults.\n\n"
            "You can READ the live plan + balances + actuals (given below each turn) and you can "
            "EDIT the plan. To change the plan, include exactly one fenced block in your reply:\n\n"
            "```plan-patch\n"
            '{"set": [{"path": "<dotted.path>", "value": <new value>}, ...]}\n'
            "```\n\n"
            "Path rules: dotted segments. List items are addressed by their `id`. Examples:\n"
            "  income.monthly                      -> the monthly income number\n"
            "  income.per_paycheck\n"
            "  savings.emergency.target            -> emergency bucket's target\n"
            "  savings.emergency.planned_monthly\n"
            "  savings.emergency.target_date       -> 'YYYY-MM-DD' or null\n"
            "  savings.emergency.note              -> free-text note\n"
            "  debts.citi.planned_monthly          -> Citi monthly payment\n"
            "  debts.citi.balance                  -> override balance (live overrides for mask)\n"
            "  necessities.rent.monthly\n"
            "  necessities.rent.name               -> rename\n"
            "  variable.groceries.target           -> monthly groceries budget\n"
            "  variable.groceries.name             -> rename\n\n"
            "Only emit a plan-patch when the operator actually asks to change something. Always "
            "also explain the change in plain words. When only answering a question, do NOT emit a "
            "patch. Be direct and brief. No emojis.\n\n"
            "TOOLS: you have a NARROW read-only Plaid lookup tool set in this chat — ad-hoc "
            "transaction search (mcp__plaid__search_transactions) and date-range listing "
            "(mcp__plaid__list_transactions). Use them ONLY for questions the injected 30-day "
            "rollup and balances above can't answer (e.g. 'what did I spend at Whole Foods last "
            "month', 'find Comcast bills in the last 90 days'). Prefer the context that's already "
            "in front of you; reach for a lookup only when the answer truly isn't there. You also "
            "have web search + fetch for quick external lookups (a merchant, a current rate, a "
            "definition). You do NOT have Read, Bash, Gmail, or file edits. If asked for something "
            "outside plan edits, these Plaid lookups, and web search (e.g. 'check my emails for the "
            "Citi statement'), say plainly you can't and point the operator to where to find it. The "
            "authoritative capability statement is in the preamble above this persona."
            + _PLAID_LOOKUP_METHOD
        )

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
            return reply, ["(could not parse a plan edit — left the plan unchanged)"]

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


class FinanceContextProvider(ContextProvider):
    """Read-only assistant for the Accounts/Finance page."""
    key = "finance"
    label = "Accounts assistant"
    allowed_tools = [
        "mcp__plaid__search_transactions",
        "mcp__plaid__list_transactions",
        "mcp__plaid__get_holdings",
        "mcp__plaid__list_investment_transactions",
    ]
    mcp_servers = ["plaid"]
    max_turns = 4

    def persona(self) -> str:
        return (
            "You are the operator's finance assistant, embedded inside the Workshop "
            "Accounts page. You can answer questions about live balances, recent transactions, "
            "and spending patterns — using the snapshot supplied each turn. You CANNOT edit "
            "anything on this page (no plan to mutate); for budget changes, the operator opens "
            "the Budget app. Be direct, brief. No emojis. If asked to do something that requires "
            "writes, say plainly that this view is read-only and point them at the right app."
            + _PLAID_LOOKUP_METHOD
        )

    def live_context(self) -> str:
        return finance_snapshot() + "\n\n" + spend_snapshot()


class CommuteContextProvider(ContextProvider):
    key = "commute"
    label = "Commute assistant"

    def persona(self) -> str:
        return (
            "You are the operator's commute assistant, embedded inside the Workshop "
            "Commute app. You see the latest train schedule decision, recent location ping, "
            "and the brief-tracking state. Answer questions about timing, what the alerter "
            "is doing, and when the next decision will fire. Be direct, brief. No emojis. "
            "Read-only — you can't change schedules or send alerts; explain how to do those "
            "in the operator's terminal if asked."
        )

    def live_context(self) -> str:
        return commute_snapshot() + "\n\n" + location_snapshot()


class LocationContextProvider(ContextProvider):
    key = "location"
    label = "Location assistant"

    def persona(self) -> str:
        return (
            "You are the operator's location assistant, embedded inside the Workshop "
            "Location app. You see the most recent OwnTracks ping (lat/lng/ts) and can answer "
            "questions about where the operator was last seen and how stale that signal is. "
            "Read-only. Be direct, brief. No emojis."
        )

    def live_context(self) -> str:
        return location_snapshot()
