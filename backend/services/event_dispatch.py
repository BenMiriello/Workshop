"""Event → agent-run pending-queue dispatch (Phase 5b wiring half).

When an event lands at /events/{source} or /plaid/webhook, we want to enqueue
a run for each agent subscribed to that source whose `autonomy_mode` is
`event-driven`. The cron-loop (5a) downstream is what actually pops these and
fires `claude -p`; this module is the producer side only.

Design notes:
- We deliberately do NOT call into Letta to filter by `autonomy_mode` here —
  that would require an async httpx call from inside the webhook hot path. The
  subscription file is the SOR for "which agents are listening"; the autonomy
  mode is consulted from a cached per-agent metadata file if present, else we
  fall back to "enqueue anyway" (the cron consumer will re-check before firing).
- File format: JSONL, one `{ts, agent_id, source, event_ref}` per line at
  `~/workshop/.runtime/agent-runs-pending.jsonl`.
- The event_ref is a `{kind: "events_log_offset_byte", offset: int}` pointing
  into events.jsonl so the consumer can re-read the original payload without
  us duplicating it into the pending file.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from wsplatform import paths

log = logging.getLogger("workshop.event_dispatch")

SUBSCRIPTIONS = paths.env_path("WORKSHOP_SUBSCRIPTIONS", "agent-subscriptions.json")
PENDING_RUNS = paths.env_path("WORKSHOP_PENDING_RUNS", "agent-runs-pending.jsonl")
AGENT_META_CACHE = paths.env_path("WORKSHOP_AGENT_META_CACHE", "agent-meta-cache.json")
DEDUP_FILE = paths.env_path("WORKSHOP_EVENT_DEDUP", "event-dedup.json")
DEDUP_WINDOW_S = int(os.environ.get("WORKSHOP_EVENT_DEDUP_WINDOW_S", "600"))  # 10 min


# Fourth copy of this read, now the only one. See services/event_log.
def _load_subscriptions() -> dict:
    from services import event_log

    return event_log.load_subscriptions()


def _load_meta_cache() -> dict:
    """Optional cache mapping agent_id -> {autonomy_mode, tools, ...}.
    Maintained by agents.py PATCH/POST handlers when they update Letta metadata.
    If absent, we don't filter — every subscriber gets enqueued."""
    try:
        return json.loads(AGENT_META_CACHE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _idempotency_key(source: str, event_summary: dict) -> str | None:
    """Derive a stable key for cross-arrival dedup.

    Which payload fields IDENTIFY an event, as opposed to merely varying with
    it, is knowledge the integration's owner has -- so an app declares its own
    deriver on `AppSpec.event_dedup` and the platform consults it first. Only
    the platform's own sources are named here.

    - gmail: message_id (unique per email — exact dedup)
    - github: delivery-id from header summary (unique per delivery)
    - generic: hash of source + sorted(event_summary)
    Returns None if no stable key can be derived (then dedup is skipped)."""
    s = event_summary or {}
    try:
        from services import app_registry

        app_deriver = app_registry.installed_event_dedup().get(source)
    except Exception:
        app_deriver = None
    if app_deriver is not None:
        # An app that can identify its own event wins outright, INCLUDING when
        # it returns None: "I own this source and this payload carries no stable
        # identity" is an answer, and falling through to the content hash would
        # overrule it with a weaker guess.
        try:
            return app_deriver(s)
        except Exception as e:
            log.warning("event_dedup deriver failed source=%s: %s", source, e)
            return None
    if source == "gmail":
        mid = s.get("message_id") or s.get("id")
        return f"gmail:{mid}" if mid else None
    if source == "github":
        did = s.get("delivery_id") or (s.get("headers") or {}).get("x-github-delivery")
        return f"github:{did}" if did else None
    if not s:
        return None
    # Generic fallback — hash a small canonical form
    try:
        canonical = json.dumps(s, sort_keys=True, separators=(",", ":"))[:512]
        import hashlib
        return f"{source}:{hashlib.sha1(canonical.encode()).hexdigest()[:16]}"
    except Exception:
        return None


def _check_and_record_dedup(key: str) -> bool:
    """Returns True if THIS arrival should be skipped (already seen within window).
    Prunes stale entries on every call (file is small — agents x window seconds)."""
    now_ts = datetime.now(timezone.utc).timestamp()
    try:
        cache = json.loads(DEDUP_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}
    # Prune stale
    cache = {k: v for k, v in cache.items() if (now_ts - v) < DEDUP_WINDOW_S * 2}
    seen_at = cache.get(key)
    if seen_at and (now_ts - seen_at) < DEDUP_WINDOW_S:
        # Update last_seen anyway so a steady stream extends the window (catches retry storms)
        cache[key] = now_ts
        DEDUP_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEDUP_FILE.write_text(json.dumps(cache))
        return True
    cache[key] = now_ts
    DEDUP_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEDUP_FILE.write_text(json.dumps(cache))
    return False


def enqueue_event(source: str, event_offset: int | None = None, event_summary: dict | None = None) -> dict:
    """For each agent subscribed to `source` whose cached metadata is
    `autonomy_mode == event-driven` (or has no cached metadata — see fallback),
    append a pending-run record. Returns {enqueued: int, skipped: int, agent_ids: [...]}.
    """
    subs_data = _load_subscriptions()
    candidates = subs_data.get(source, []) if isinstance(subs_data.get(source), list) else []
    meta_cache = _load_meta_cache()

    # Cross-arrival idempotency: suppress duplicate events (Plaid retries,
    # gmail double-arrivals, etc.). One claude -p per logical event, not per
    # webhook delivery. Keyed per-source; see _idempotency_key.
    dedup_key = _idempotency_key(source, event_summary or {})
    if dedup_key and _check_and_record_dedup(dedup_key):
        log.info("event_dispatch source=%s deduped key=%s (within %ds window)", source, dedup_key, DEDUP_WINDOW_S)
        return {"enqueued": 0, "skipped": len(candidates), "agent_ids": [], "deduped": True, "dedup_key": dedup_key}

    enqueued: list[str] = []
    skipped: list[dict] = []
    PENDING_RUNS.parent.mkdir(parents=True, exist_ok=True)

    with PENDING_RUNS.open("a") as f:
        for agent_id in candidates:
            meta = meta_cache.get(agent_id)
            if meta is not None:
                mode = meta.get("autonomy_mode") or meta.get("autonomy")
                # Both event-driven and continuous agents may have event subscriptions;
                # only skip if mode is explicitly off/scheduled.
                if mode and mode not in ("event-driven", "continuous"):
                    skipped.append({"agent_id": agent_id, "reason": f"autonomy_mode={mode}"})
                    continue
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "agent_id": agent_id,
                "source": source,
                "event_ref": {
                    "kind": "events_log_offset_byte" if event_offset is not None else "summary_only",
                    "offset": event_offset,
                },
                "event_summary": event_summary or {},
                "status": "pending",
            }
            f.write(json.dumps(record) + "\n")
            enqueued.append(agent_id)

    if enqueued:
        log.info("event_dispatch source=%s enqueued=%d skipped=%d", source, len(enqueued), len(skipped))
    return {"enqueued": len(enqueued), "skipped": len(skipped), "agent_ids": enqueued}
