import json
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from services import event_log
from wsplatform import paths

router = APIRouter()
log = logging.getLogger("workshop.plaid_webhook")

WEBHOOK_LOG = paths.env_path("WORKSHOP_PLAID_WEBHOOK_LOG", "plaid-webhooks.jsonl")


@router.post("")
async def plaid_webhook(request: Request):
    body = await request.body()

    # Verify the Plaid-Verification ES256 JWT (signature + body-sha256 + replay
    # window) BEFORE acting. This endpoint is unauthenticated by design (Plaid
    # calls it), but it fans payloads into the event feed (which can enqueue
    # agent runs) and invalidates the finance cache — so an unsigned POST is an
    # event-injection primitive. Fail closed on a missing/invalid signature.
    from services.plaid_verify import verify as _verify_plaid_jwt
    ok, reason = _verify_plaid_jwt(request.headers.get("plaid-verification", ""), body)
    if not ok:
        log.warning("plaid webhook rejected (unverified): %s", reason)
        raise HTTPException(status_code=401, detail="invalid plaid webhook signature")

    try:
        payload = json.loads(body)
    except Exception:
        payload = {"_raw": body.decode("utf-8", errors="replace")}

    WEBHOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with WEBHOOK_LOG.open("a") as f:
        f.write(json.dumps({"hdr": dict(request.headers), "body": payload}) + "\n")

    # Fan out to the generic event feed (Phase 5b). Strip auth-ish headers; the
    # plaid-specific log above retains the full set for audit.
    #
    # This was the third hand-copy of the append sequence and the one that had
    # drifted: it wrote no `signature` field at all, so a plaid row in the feed
    # was indistinguishable from an unverified one despite this route having
    # just done a full ES256 verify above. Publishing through the platform API
    # records the verdict it already has.
    enqueued = 0
    try:
        dispatch = event_log.publish_and_dispatch(
            "plaid",
            payload,
            headers=dict(request.headers),
            signature={"ok": ok, "reason": reason},
            redact_headers=("plaid-verification",),
            known=True,
        )
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
