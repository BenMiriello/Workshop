"""Generic event receiver — foundation for event-driven agent autonomy.

Doc spec (Tab 3, Agent Layer): an agent's autonomy mode can be `event-driven`
with a webhook/trigger source. This route accepts arbitrary JSON from any
named source, logs it to a JSONL, and (in a future commit) will nudge any
agents subscribed to that source via Letta.

For now it's a passive receiver — subscription dispatch is a 5b TODO item.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request


router = APIRouter(prefix="/events", tags=["events"])
log = logging.getLogger("workshop.events")

EVENTS_LOG = Path(os.environ.get(
    "WORKSHOP_EVENTS_LOG",
    "/home/clawd-a/workshop/.runtime/events.jsonl",
))
SUBSCRIPTIONS = Path(os.environ.get(
    "WORKSHOP_SUBSCRIPTIONS",
    "/home/clawd-a/workshop/.runtime/agent-subscriptions.json",
))

# Sources we will actively accept. Unknown sources are still logged but
# get an explicit "unknown_source" flag so we can audit untrusted callers.
KNOWN_SOURCES = {"plaid", "gmail", "calendar", "github", "open-wearables", "bluebubbles", "custom"}


def _load_subscriptions() -> dict[str, list[str]]:
    try:
        return json.loads(SUBSCRIPTIONS.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# Webhook signature verifier registry. Each verifier returns (ok: bool, reason: str).
# Default for unknown sources = (True, "no_verifier_registered").
def _verify_none(headers: dict, body: bytes) -> tuple[bool, str]:
    return True, "no_verifier_registered"


def _verify_plaid(headers: dict, body: bytes) -> tuple[bool, str]:
    """Plaid sends a `Plaid-Verification` JWT signed with their per-webhook key.
    Full verification needs `/webhook_verification_key/get` lookup. SCAFFOLDING:
    requires the header is present and looks JWT-shaped; full crypto verify is
    a TODO when Plaid sandbox webhooks start arriving."""
    jwt_header = headers.get("plaid-verification", "")
    if not jwt_header or jwt_header.count(".") != 2:
        return False, "missing_or_malformed_plaid_verification_jwt"
    return True, "jwt_shape_ok_scaffold_only_no_crypto_verify_yet"


def _verify_github(headers: dict, body: bytes) -> tuple[bool, str]:
    """GitHub uses `X-Hub-Signature-256: sha256=<hex>` HMAC over the body with
    the webhook secret. SCAFFOLDING: validates header presence; full HMAC verify
    needs the per-repo secret loaded from secrets.env (GITHUB_WEBHOOK_SECRET)."""
    sig = headers.get("x-hub-signature-256", "")
    if not sig.startswith("sha256="):
        return False, "missing_or_malformed_x_hub_signature_256"
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        return True, "header_present_but_GITHUB_WEBHOOK_SECRET_unset_scaffold_only"
    import hmac, hashlib
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig), "hmac_strict"


VERIFIERS = {
    "plaid": _verify_plaid,
    "github": _verify_github,
}


@router.post("/{source}")
async def receive_event(source: str, request: Request):
    body = await request.body()

    # Per-source signature verification. Unknown sources pass with a "no_verifier"
    # note; known sources (plaid, github) MUST validate.
    headers_lower = {k.lower(): v for k, v in request.headers.items()}
    verifier = VERIFIERS.get(source, _verify_none)
    sig_ok, sig_reason = verifier(headers_lower, body)
    if not sig_ok:
        log.warning("event signature_failed source=%s reason=%s", source, sig_reason)
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail={"signature": "failed", "reason": sig_reason})

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {"_raw": body.decode("utf-8", errors="replace")}

    EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "known_source": source in KNOWN_SOURCES,
        "signature": {"ok": sig_ok, "reason": sig_reason},
        "headers": {k: v for k, v in request.headers.items() if k.lower() not in {"authorization", "cookie"}},
        "body": payload,
    }
    # Capture byte offset BEFORE write so pending-runs can re-read the exact line.
    offset = EVENTS_LOG.stat().st_size if EVENTS_LOG.exists() else 0
    with EVENTS_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")

    # Phase 5b: enqueue pending-run for each event-driven subscriber. The cron
    # loop (5a) pops these and decides whether to fire `claude -p`.
    # Pass the full payload as event_summary so dedup-key derivation has access
    # to per-source identifiers (gmail.message_id, plaid.webhook_code+item_id, etc.).
    from services.event_dispatch import enqueue_event
    summary = payload if isinstance(payload, dict) else {"_raw": str(payload)[:512]}
    dispatch = enqueue_event(source, event_offset=offset, event_summary=summary)
    subscribers = _load_subscriptions().get(source, [])
    log.info(
        "event source=%s known=%s subscribers=%d enqueued=%d payload_keys=%s",
        source, source in KNOWN_SOURCES, len(subscribers), dispatch["enqueued"],
        list(payload.keys())[:8] if isinstance(payload, dict) else "non-dict",
    )

    return {
        "ok": True,
        "logged": True,
        "subscribers_notified": 0,
        "subscribers_pending": len(subscribers),
        "enqueued_runs": dispatch["enqueued"],
        "enqueued_agent_ids": dispatch["agent_ids"],
    }


@router.get("/recent")
async def recent_events(limit: int = 20, source: str | None = None):
    """Tail the events log. Limit caps at 200 to keep responses bounded."""
    limit = max(1, min(limit, 200))
    if not EVENTS_LOG.exists():
        return {"events": [], "count": 0}

    # Read entire file (it's expected to stay small — JSONL append, no fanout).
    # If this grows past ~10MB, switch to seeking from end.
    lines = EVENTS_LOG.read_text().splitlines()
    out = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if source and rec.get("source") != source:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return {"events": out, "count": len(out)}


@router.get("/subscriptions")
async def list_subscriptions():
    """Read-only view of which agents are wired to which sources. Strips
    metadata keys (those starting with `_`) and any non-list values so the
    response is consistently `{source: [agent_id, ...]}` for the UI."""
    raw = _load_subscriptions()
    clean = {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, list)}
    return {"subscriptions": clean, "path": str(SUBSCRIPTIONS)}
