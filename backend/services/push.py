"""Remote push (APNs) — the DEFERRED PAID feature.

Sending is disabled until the Apple Developer Program is enrolled and the APNS_*
env is configured; until then `send_push` is a no-op that reports `disabled`.
The device-token REGISTRY works now (free) so the mobile app can register, and
the alerter can call `send_push` unconditionally (best-effort, gated here).

Enable later by setting: APNS_KEY_ID, APNS_TEAM_ID, APNS_KEY_P8_PATH (path to the
AuthKey .p8), APNS_TOPIC (bundle id). No code change, just env + a backend restart.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import ownership

# Push tokens are partitioned per owner: `.runtime/users/<owner_id>/push-tokens.json`.


def _tokens_file():
    return ownership.user_runtime_dir() / "push-tokens.json"

APNS_KEY_ID = os.environ.get("APNS_KEY_ID", "")
APNS_TEAM_ID = os.environ.get("APNS_TEAM_ID", "")
APNS_KEY_PATH = os.environ.get("APNS_KEY_P8_PATH", "")
APNS_TOPIC = os.environ.get("APNS_TOPIC") or os.environ.get("WORKSHOP_MOBILE_BUNDLE", "")
APNS_HOST = os.environ.get("APNS_HOST", "https://api.push.apple.com")


def is_enabled() -> bool:
    return bool(
        APNS_KEY_ID
        and APNS_TEAM_ID
        and APNS_KEY_PATH
        and Path(APNS_KEY_PATH).exists()
    )


def _read() -> dict[str, Any]:
    try:
        return json.loads(_tokens_file().read_text())
    except (OSError, json.JSONDecodeError):
        return {"tokens": []}


def _write(data: dict[str, Any]) -> None:
    _tokens_file().parent.mkdir(parents=True, exist_ok=True)
    _tokens_file().write_text(json.dumps(data, indent=2))


def register_token(token: str, platform: str = "ios") -> dict[str, Any]:
    token = (token or "").strip()
    if not token:
        raise ValueError("empty token")
    data = _read()
    tokens = data.setdefault("tokens", [])
    existing = next((t for t in tokens if t.get("token") == token), None)
    now = int(time.time())
    if existing:
        existing["platform"] = platform
        existing["seen"] = now
    else:
        tokens.append({"token": token, "platform": platform, "added": now, "seen": now})
    _write(data)
    return {"ok": True, "count": len(tokens)}


def list_tokens() -> list[dict[str, Any]]:
    return _read().get("tokens", [])


def remove_token(token: str) -> dict[str, Any]:
    data = _read()
    before = data.get("tokens", [])
    data["tokens"] = [t for t in before if t.get("token") != token]
    _write(data)
    return {"ok": True, "removed": len(before) - len(data["tokens"])}


def status() -> dict[str, Any]:
    return {
        "configured": is_enabled(),
        "tokens": len(list_tokens()),
        "topic": APNS_TOPIC,
        "host": APNS_HOST,
    }


def _provider_jwt() -> str:
    """ES256-signed APNs provider token. Raises if deps/config are missing."""
    import jwt  # PyJWT — only needed when push is enabled

    with open(APNS_KEY_PATH) as fh:
        key = fh.read()
    return jwt.encode(
        {"iss": APNS_TEAM_ID, "iat": int(time.time())},
        key,
        algorithm="ES256",
        headers={"kid": APNS_KEY_ID},
    )


async def send_push(title: str, body: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send to every registered token. No-op (disabled) until APNs is configured."""
    if not is_enabled():
        return {"sent": 0, "disabled": True, "reason": "APNs not configured"}

    tokens = list_tokens()
    if not tokens:
        return {"sent": 0, "disabled": False, "reason": "no registered tokens"}

    try:
        import httpx

        auth = _provider_jwt()
    except Exception as e:  # noqa: BLE001 — missing PyJWT/httpx/key => stay disabled
        return {"sent": 0, "disabled": True, "reason": f"send unavailable: {e}"}

    payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"}}
    if data:
        payload.update(data)

    sent, errors = 0, []
    async with httpx.AsyncClient(http2=True, timeout=10.0) as client:
        for t in tokens:
            try:
                r = await client.post(
                    f"{APNS_HOST}/3/device/{t['token']}",
                    headers={
                        "authorization": f"bearer {auth}",
                        "apns-topic": APNS_TOPIC,
                        "apns-push-type": "alert",
                    },
                    json=payload,
                )
                if r.status_code == 200:
                    sent += 1
                else:
                    errors.append(f"{r.status_code}:{r.text[:80]}")
            except Exception as e:  # noqa: BLE001
                errors.append(str(e)[:80])
    return {"sent": sent, "disabled": False, "errors": errors}
