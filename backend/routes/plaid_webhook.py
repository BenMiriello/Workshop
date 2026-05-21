import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request

router = APIRouter()
log = logging.getLogger("workshop.plaid_webhook")

WEBHOOK_LOG = Path(os.environ.get("WORKSHOP_PLAID_WEBHOOK_LOG", "/home/clawd-a/workshop/.runtime/plaid-webhooks.jsonl"))
# Mirror events to the generic feed so the Events page in the UI shows Plaid activity.
EVENTS_LOG = Path(os.environ.get("WORKSHOP_EVENTS_LOG", "/home/clawd-a/workshop/.runtime/events.jsonl"))


@router.post("")
async def plaid_webhook(request: Request):
    body = await request.body()
    try:
        payload = json.loads(body)
    except Exception:
        payload = {"_raw": body.decode("utf-8", errors="replace")}

    WEBHOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with WEBHOOK_LOG.open("a") as f:
        f.write(json.dumps({"hdr": dict(request.headers), "body": payload}) + "\n")

    # Fan out to the generic event feed (Phase 5b). Strip auth-ish headers; the
    # plaid-specific log above retains the full set for audit.
    enqueued = 0
    try:
        EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": "plaid",
            "known_source": True,
            "headers": {k: v for k, v in request.headers.items() if k.lower() not in {"authorization", "cookie", "plaid-verification"}},
            "body": payload,
        }
        offset = EVENTS_LOG.stat().st_size if EVENTS_LOG.exists() else 0
        with EVENTS_LOG.open("a") as f:
            f.write(json.dumps(record) + "\n")

        # Phase 5b: enqueue pending-run for each event-driven plaid subscriber.
        # Pass the full payload so dedup-key derivation has webhook_code+item_id.
        from services.event_dispatch import enqueue_event
        summary = payload if isinstance(payload, dict) else {"_raw": str(payload)[:512]}
        dispatch = enqueue_event("plaid", event_offset=offset, event_summary=summary)
        enqueued = dispatch["enqueued"]
    except Exception as e:
        # Don't break the webhook response if the event fanout fails; just log.
        log.warning("plaid event fanout failed: %s", e)

    # Invalidate the finance cache on any code that suggests stale account
    # state — transactions update, balance update, holdings update, item state.
    # The UI will refetch fresh next time it loads /apps.
    INVALIDATE_CODES = {
        "DEFAULT_UPDATE", "INITIAL_UPDATE", "HISTORICAL_UPDATE",
        "SYNC_UPDATES_AVAILABLE", "TRANSACTIONS_REMOVED",
        "DEFAULT_HOLDING_UPDATE", "DEFAULT_INVESTMENT_TRANSACTIONS_UPDATE",
    }
    invalidated = 0
    if isinstance(payload, dict) and payload.get("webhook_code") in INVALIDATE_CODES:
        from services.finance_cache import invalidate
        invalidated = invalidate("transactions", "balances")

    log.info(
        "plaid webhook: type=%s code=%s item_id=%s enqueued=%d cache_invalidated=%d",
        payload.get("webhook_type"),
        payload.get("webhook_code"),
        payload.get("item_id"),
        enqueued,
        invalidated,
    )
    return {"ok": True, "enqueued_runs": enqueued, "cache_invalidated": invalidated}
