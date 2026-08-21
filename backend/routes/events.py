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
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from services import event_log


router = APIRouter(prefix="/events", tags=["events"])
log = logging.getLogger("workshop.events")

# The log and the subscription file belong to the event system, not to this
# route. Re-exported because GET /events/recent below reads the log directly and
# tests import EVENTS_LOG from here.
EVENTS_LOG = event_log.EVENTS_LOG
SUBSCRIPTIONS = event_log.SUBSCRIPTIONS

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


# `_load_subscriptions` was a third copy of the same read, and routes/birds.py
# imported it across route modules to publish an event. Both are gone: one
# implementation, in services/event_log.
_load_subscriptions = event_log.load_subscriptions


# Webhook signature verifier registry. Each verifier returns (ok: bool, reason: str).
# Default for unknown sources = (True, "no_verifier_registered").
def _verify_none(headers: dict, body: bytes) -> tuple[bool, str]:
    return True, "no_verifier_registered"


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


# The platform's own sources only. An app's verifier is declared on its
# AppSpec.event_verifiers and is present exactly while that app is installed --
# see verifier_for(). `github` stays here because github is a PLATFORM source.
PLATFORM_VERIFIERS = {
    "github": _verify_github,
}


def verifier_for(source: str):
    """The verifier for `source`: the platform's, else an installed app's, else
    the permissive default.

    Resolved PER REQUEST rather than frozen in a module-level dict, so
    installing an app makes its webhook verifiable without a restart -- and
    uninstalling one stops the platform vouching for a source it no longer
    serves."""
    v = PLATFORM_VERIFIERS.get(source)
    if v is not None:
        return v
    from services import app_registry

    return app_registry.installed_event_verifiers().get(source, _verify_none)



class Signature(BaseModel):
    """Whether the webhook proved it was who it said. `reason` names the
    verifier's verdict, including "no_verifier_registered" for a source with no
    verifier -- which is a pass, and says so rather than implying a check ran."""

    ok: bool
    reason: str


class EventAccepted(BaseModel):
    """`enqueued_runs` is named honestly: this route's job ends at ENQUEUED, not
    at ran. The cron loop pops those pending runs and decides whether to fire a
    model at all."""

    ok: bool
    logged: bool
    subscribers_pending: int
    enqueued_runs: int
    enqueued_agent_ids: list[str]


class EventRecord(BaseModel):
    """One logged event. `body` is the caller's payload verbatim and stays open:
    it is an arbitrary webhook's schema. `headers` has already had authorization
    and cookie stripped. `known_source` is false for a source the platform does
    not speak and no installed app declares, which is the honest answer for a
    webhook aimed at an uninstalled app."""

    ts: str
    source: str
    known_source: bool
    # OPTIONAL because the log is replayed, not just appended to. Measured over
    # the live log on 2026-08-18: 20 records, and 5 of them predate this field
    # entirely. `/events/recent` reads history, so a required `signature` would
    # 500 the endpoint on exactly those five rows. Absent means "written before
    # verification existed", which is not the same as a check that passed.
    signature: Signature | None = None
    headers: dict[str, str]
    body: dict[str, Any]


class RecentEvents(BaseModel):
    events: list[EventRecord]
    count: int


@router.post("/{source}", response_model=EventAccepted, response_model_exclude_unset=True)
async def receive_event(source: str, request: Request):
    body = await request.body()

    # Per-source signature verification. Unknown sources pass with a "no_verifier"
    # note; known sources (plaid, github) MUST validate.
    headers_lower = {k.lower(): v for k, v in request.headers.items()}
    verifier = verifier_for(source)
    sig_ok, sig_reason = verifier(headers_lower, body)
    if not sig_ok:
        log.warning("event signature_failed source=%s reason=%s", source, sig_reason)
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail={"signature": "failed", "reason": sig_reason})

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {"_raw": body.decode("utf-8", errors="replace")}

    # Append + fan out. The record shape, the strip-credential-headers rule and
    # the capture-the-offset-before-the-write rule all live in services/event_log
    # now; this route had one of the three hand-copies of them.
    known = source in known_sources()
    dispatch = event_log.publish_and_dispatch(
        source,
        payload,
        headers=dict(request.headers),
        signature={"ok": sig_ok, "reason": sig_reason},
        known=known,
    )
    subscribers = event_log.subscribers(source)
    log.info(
        "event source=%s known=%s subscribers=%d enqueued=%d payload_keys=%s",
        source, known, len(subscribers), dispatch["enqueued"],
        list(payload.keys())[:8] if isinstance(payload, dict) else "non-dict",
    )

    return {
        "ok": True,
        "logged": True,
        "subscribers_pending": len(subscribers),
        "enqueued_runs": dispatch["enqueued"],
        "enqueued_agent_ids": dispatch["agent_ids"],
    }


# GET /events/subscriptions IS GONE (2026-08-20). It dumped the source -> agent
# map for frontend/src/pages/Events.tsx, which is deleted; nothing else called
# it. `_load_subscriptions` stays, because event_dispatch and routes/birds read
# it.
#
# GET /events/recent SURVIVES, and not for the UI. `scripts/chat_event_bridge.py`
# polls it every 2s as an OUTBOUND MESSAGE BUS: it reads `discord-reply` records
# back off the webhook audit log and delivers them to Discord, deduping on an
# ISO-timestamp string comparison. That is a jerry-rig -- a webhook audit log is
# not a queue, and a string-compared timestamp is not a cursor -- and it should
# become a real outbound queue. It is left alone here because the fix is a
# design change, not a cleanup, and deleting the route underneath a script that
# calls it would leave a landmine for whoever next starts that bridge.
#
# Measured 2026-08-20: the bridge is NOT running and `discord-reply` has never
# appeared in the log (sources present: plaid, gmail, commute-ui, custom,
# birdnet, test_source). So this route has one caller and that caller has never
# succeeded. Recorded in the handoff rather than acted on.
#
# Both were, and /events/recent still is, unreachable from the operator's origin:
# /events is on the public edge blocklist because THIS router also carries
# POST /events/{source}, an unauthenticated webhook receiver, and blocking is
# per-prefix. Loopback callers are unaffected, which is all this one has.


@router.get("/recent", response_model=RecentEvents, response_model_exclude_unset=True)
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

