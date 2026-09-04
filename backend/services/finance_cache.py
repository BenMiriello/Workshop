"""TTL-cached wrapper around the Plaid bridge.

Every Apps page load was firing 2 fresh Plaid API roundtrips (balances ~4s,
transactions ~1s) via a Node subprocess. With caching, hot loads return in
<5ms and we only hit Plaid on TTL expiry OR webhook-triggered invalidation.

Cache file: ~/workshop/.runtime/finance-cache.json
Schema: {"balances": {ts, data}, "transactions:30": {ts, data}, ...}
TTL: 5 minutes (configurable per-key via WORKSHOP_FINANCE_TTL_S env).

Webhook invalidation: routes/finance_plaid_webhook.py calls invalidate() on any
TRANSACTIONS webhook so the next /finance/transactions request fetches fresh.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from services import ownership

# Per-owner cache file: .runtime/users/<owner_id>/finance-cache.json. There is no
# global finance cache — one owner's cached balances/transactions must never be
# served to another. Resolved at call time from the pinned owner; None when no
# owner is set (e.g. an unowned webhook invalidate), in which case cache ops are
# no-ops and the TTL keeps data fresh.
def _cache_file() -> Path | None:
    oid = ownership.owner_or_none()
    if oid is None:
        return None
    return ownership.user_runtime_dir(oid) / "finance-cache.json"
DEFAULT_TTL_S = int(os.environ.get("WORKSHOP_FINANCE_TTL_S", "600"))  # 10 min fallback

# Per-key TTL overrides. Balances move slowly (deposits/charges settle once a day
# in practice); transactions even more so for already-recorded history. Picking
# generous TTLs makes the Apps page hit warm cache for the entire workday after
# one prewarm at startup. Webhook invalidation handles "fresh data arrived".
PER_KEY_TTL_S = {
    "balances": int(os.environ.get("WORKSHOP_FINANCE_BAL_TTL_S", "600")),    # 10 min
    "transactions": int(os.environ.get("WORKSHOP_FINANCE_TXN_TTL_S", "1800")),  # 30 min
}


def _ttl_for(key: str) -> int:
    """Resolve TTL for a cache key, falling back to DEFAULT_TTL_S. Matches both
    bare keys ('balances') and namespaced ones ('transactions:30')."""
    head = key.split(":", 1)[0]
    return PER_KEY_TTL_S.get(head, DEFAULT_TTL_S)

_lock = asyncio.Lock()

# Per-key in-flight refresh tasks. Dedupes concurrent fetchers for the same key
# (stampede control) and lets a stale-while-revalidate background refresh run
# without holding the file lock across the slow Plaid roundtrip.
_inflight: dict[str, "asyncio.Future"] = {}


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _load() -> dict:
    f = _cache_file()
    if f is None:
        return {}
    try:
        return json.loads(f.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(state: dict) -> None:
    f = _cache_file()
    if f is None:
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write — finance-cache is read by multiple processes (UI polling
    # + cron consumer + webhook). Write to tmp + rename.
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(f)


def _ikey(key: str) -> str:
    """In-flight dedupe key, namespaced by owner so two owners refreshing the
    same logical key ('balances') never share a fetch task (cross-owner leak)."""
    return f"{ownership.owner_or_none()}:{key}"


async def _fetch_and_store(key: str, fetcher) -> dict:
    """Run the (slow) fetcher OUTSIDE the file lock, then take the lock only to
    merge the result in. Reloads state before writing so a concurrent refresh of
    a different key isn't clobbered."""
    data = await fetcher()
    async with _lock:
        state = _load()
        state[key] = {"ts": _now(), "data": data}
        _save(state)
    return data


def _kick(key: str, fetcher) -> "asyncio.Future":
    """Start (or reuse) a background refresh for `key`. Concurrent callers share
    the one in-flight task so we never fire duplicate Plaid roundtrips. The
    in-flight map is owner-namespaced. asyncio.ensure_future copies the current
    context, so the task runs with this caller's pinned owner (correct file +
    correct Plaid items)."""
    ik = _ikey(key)
    existing = _inflight.get(ik)
    if existing is not None and not existing.done():
        return existing
    task = asyncio.ensure_future(_fetch_and_store(key, fetcher))
    _inflight[ik] = task
    task.add_done_callback(lambda t, k=ik: _inflight.pop(k, None) if _inflight.get(k) is t else None)
    return task


async def get_or_fetch(key: str, fetcher, ttl_s: int | None = None, force_refresh: bool = False) -> tuple[dict, dict]:
    """Stale-while-revalidate read. Returns (data, meta) where
    meta = {cached, stale, ts, age_s, ttl_s}.

      - Fresh entry (age < ttl): return it. cached=True, stale=False.
      - Stale entry (age >= ttl): return it IMMEDIATELY and kick a background
        refresh. cached=True, stale=True. The page renders instantly off cache
        and picks up fresh numbers on the next poll/visit — no 17s block.
      - No entry at all (truly cold) OR force_refresh: block on the fetch.

    `fetcher` is an async callable returning the raw data dict. When ttl_s is
    None, resolves via PER_KEY_TTL_S (falling back to DEFAULT_TTL_S)."""
    if ttl_s is None:
        ttl_s = _ttl_for(key)
    async with _lock:
        entry = _load().get(key)
    now = _now()

    if entry is not None and not force_refresh:
        age = now - entry.get("ts", 0)
        if age < ttl_s:
            return entry["data"], {"cached": True, "stale": False, "ts": entry["ts"], "age_s": age, "ttl_s": ttl_s}
        # Stale: serve now, refresh behind it.
        _kick(key, fetcher)
        return entry["data"], {"cached": True, "stale": True, "ts": entry["ts"], "age_s": age, "ttl_s": ttl_s}

    # Cold or forced: must block. Dedupe via the in-flight guard.
    data = await _kick(key, fetcher)
    return data, {"cached": False, "stale": False, "ts": _now(), "age_s": 0.0, "ttl_s": ttl_s}


# Pluggable post-invalidate hook so main.py can re-prewarm without a circular
# import. Set in main.py's lifespan. No-op by default.
_on_invalidate = None


def set_invalidate_hook(fn) -> None:
    """Register an async function called (no args) after each invalidate().
    Used by main.py to kick a background reprewarm so first user request hits cache."""
    global _on_invalidate
    _on_invalidate = fn


def invalidate(*prefixes: str) -> int:
    """Drop cache entries whose key starts with any of the given prefixes.
    Returns number of entries removed. Call with `("transactions",)` on webhook.
    Triggers the registered re-prewarm hook (fire-and-forget) if any."""
    state = _load()
    before = len(state)
    if prefixes:
        state = {k: v for k, v in state.items() if not any(k == p or k.startswith(p + ":") for p in prefixes)}
    else:
        state = {}
    _save(state)
    removed = before - len(state)
    if removed and _on_invalidate is not None:
        try:
            # Schedule the prewarm on the running loop without awaiting it.
            asyncio.get_event_loop().create_task(_on_invalidate())
        except RuntimeError:
            # No running loop (called from a sync context like a test). Skip.
            pass
    return removed


def status() -> dict:
    """Diagnostic snapshot of what's cached and how stale."""
    state = _load()
    now = _now()
    f = _cache_file()
    return {
        "cache_file": str(f) if f else None,
        "ttl_s": DEFAULT_TTL_S,
        "ttl_overrides": PER_KEY_TTL_S,
        "entries": {
            k: {
                "ts": v.get("ts"),
                "age_s": now - v.get("ts", 0),
                "ttl_s": _ttl_for(k),
                "size_bytes": len(json.dumps(v.get("data", {}))),
            }
            for k, v in state.items()
        },
    }
