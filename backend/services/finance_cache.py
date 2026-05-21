"""TTL-cached wrapper around the Plaid bridge.

Every Apps page load was firing 2 fresh Plaid API roundtrips (balances ~4s,
transactions ~1s) via a Node subprocess. With caching, hot loads return in
<5ms and we only hit Plaid on TTL expiry OR webhook-triggered invalidation.

Cache file: ~/workshop/.runtime/finance-cache.json
Schema: {"balances": {ts, data}, "transactions:30": {ts, data}, ...}
TTL: 5 minutes (configurable per-key via WORKSHOP_FINANCE_TTL_S env).

Webhook invalidation: routes/plaid_webhook.py calls invalidate() on any
TRANSACTIONS webhook so the next /finance/transactions request fetches fresh.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

CACHE_FILE = Path(os.environ.get(
    "WORKSHOP_FINANCE_CACHE",
    "/home/clawd-a/workshop/.runtime/finance-cache.json",
))
DEFAULT_TTL_S = int(os.environ.get("WORKSHOP_FINANCE_TTL_S", "300"))  # 5 min

_lock = asyncio.Lock()


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _load() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(state: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write — finance-cache is read by multiple processes (UI polling
    # + cron consumer + webhook). Write to tmp + rename.
    tmp = CACHE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(CACHE_FILE)


async def get_or_fetch(key: str, fetcher, ttl_s: int = DEFAULT_TTL_S, force_refresh: bool = False) -> tuple[dict, dict]:
    """Returns (data, meta) where meta = {cached, ts, age_s, ttl_s}.
    `fetcher` is an async callable returning the raw data dict."""
    async with _lock:
        state = _load()
        entry = state.get(key)
        now = _now()
        if not force_refresh and entry and (now - entry.get("ts", 0)) < ttl_s:
            age = now - entry["ts"]
            return entry["data"], {"cached": True, "ts": entry["ts"], "age_s": age, "ttl_s": ttl_s}
        # Cache miss / forced — fetch fresh outside the lock would be ideal,
        # but we hold to serialize concurrent first-fetchers (avoid stampede).
        data = await fetcher()
        state[key] = {"ts": now, "data": data}
        _save(state)
        return data, {"cached": False, "ts": now, "age_s": 0.0, "ttl_s": ttl_s}


def invalidate(*prefixes: str) -> int:
    """Drop cache entries whose key starts with any of the given prefixes.
    Returns number of entries removed. Call with `("transactions",)` on webhook."""
    state = _load()
    before = len(state)
    if prefixes:
        state = {k: v for k, v in state.items() if not any(k == p or k.startswith(p + ":") for p in prefixes)}
    else:
        state = {}
    _save(state)
    return before - len(state)


def status() -> dict:
    """Diagnostic snapshot of what's cached and how stale."""
    state = _load()
    now = _now()
    return {
        "cache_file": str(CACHE_FILE),
        "ttl_s": DEFAULT_TTL_S,
        "entries": {
            k: {"ts": v.get("ts"), "age_s": now - v.get("ts", 0), "size_bytes": len(json.dumps(v.get("data", {})))}
            for k, v in state.items()
        },
    }
