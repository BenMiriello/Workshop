"""The finance app's Store adapter: type "Finance", the transaction cache.

Moved out of `services/stores.py`, which held it in a module-level
`_ADAPTERS` tuple beside the platform's own. It is contributed through
`AppSpec.store_adapters` now, so the Store protocol is reachable by an app
without the protocol carrying a line about which apps exist.
"""
from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import Any

from services.stores import Store, StoreAdapter


class FinanceAdapter(StoreAdapter):
    """Type "Finance" — the transaction cache, and the reason this protocol is not
    a notes feature in a costume.

    The alternative today is binary: either a bill-triage worker gets the whole
    file, or it cannot answer a question the operator wants answered. "Filter to
    the relevant fields" is not a nicety here, it is the only thing that makes the
    capability grantable at all.

    There is no `read` in the Finance policy's verbs_max, so a raw transaction row
    is unreachable by construction: a caller gets the policy's declared field
    projection or nothing. The search leak rule holds trivially as a consequence —
    the free-text description is simply not a projectable field."""

    type = "Finance"

    def selectors(self) -> "list[str]":
        return ["query", "month", "limit"]

    def _transactions(self, store: Store) -> "list[dict[str, Any]]":
        src = Path(store.path)
        if not src.exists():
            return []
        try:
            doc = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        rows: "list[dict[str, Any]]" = []
        # finance-cache.json: {"transactions:<days>": {"ts", "data": {"transactions": [...]}}}
        for key, entry in (doc.items() if isinstance(doc, dict) else []):
            if not str(key).startswith("transactions") or not isinstance(entry, dict):
                continue
            data = entry.get("data")
            if isinstance(data, dict) and isinstance(data.get("transactions"), builtins.list):
                rows.extend(t for t in data["transactions"] if isinstance(t, dict))
        return rows

    def resolve(self, store, path, selector, verbs, verb):
        rows = self._transactions(store)
        query = str(selector.get("query") or "").lower().strip()
        month = str(selector.get("month") or "").strip()
        if verb == "list":
            match = rows
        else:
            match = [
                t for t in rows
                if (not query or query in f"{t.get('merchant_name') or ''} "
                                          f"{t.get('name') or ''}".lower())
                and (not month or str(t.get("date") or "").startswith(month))
            ]
        limit = selector.get("limit")
        if isinstance(limit, int) and limit > 0:
            match = match[:limit]
        return match
