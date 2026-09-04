"""The finance app's side of the event bus: verify a Plaid webhook, and say
what identifies one.

Both of these used to live in platform code -- a `VERIFIERS = {"plaid": ...}`
dict in `routes/events.py` and an `if source == "plaid":` branch in
`services/event_dispatch.py`. Neither is platform knowledge. Whether a webhook
is authentic, and which of its fields identify the event rather than merely
vary with it, are things the owner of the integration knows and the platform
cannot infer. They are declared on `AppSpec.event_verifiers` /
`AppSpec.event_dedup` and are in effect exactly while the app is installed.
"""
from __future__ import annotations


def verify_webhook(headers: dict, body: bytes) -> tuple[bool, str]:
    """AppSpec.event_verifiers signature. Full ES256 JWT verify: per-webhook key
    fetch, signature, body sha256, and the replay window, in services/plaid_verify.

    Degrades to a shape check when PLAID_CLIENT_ID/PLAID_SECRET are unset, so a
    sandbox is testable; real traffic fails crypto until the creds are wired.
    """
    from services import finance_plaid_verify as plaid_verify

    return plaid_verify.verify_webhook(headers, body)


def dedup_key(summary: dict) -> str | None:
    """AppSpec.event_dedup signature. Plaid redelivers identical webhooks, and
    `webhook_code` + `item_id` is what makes two arrivals the same event.

    Returning None is a real answer, not a fallthrough: a payload without both
    fields carries no stable identity, and the platform's content hash would
    dedup two genuinely distinct events that happened to serialize alike.
    """
    s = summary or {}
    body = s.get("body") or {}
    code = s.get("webhook_code") or body.get("webhook_code")
    item_id = s.get("item_id") or body.get("item_id")
    if code and item_id:
        return f"plaid:{code}:{item_id}"
    return None
