"""Generic event receiver — foundation for event-driven agent autonomy.

Doc spec (Tab 3, Agent Layer): an agent's autonomy mode can be `event-driven`
with a webhook/trigger source. This route accepts arbitrary JSON from any
named source, logs it to a JSONL, and enqueues a pending run for each
subscriber via `services.event_dispatch.enqueue_event` (Phase 5b — landed,
not a stub). The cron loop (5a) pops those pending runs and decides whether
to actually fire `claude -p`; this route's job ends at "enqueued," not
"ran," which is what `enqueued_runs` in the response names honestly.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from services import paths


router = APIRouter(prefix="/events", tags=["events"])
log = logging.getLogger("workshop.events")

EVENTS_LOG = paths.env_path("WORKSHOP_EVENTS_LOG", "events.jsonl")
SUBSCRIPTIONS = paths.env_path("WORKSHOP_SUBSCRIPTIONS", "agent-subscriptions.json")

# Sources we will actively accept. Unknown sources are still logged but get an
# explicit "unknown_source" flag so we can audit untrusted callers.
#
# The platform's own are the surfaces it speaks natively; everything else is an
# app's, declared on its AppSpec.event_sources and present only while that app
# is installed. A webhook for an uninstalled app is an unknown source, which is
# the honest answer.
PLATFORM_SOURCES = {"gmail", "calendar", "github", "discord", "custom"}


def known_sources() -> set[str]:
    from services import app_registry

    return PLATFORM_SOURCES | app_registry.installed_event_sources()


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
    """Full Plaid JWT crypto verify — fetches per-webhook ES256 key from
    /webhook_verification_key/get, verifies signature, checks request body
    sha256, enforces 5-min replay window. Implementation in services/plaid_verify.

    Falls back to shape-check only when PLAID_CLIENT_ID/PLAID_SECRET are not
    set in the environment (degraded but unblocked for sandbox testing —
    real production traffic will fail crypto until creds are wired)."""
    jwt_header = headers.get("plaid-verification", "")
    from services.plaid_verify import verify
    return verify(jwt_header, body)


def _verify_github(headers: dict, body: bytes) -> tuple[bool, str]:
    """GitHub uses `X-Hub-Signature-256: sha256=<hex>` HMAC over the body with
    the webhook secret. Enforces full HMAC if `GITHUB_WEBHOOK_SECRET` env is
    set; rejects entirely if header is missing OR secret-set-but-mismatched.
    If the secret is NOT set, we refuse the webhook (changed from previous
    behavior that permissively passed missing-secret) — there's no GitHub
    integration wired today, so any github-source POST is suspect."""
    sig = headers.get("x-hub-signature-256", "")
    if not sig.startswith("sha256="):
        return False, "missing_or_malformed_x_hub_signature_256"
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        return False, "GITHUB_WEBHOOK_SECRET_unset_refusing_until_configured"
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
        "known_source": source in known_sources(),
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
        source, source in known_sources(), len(subscribers), dispatch["enqueued"],
        list(payload.keys())[:8] if isinstance(payload, dict) else "non-dict",
    )

    return {
        "ok": True,
        "logged": True,
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
