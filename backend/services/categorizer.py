"""Daily Sonnet transaction categorizer.

The budget engine categorizes spend deterministically from Plaid's Personal
Finance Category (PFC). That covers ~99% of spend by dollar, but two classes
slip through the rules:

  1. **Unclassified** — a real purchase whose PFC doesn't map to any bucket
     (e.g. MEDICAL_DENTAL_CARE before a bucket exists, odd merchant categories).
  2. **Transfers** — Venmo / Zelle / bank transfers get TRANSFER_* PFC codes and
     are *excluded* from spend by the rules. But some are real obligations paid
     after the fact: rent or utilities sent via Venmo, repaying Dad, etc. Rules
     can't tell which; a model reading the merchant/name can.

This pass sends ONLY those candidate transactions to Sonnet (cheap — usually a
dozen or two per window), asks it to assign each a bucket, and writes the result
to the per-transaction override store (services/budget_store). The engine reads
overrides BEFORE its PFC rules, so the judgement sticks across reloads. It is
idempotent: transactions already in the override store are skipped.

Run on a schedule (backend daily loop) or on demand (POST /budget/categorize).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from services import finance_cache, budget_store, budget_engine, llm_router
from services import finance_plaid as plaid_bridge

log = logging.getLogger("workshop.categorizer")

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _bucket_vocab(plan: dict) -> tuple[list[dict], set[str]]:
    """Allowed buckets the model may assign, plus the set of valid bucket ids."""
    options = []
    for v in plan.get("variable", []):
        options.append({"id": v["id"], "name": v.get("name", v["id"]), "kind": "variable"})
    for n in plan.get("necessities", []):
        options.append({"id": n["id"], "name": n.get("name", n["id"]), "kind": "necessity"})
    options.append({"id": "exclude", "name": "Internal transfer / not real spend", "kind": "special"})
    valid = {o["id"] for o in options} | {"necessities"}
    return options, valid


def _is_candidate(plan: dict, t: dict) -> bool:
    """True if the deterministic rules can't confidently place this txn, so the
    model should weigh in. Outflows only."""
    amt = t.get("amount")
    if amt is None or amt <= 0:
        return False
    cat = t.get("category_detailed") or t.get("category") or ""
    # Transfers/income/loan-payments are excluded by rules but may be real
    # obligations (rent/utilities/Dad via Venmo) — let the model decide.
    if budget_engine._is_excluded(cat) or cat.startswith("TRANSFER") or not cat:
        return True
    # Confidently a necessity (rent/utilities) — skip.
    if cat.startswith("RENT_AND_UTILITIES") and not cat.startswith("RENT_AND_UTILITIES_INTERNET"):
        return False
    # Matches a variable bucket — skip.
    for v in plan.get("variable", []):
        if any(s in cat for s in v.get("match", [])):
            return False
    # Falls through the rules → unclassified → candidate.
    return True


def _build_prompt(options: list[dict], candidates: list[dict]) -> str:
    opts_lines = "\n".join(f"  - {o['id']}: {o['name']} ({o['kind']})" for o in options)
    rows = [
        {
            "txn_id": t.get("id"),
            "date": t.get("date"),
            "name": t.get("name"),
            "merchant": t.get("merchant_name"),
            "amount": t.get("amount"),
            "institution": t.get("institution_name"),
            "plaid_category": t.get("category_detailed") or t.get("category"),
        }
        for t in candidates
    ]
    return (
        "You are categorizing personal bank transactions that automated rules "
        "could not confidently classify. Most are either uncategorized purchases "
        "or money transfers (Venmo/Zelle/bank) that might actually be a recurring "
        "obligation paid after the fact — rent, a utility bill, or repaying a "
        "family member.\n\n"
        "Assign each transaction to exactly ONE bucket id from this list:\n"
        f"{opts_lines}\n"
        "  - necessities: a fixed monthly necessity not matching a specific id above\n\n"
        "Rules:\n"
        "- A Venmo/Zelle/transfer that is clearly rent or a utility bill → the matching "
        "necessity id (e.g. rent, utilities).\n"
        "- A transfer that is moving your own money between your accounts, a credit-card "
        "payment, or paying off a balance → exclude.\n"
        "- A real discretionary purchase → the best-fitting variable bucket.\n"
        "- If you genuinely cannot tell, use bucket \"skip\" and it will be left "
        "unclassified. Do NOT guess wildly.\n\n"
        "Transactions:\n"
        f"{json.dumps(rows, indent=2)}\n\n"
        "Respond with ONLY a JSON array, no prose, of objects: "
        '{\"txn_id\": str, \"bucket\": str, \"reason\": str (<=12 words)}. '
        "Omit transactions you would skip, or give them bucket \"skip\"."
    )


def _parse(text: str) -> list[dict]:
    """Pull a JSON array out of the model reply, tolerating code fences/prose."""
    text = (text or "").strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    if not text.startswith("["):
        i, j = text.find("["), text.rfind("]")
        if i != -1 and j != -1 and j > i:
            text = text[i : j + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


async def run(days: int = 30, model: str | None = None) -> dict:
    """Categorize uncategorized/transfer transactions in the window and merge the
    results into the override store. Returns a summary."""
    plan = budget_store.load_plan()
    existing = budget_store.load_overrides()
    txns_resp, _ = await finance_cache.get_or_fetch(
        f"transactions:{days}", lambda: plaid_bridge.transactions(days=days)
    )
    txns = txns_resp.get("transactions", []) or []

    candidates = [
        t for t in txns
        if _is_candidate(plan, t) and t.get("id") and t["id"] not in existing
    ]
    if not candidates:
        log.info("categorizer: no new candidates in %dd window", days)
        return {"candidates": 0, "applied": 0, "skipped": 0, "overrides_total": len(existing)}

    options, valid = _bucket_vocab(plan)
    prompt = _build_prompt(options, candidates)
    model = model or llm_router.TIER_SONNET

    result = await asyncio.to_thread(
        llm_router.call_cloud,
        prompt,
        model=model,
        tools_disabled=True,
        timeout_s=180,
    )
    assignments = _parse(result.get("text", ""))

    new: dict[str, dict] = {}
    skipped = 0
    cand_ids = {t["id"] for t in candidates}
    for a in assignments:
        tid = a.get("txn_id")
        bucket = a.get("bucket")
        if tid not in cand_ids or not bucket or bucket == "skip":
            skipped += 1
            continue
        if tid in new:
            # Model emitted a duplicate row for this txn — first valid one wins;
            # ignore the rest rather than letting an arbitrary last-write decide.
            skipped += 1
            continue
        if bucket not in valid:
            log.warning("categorizer: model returned invalid bucket %r for %s", bucket, tid)
            skipped += 1
            continue
        new[tid] = {
            "bucket": bucket,
            "reason": (a.get("reason") or "")[:120],
            "source": f"sonnet:{model}",
        }

    if new:
        budget_store.merge_overrides(new)
    log.info("categorizer: %d candidates -> %d applied, %d skipped",
             len(candidates), len(new), skipped)
    return {
        "candidates": len(candidates),
        "applied": len(new),
        "skipped": skipped,
        "overrides_total": len(existing) + len(new),
        "assignments": new,
    }


def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import argparse

    ap = argparse.ArgumentParser(description="Daily Sonnet transaction categorizer")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    summary = asyncio.run(run(days=args.days))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    _main()
