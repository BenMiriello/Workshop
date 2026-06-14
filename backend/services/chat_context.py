"""Context providers — the pluggable bridge between the generalized chat core
and a specific app.

A provider answers three questions for a given chat thread:
  1. persona()        — who the assistant is + how it should behave / its powers
  2. live_context()   — a fresh snapshot of the app's state, injected each turn
  3. apply_actions()  — parse any structured directives the model emitted in its
                        reply, mutate app state, and return human-readable notes

Register a provider per app by adding it to PROVIDERS. The chat route stays
app-agnostic; everything app-specific lives behind this interface. Adding chat
to a new app = write one provider + drop the <ChatDock contextKey="..."> in.

Per-provider context is bounded — Plaid balances + last-30d-summary + computed
budget go in for budget chat; latest commute decision for commute chat; etc.
We never dump raw transaction lists (too large + redundant); the engine has
already categorized them into actuals per bucket.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger("workshop.chat_context")

# The three pickable models surfaced in the composer's model selector, full ids.
# Sourced from llm_router so they never drift from the actual call tiers.
from services import llm_router as _llm  # noqa: E402

PICKABLE_MODELS: list[str] = [
    _llm.TIER_OPUS,    # claude-opus-4-8
    _llm.TIER_SONNET,  # claude-sonnet-4-6
    _llm.TIER_HAIKU,   # claude-haiku-4-5-20251001
]

# Short tier-name aliases accepted from the API alongside the full ids.
MODEL_ALIASES: dict[str, str] = {
    "opus": _llm.TIER_OPUS,
    "sonnet": _llm.TIER_SONNET,
    "haiku": _llm.TIER_HAIKU,
}


def normalize_model(value: str | None) -> str | None:
    """Map an incoming model selector value (short tier name OR full id) to a
    full, pickable model id. Returns None for blank input; raises ValueError if
    the value isn't one of the three pickable models."""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if v in MODEL_ALIASES:
        return MODEL_ALIASES[v]
    if v in PICKABLE_MODELS:
        return v
    raise ValueError(
        f"unknown model {value!r}; pick one of {PICKABLE_MODELS} "
        f"or a tier name {list(MODEL_ALIASES)}"
    )


# ---------------------------------------------------------------- role preamble


def role_preamble(
    *,
    tools_active: bool,
    tool_summary: str = "",
    cwd: str | None = None,
) -> str:
    """Platform-owned layer-1 system facts, generated from the ACTUAL run config
    (tools_active is computed in routes/chat.py from the same branch that decides
    tools_disabled/allowed_tools, so the preamble can never drift from reality).

    tools_active=False -> a tools-off safety preamble (no actions, no promises).
    tools_active=True  -> the real toolset + working dir + egress facts.
    """
    if not tools_active:
        return (
            "=== YOUR CAPABILITIES (read this first) ===\n"
            "You have NO tools in this session: you cannot run commands, fetch "
            "data, edit files, browse the web, or take any action. Do not claim "
            "you will do something or that you did it. Answer from the provided "
            "context only; if an action is needed, tell the operator the exact "
            "steps to take or offer to draft the text for them."
        )
    where = cwd or "the working directory"
    tools = tool_summary or (
        "full Claude Code toolset (Read, Write, Edit, Bash, Grep, Glob, and the "
        "rest)"
    )
    return (
        "=== YOUR CAPABILITIES (read this first) ===\n"
        f"You have {tools}, operating in {where}. You can read and edit files, "
        "run commands, and search the project.\n\n"
        "NETWORK EGRESS IS RESTRICTED: external network access (git clone, "
        "npm install, curl/wget to public hosts like github.com or "
        "registry.npmjs.org) is BLOCKED in this environment. Only loopback "
        "(127.0.0.1) and the tailnet are reachable. If a task needs an external "
        "fetch, do NOT promise it and do NOT pretend it succeeded: surface that "
        "it is blocked and ask the operator to run it in their own terminal or "
        "to approve the host."
    )


# ---------------------------------------------------------------- base / general


class ContextProvider:
    key = "general"
    label = "Clawd"
    # Per-provider tool policy for the chat surface.
    #   allowed_tools : list of MCP tool names ("mcp__plaid__search_transactions")
    #                   the model may call. Empty = tools fully off (single-turn
    #                   text Q&A; the call_cloud path pins max_turns=1).
    #   mcp_servers   : which MCP server specs to spawn in the chat subprocess.
    #                   Must be a subset of llm_router._MCP_SERVER_SPECS keys.
    #   max_turns     : agentic budget when tools are allowed. 1 tool call + 1
    #                   synthesis turn is the floor (2); 4 gives headroom for a
    #                   retry or follow-up lookup. Ignored when allowed_tools is
    #                   empty.
    # Defaults below are deliberately empty — `general` is text-only because
    # cross-cutting chat without a clear data domain doesn't justify the surface.
    allowed_tools: list[str] = []
    mcp_servers: list[str] = []
    max_turns: int = 4
    # Role surfaced to the UI: "assistant" for data/app chats, "builder" for
    # project:/build: providers. Drives the composer label + default model.
    role: str = "assistant"
    # Provider default model (full id). Resolution order at call time is:
    # explicit body.model > thread.model > this default. Assistants default to
    # the fast tier; builders override to a strong tier (see subclasses).
    default_model: str = _llm.TIER_HAIKU

    def persona(self) -> str:
        return (
            "You are Clawd, the operator's personal assistant inside the Workshop app. "
            "The operator is benmiriello — a senior engineer who dislikes padding. Be direct "
            "and brief. Use short paragraphs or tight bullet lists. No emojis.\n\n"
            "You have access to a snapshot of the operator's connected accounts (via Plaid), "
            "live commute state, and current budget plan — read what's given to you each turn "
            "before answering, and DON'T make up numbers when real ones are right there."
        )

    def live_context(self) -> str:
        # General assistant gets a broad system snapshot so it can answer
        # cross-cutting questions ("how much in savings", "next train", etc.)
        # without the operator having to switch tabs.
        parts: list[str] = []
        try:
            parts.append(_finance_snapshot())
        except Exception as e:
            log.warning("general live_context: finance snapshot failed: %s", e)
        try:
            parts.append(_commute_snapshot())
        except Exception as e:
            log.warning("general live_context: commute snapshot failed: %s", e)
        try:
            parts.append(_location_snapshot())
        except Exception as e:
            log.warning("general live_context: location snapshot failed: %s", e)
        return "\n\n".join(p for p in parts if p)

    def apply_actions(self, reply: str) -> tuple[str, list[str]]:
        """Returns (cleaned_reply, notes). Default: nothing to apply."""
        return reply, []

    def custom_prompt(self) -> str:
        """User-owned free-text system prompt (layer 3), appended after the
        platform context and BEFORE the conversation. The platform safety
        preamble (layer 1) always precedes this and is never overridden by it.
        Default empty — the seam exists; no UI is built here yet."""
        return ""


# ---------------------------------------------------------------- budget provider

_PATCH_BLOCK = re.compile(r"```plan-patch\s*\n(.*?)```", re.DOTALL)


class BudgetContextProvider(ContextProvider):
    key = "budget"
    label = "Budget assistant"
    # Budget chat gets read-only Plaid lookups: ad-hoc transaction search +
    # date-range listing for questions the cached 30-day rollup can't answer
    # ("what did I spend at Whole Foods last month", "find Comcast bills in
    # the last 90 days"). NO balance fetch (already in context), NO writes,
    # NO refresh (the finance cache warms on its own cadence).
    allowed_tools = [
        "mcp__plaid__search_transactions",
        "mcp__plaid__list_transactions",
    ]
    mcp_servers = ["plaid"]
    max_turns = 4
    # Budget chat keeps a stronger default — plan-patch path generation wants
    # careful reasoning (operator can still drop to haiku via the picker).
    default_model = _llm.TIER_SONNET

    def persona(self) -> str:
        return (
            "You are Clawd, the operator's budgeting assistant, embedded inside the Workshop "
            "Budget app. The operator's model is a priority allocation waterfall: income → "
            "necessities → prioritized debt payoffs → prioritized savings buckets (emergency "
            "fund first) → variable spend targets → leftover.\n\n"
            "Each turn you receive: (1) the editable plan, (2) live Plaid account balances, "
            "(3) recent-30-day spending actuals already categorized by bucket. USE THESE. The "
            "operator's repeated correction: don't invent numbers you can readily see in the "
            "context. If they ask 'fill in realistic targets', base them on the actual recent "
            "spend totals shown to you, not Mountain-View defaults.\n\n"
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
            "IMPORTANT: You do NOT have tool access in this chat surface — no Read, Bash, Gmail, "
            "Plaid, etc. You only have what's in the context below. If the operator asks you to "
            "look something up (e.g. 'check my emails for the Citi statement'), don't pretend to "
            "go fetch it — say plainly that the chat surface is text-only and the data isn't in "
            "the current context, then suggest where they can find it themselves or which "
            "Workshop page to open."
        )

    def live_context(self) -> str:
        # Cheap: plan + live snapshot, computed budget summary, no raw txn dump.
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

        # Strip the machine block from what the operator sees — the note covers it.
        cleaned = _PATCH_BLOCK.sub("", reply).strip()
        return cleaned, notes


# ---------------------------------------------------------------- finance provider


class FinanceContextProvider(ContextProvider):
    """Read-only assistant for the Accounts/Finance page. Knows balances +
    recent spend without the budget-plan editing surface."""
    key = "finance"
    label = "Accounts assistant"
    # Finance chat = strictly read-only Plaid surface. Same transaction
    # lookup as budget, plus holdings/investments since this page also
    # surfaces investment accounts. No balance fetch (cached snapshot is
    # injected each turn; refresh has its own cadence).
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
            "You are Clawd, the operator's finance assistant, embedded inside the Workshop "
            "Accounts page. You can answer questions about live balances, recent transactions, "
            "and spending patterns — using the snapshot supplied each turn. You CANNOT edit "
            "anything on this page (no plan to mutate); for budget changes, the operator opens "
            "the Budget app. Be direct, brief. No emojis. If asked to do something that requires "
            "writes, say plainly that this view is read-only and point them at the right app."
        )

    def live_context(self) -> str:
        return _finance_snapshot() + "\n\n" + _spend_snapshot()


# ---------------------------------------------------------------- commute provider


class CommuteContextProvider(ContextProvider):
    key = "commute"
    label = "Commute assistant"

    def persona(self) -> str:
        return (
            "You are Clawd, the operator's commute assistant, embedded inside the Workshop "
            "Commute app. You see the latest train schedule decision, recent location ping, "
            "and the brief-tracking state. Answer questions about timing, what the alerter "
            "is doing, and when the next decision will fire. Be direct, brief. No emojis. "
            "Read-only — you can't change schedules or send alerts; explain how to do those "
            "in the operator's terminal if asked."
        )

    def live_context(self) -> str:
        return _commute_snapshot() + "\n\n" + _location_snapshot()


# ---------------------------------------------------------------- location provider


class LocationContextProvider(ContextProvider):
    key = "location"
    label = "Location assistant"

    def persona(self) -> str:
        return (
            "You are Clawd, the operator's location assistant, embedded inside the Workshop "
            "Location app. You see the most recent OwnTracks ping (lat/lng/ts) and can answer "
            "questions about where the operator was last seen and how stale that signal is. "
            "Read-only. Be direct, brief. No emojis."
        )

    def live_context(self) -> str:
        return _location_snapshot()


# ============================================================================
# Live snapshot helpers — each returns a compact, plain-text block. None of
# these block long: balances come from the finance cache (<5ms hot), commute
# decisions are tiny JSON, location is a single file. They tolerate missing
# files / Plaid not connected gracefully.
# ============================================================================


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


def _finance_snapshot() -> str:
    """Compact balances summary from the finance cache. Doesn't trigger a Plaid
    roundtrip — only reads the already-warm cache file."""
    from pathlib import Path
    import os

    cache_file = Path(os.environ.get(
        "WORKSHOP_FINANCE_CACHE",
        "/home/clawd-a/workshop/.runtime/finance-cache.json",
    ))
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
                # For credit, `current` is the amount owed. Limit + available is informative.
                lines.append(
                    f"- {name} ··{mask} ({subtype}): owed {_money_c(cur)}"
                    + (f", limit {_money(limit)}" if limit else "")
                )
            else:
                lines.append(f"- {name} ··{mask} ({subtype}): {_money_c(cur)}")
    return "\n".join(lines)


def _spend_snapshot(days: int = 30) -> str:
    """Last-N-day spend categorized by Plaid primary category. Reads the cached
    transactions and bucketizes on the fly — small enough computation that the
    extra ~5ms beats trying to share state with budget_engine."""
    from pathlib import Path
    import os

    cache_file = Path(os.environ.get(
        "WORKSHOP_FINANCE_CACHE",
        "/home/clawd-a/workshop/.runtime/finance-cache.json",
    ))
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
        # Primary = first segment of detailed cat
        primary = cat.split("_")[0] if cat else "UNKNOWN"
        # For FOOD_AND_DRINK keep two segments so groceries vs eating-out are distinguishable
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

    # Sort largest first
    rows = sorted(by_primary.items(), key=lambda kv: -kv[1])
    lines = [f"LAST {days}D SPEND (Plaid categories, transfers/income/payments excluded):"]
    lines.append(f"  Total: {_money_c(total_out)}")
    for cat, amt in rows[:12]:  # cap at top 12 — long-tail is noise
        lines.append(f"  - {cat}: {_money_c(amt)}")
    return "\n".join(lines)


def _budget_full_context() -> str:
    """Plan + balances + computed actuals + per-bucket pacing. The richest
    context we ship to any provider — the budget chat needs all of it to
    answer questions or fill realistic targets."""
    try:
        from services import budget_store, budget_engine

        plan = budget_store.load_plan()
        # Reuse the cached balances + txns rather than re-fetching from Plaid.
        # The engine wants the full transactions-response dict (it pulls
        # ["transactions"] internally), so pass that through verbatim.
        balances, txns_resp = _read_finance_cache_raw()
        computed = budget_engine.compute(plan, balances, txns_resp, window_days=30)
    except Exception as e:
        log.warning("budget context build failed: %s", e, exc_info=True)
        return build_budget_context()  # fall back to plain plan dump

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

    # Live snapshot — balances first.
    out.append("")
    out.append(_finance_snapshot())

    # Computed actuals per bucket (this is the "what was actually spent" signal
    # the operator wanted — derived from real transactions, not made up).
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

    # Summary numbers — what's allocated vs leftover.
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


def _read_finance_cache_raw() -> tuple[dict, dict]:
    """Direct read of the finance cache file — sync, no Plaid call. Returns
    (balances_response, transactions_response) — both raw shapes from the
    Plaid-bridge, ready to hand to budget_engine.compute which expects dicts
    (balances has .items, txns has .transactions). Safe empty defaults so the
    caller never needs to None-check the cache."""
    from pathlib import Path
    import os

    cache_file = Path(os.environ.get(
        "WORKSHOP_FINANCE_CACHE",
        "/home/clawd-a/workshop/.runtime/finance-cache.json",
    ))
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


def _commute_snapshot() -> str:
    """Latest commute decision + tick state. The commute app writes a poll/decide
    result into .runtime/commute/ — we surface the latest one."""
    from pathlib import Path

    runtime = Path("/home/clawd-a/workshop/.runtime/commute")
    if not runtime.exists():
        return "COMMUTE: (no commute runtime dir)"
    # Latest schedule snapshot
    latest_files = sorted(
        runtime.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
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
        lines.append(
            f"- {p.name} ts={ts} agency={agency} trips={trip_count} vehicles={vehicle_count}"
        )
        found_any = True
        # First decision is enough — break after one
        break
    if not found_any:
        lines.append("(no parseable snapshots)")

    # Subscription count (informational only). The file mixes meta keys (_schema)
    # with source-keyed agent lists, so we just report the count of real source
    # entries — nothing fancy.
    sub_file = Path("/home/clawd-a/workshop/.runtime/agent-subscriptions.json")
    if sub_file.exists():
        try:
            subs = json.loads(sub_file.read_text())
            if isinstance(subs, dict):
                real_sources = [k for k in subs if not k.startswith("_")]
                lines.append(f"  Subscribed sources: {len(real_sources)} ({', '.join(real_sources[:6])})")
        except (json.JSONDecodeError, OSError):
            pass
    return "\n".join(lines)


def _location_snapshot() -> str:
    """OwnTracks-fed latest position. Just the most recent ping + how stale."""
    from datetime import datetime, timezone
    from pathlib import Path

    p = Path("/home/clawd-a/workshop/.runtime/location-latest.json")
    if not p.exists():
        return "LOCATION: (no location pings)"
    try:
        d = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return "LOCATION: (location file unreadable)"
    ts = d.get("ts")
    age_str = ""
    if ts:
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_min = (datetime.now(timezone.utc) - t).total_seconds() / 60
            if age_min < 60:
                age_str = f" ({age_min:.0f} min ago)"
            elif age_min < 1440:
                age_str = f" ({age_min/60:.1f} h ago)"
            else:
                age_str = f" ({age_min/1440:.1f} d ago)"
        except (ValueError, TypeError):
            pass
    return (
        f"LOCATION: lat {d.get('lat')}, lng {d.get('lng')}, "
        f"src={d.get('source','?')} at {ts}{age_str}"
    )


# ============================================================================
# Path resolution for plan-patch (used by BudgetContextProvider.apply_actions)
# ============================================================================


def _set_path(plan: dict, path: str, value: Any) -> tuple[bool, str]:
    """Resolve a dotted path (list items keyed by `id`) and set the leaf.
    Returns (ok, human_label)."""
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
                # setting a whole list item is unusual; require a sub-field
                return False, f"{path} (point at a field of '{seg}')"
            node = match
            trail.append(seg)
        else:
            return False, f"{path} (cannot descend into '{seg}')"
    return False, f"{path} (unresolved)"


def build_budget_context() -> str:
    """A compact, current snapshot of the editable plan. Plan-only fallback for
    when the engine isn't reachable; main path goes through _budget_full_context."""
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


# ---------------------------------------------------------------- project provider
# Build-mode chat scoped to a Projects-hub project. The provider is parameterized
# by the project slug; context_key looks like "project:<slug>". Reads PROJECT.md,
# AGENTS.md, and TODO.md as the system context every turn so the agent always has
# the brief, the rules, and the live checklist in front of it.

import os as _os
from pathlib import Path as _Path


def _projects_root() -> _Path:
    return _Path(_os.environ.get(
        "WORKSHOP_PROJECTS_ROOT_NEW",
        str(_Path.home() / "workshop" / "projects"),
    ))


class ProjectContextProvider(ContextProvider):
    """Build-mode provider for a single Project. Routes call_cloud with cwd
    set to the project directory and the full default agentic toolset enabled
    (Read/Write/Edit/Bash/Grep/MultiEdit/Glob) so the chat behaves like a
    terminal-resident `claude` session inside the project."""

    label = "Build · project"
    # Build mode runs the default agentic path in call_cloud (NOT the
    # tools_disabled or allowed_tools narrow paths). Leaving these empty
    # signals routes/chat.py to switch into the project-build branch.
    allowed_tools = []
    mcp_servers = []
    # Vibe-coding wants real headroom; tasks routinely span 20+ tool calls.
    max_turns = 60
    role = "builder"
    # Builders default to a strong model for codegen quality; opus is selectable.
    default_model = _llm.TIER_SONNET

    def __init__(self, slug: str):
        self.slug = slug
        self.key = f"project:{slug}"
        self.dir = _projects_root() / slug

    def _read(self, name: str, default: str = "") -> str:
        p = self.dir / name
        try:
            return p.read_text() if p.exists() else default
        except Exception:
            return default

    def persona(self) -> str:
        rules = self._read("AGENTS.md")
        return (
            "You are the build agent for the operator's Workshop Project "
            f"'{self.slug}'. You have full Claude Code tool access inside the "
            f"project directory ({self.dir}). The operator is benmiriello — a "
            "senior engineer who dislikes padding. Be direct and brief.\n\n"
            "Always read PROJECT.md FIRST to ground your work in the current "
            "goal and constraints, then act. When you finish a task, update "
            "TODO.md (check items off, add follow-ups). When the operator gives "
            "a non-trivial direction, append a one-line decision note to "
            "PROJECT.md.\n\n"
            f"Project-local agent rules:\n\n{rules or '(none)'}"
        )

    def live_context(self) -> str:
        brief = self._read("PROJECT.md")
        todo = self._read("TODO.md")
        parts = []
        if brief:
            parts.append("=== PROJECT.md (brief) ===\n" + brief)
        if todo:
            parts.append("=== TODO.md ===\n" + todo)
        return "\n\n".join(parts)


# ---------------------------------------------------------------- build-agent provider
# Build agents are Claude Code coding sessions surfaced under the Agents tab,
# distinct from Projects. context_key looks like "build:<slug>". Same Build-mode
# behavior as a Project chat: full agentic toolset, cwd pinned to the agent's
# directory (which is either a Project dir for project-linked agents or a
# freestanding scratch dir).


class BuildAgentContextProvider(ContextProvider):
    label = "Build · agent"
    allowed_tools = []
    mcp_servers = []
    max_turns = 60
    role = "builder"
    default_model = _llm.TIER_SONNET

    def __init__(self, slug: str):
        # Defer build_agents import to avoid circulars at module load.
        from services import build_agents as _ba
        try:
            record = _ba.get_agent(slug)
        except KeyError:
            record = {"name": slug, "purpose": "", "cwd": str(_Path.home() / "workshop"), "project_slug": None}
        self.slug = slug
        self.key = f"build:{slug}"
        self.name = record.get("name") or slug
        self.purpose = record.get("purpose") or ""
        self.dir = _Path(record["cwd"])
        self.project_slug = record.get("project_slug")

    def _read(self, name: str, default: str = "") -> str:
        p = self.dir / name
        try:
            return p.read_text() if p.exists() else default
        except Exception:
            return default

    def persona(self) -> str:
        rules = self._read("AGENTS.md")
        return (
            f"You are the build agent '{self.name}' inside the Workshop. You have full "
            f"Claude Code tool access inside the directory {self.dir}. The operator is "
            "benmiriello — a senior engineer who dislikes padding. Be direct and brief.\n\n"
            "Always read PROJECT.md FIRST to ground your work in the current goal and "
            "constraints, then act. When you finish a task, update TODO.md (check items off, "
            "add follow-ups). When the operator gives a non-trivial direction, append a "
            "one-line decision note to PROJECT.md.\n\n"
            f"Build-agent rules:\n\n{rules or '(none)'}"
        )

    def live_context(self) -> str:
        parts = []
        brief = self._read("PROJECT.md")
        todo = self._read("TODO.md")
        if brief:
            parts.append("=== PROJECT.md (brief) ===\n" + brief)
        if todo:
            parts.append("=== TODO.md ===\n" + todo)
        return "\n\n".join(parts)


# ---------------------------------------------------------------- registry

PROVIDERS: dict[str, ContextProvider] = {
    "general": ContextProvider(),
    "budget": BudgetContextProvider(),
    "finance": FinanceContextProvider(),
    "commute": CommuteContextProvider(),
    "location": LocationContextProvider(),
}


def _project_slugs() -> list[str]:
    """Read project slugs from the projects.json registry, if present."""
    reg = _projects_root().parent / "projects.json"
    if not reg.exists():
        return []
    try:
        data = json.loads(reg.read_text())
        return sorted((data.get("projects") or {}).keys())
    except Exception:
        return []


def _build_agent_slugs() -> list[str]:
    try:
        from services import build_agents as _ba
        return sorted(_ba.slugs())
    except Exception:
        return []


def is_build_provider(provider: ContextProvider) -> bool:
    """A build provider runs the full agentic Claude Code path."""
    return isinstance(provider, (ProjectContextProvider, BuildAgentContextProvider))


def capability_for(
    provider: ContextProvider, *, enable_chat_tools: bool
) -> tuple[bool, str]:
    """SINGLE SOURCE OF TRUTH for "does this provider get tools in the actual
    run?" — used both to build the role preamble (so it can't drift) and to
    report `tools_active` from /providers.

    Mirrors the branch order in routes/chat.send_message exactly:
      - build providers always run the agentic toolset -> True
      - data providers get narrow MCP tools ONLY when ENABLE_CHAT_TOOLS is on
        AND they declare a non-empty allowed_tools list -> else tools-off.

    Returns (tools_active, tool_summary). tool_summary is human-readable text
    for the preamble's layer-1 tool list.
    """
    if is_build_provider(provider):
        return True, (
            "full Claude Code tool access (Read, Write, Edit, Bash, Grep, Glob, "
            "MultiEdit, and the rest)"
        )
    if enable_chat_tools and provider.allowed_tools:
        return True, (
            "a narrow read-only tool set: " + ", ".join(provider.allowed_tools)
        )
    return False, ""


def get_provider(context_key: str | None) -> ContextProvider:
    if context_key and context_key.startswith("project:"):
        slug = context_key.split(":", 1)[1]
        return ProjectContextProvider(slug)
    if context_key and context_key.startswith("build:"):
        slug = context_key.split(":", 1)[1]
        return BuildAgentContextProvider(slug)
    return PROVIDERS.get(context_key or "general", PROVIDERS["general"])


def _provider_entry(provider: ContextProvider, label: str | None = None) -> dict:
    """Rich /providers entry the composer relies on: key, label, role, the
    pickable models, the default model, and whether this provider actually gets
    tools in the current run config."""
    from routes.chat import ENABLE_CHAT_TOOLS

    tools_active, _summary = capability_for(provider, enable_chat_tools=ENABLE_CHAT_TOOLS)
    return {
        "key": provider.key,
        "label": label if label is not None else provider.label,
        "role": provider.role,
        "models": list(PICKABLE_MODELS),
        "default_model": provider.default_model,
        "tools_active": tools_active,
    }


def list_providers(scope: str | None = None) -> list[dict]:
    """Provider directory for the composer/picker.

    scope=None  -> the FULL list (static data providers + every discovered
                   project + build-agent provider). The Agents page needs this.
    scope=<key> -> ONLY the single matching provider entry (an in-app dock
                   passes its own contextKey and must not see unrelated
                   projects/build agents). Returns [] if the key resolves to
                   nothing valid.

    Each entry: {key, label, role, models, default_model, tools_active}.
    """
    if scope:
        provider = get_provider(scope)
        # get_provider falls back to the `general` provider for unknown keys;
        # only return an entry if the resolved key actually matches the request
        # (so a bogus scope doesn't silently expose `general`).
        if provider.key != scope and scope not in PROVIDERS:
            return []
        # Preserve the nicer dynamic labels for project:/build: keys.
        if scope.startswith("project:"):
            label = f"Build · project · {scope.split(':', 1)[1]}"
        elif scope.startswith("build:"):
            label = f"Build · agent · {scope.split(':', 1)[1]}"
        else:
            label = None
        return [_provider_entry(provider, label)]

    out = [_provider_entry(p) for p in PROVIDERS.values()]
    for slug in _project_slugs():
        out.append(_provider_entry(
            ProjectContextProvider(slug), f"Build · project · {slug}"
        ))
    for slug in _build_agent_slugs():
        out.append(_provider_entry(
            BuildAgentContextProvider(slug), f"Build · agent · {slug}"
        ))
    return out
