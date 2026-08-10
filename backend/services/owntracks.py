"""Owntracks iOS HTTP-mode receiver.

Owntracks (https://owntracks.org) is a native iOS app that publishes the
operator's location to a user-controlled HTTP endpoint using iOS background
location entitlements. Unlike browser geolocation, it keeps reporting while
the phone is locked / Workshop tab closed / browser killed.

Payload shape (https://owntracks.org/booklet/tech/json/):
    { "_type": "location", "tst": 1716406800, "lat": 37.4, "lon": -122.1,
      "acc": 12, "alt": 30, "vel": 4, "cog": 180, "batt": 85,
      "tid": "ab", "t": "u", "topic": "owntracks/.../iphone" }

Auth: HTTP Basic, validated against secrets loaded from
~/.config/clawdbot/owntracks-auth.env (mode 0600, user-owned).
"""
from __future__ import annotations

import base64
import hmac
import os
from pathlib import Path
from typing import Any

from . import location
from services import paths

AUTH_ENV_PATH = Path(
    paths.env_path("OWNTRACKS_AUTH_ENV", "owntracks-auth.env", base="config")
)

_loaded: dict[str, str] = {}


def _load_auth() -> dict[str, str]:
    if _loaded:
        return _loaded
    if not AUTH_ENV_PATH.exists():
        return {}
    for line in AUTH_ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        _loaded[k.strip()] = v.strip()
    return _loaded


def verify_basic_auth(authorization: str | None) -> bool:
    """Validate an HTTP Basic `Authorization` header against the env-loaded
    creds. Constant-time compare on user+pass."""
    if not authorization or not authorization.startswith("Basic "):
        return False
    creds = _load_auth()
    expected_user = creds.get("OWNTRACKS_USER", "")
    expected_pass = creds.get("OWNTRACKS_PASS", "")
    if not expected_user or not expected_pass:
        return False
    try:
        decoded = base64.b64decode(authorization[6:].strip()).decode("utf-8")
    except Exception:
        return False
    if ":" not in decoded:
        return False
    user, _, pw = decoded.partition(":")
    return hmac.compare_digest(user, expected_user) and hmac.compare_digest(pw, expected_pass)


def is_location_event(payload: dict[str, Any]) -> bool:
    return payload.get("_type") == "location"


def translate(payload: dict[str, Any]) -> dict[str, Any]:
    """Owntracks payload → existing location.record() shape."""
    tid = payload.get("tid") or "ios"
    source = f"owntracks-{tid}"[:32]
    out: dict[str, Any] = {
        "lat": payload["lat"],
        "lng": payload["lon"],
        "source": source,
    }
    if "acc" in payload:
        out["accuracy_m"] = payload["acc"]
    if "alt" in payload:
        out["altitude_m"] = payload["alt"]
    if "vel" in payload:
        # Owntracks reports velocity in km/h; convert to m/s for consistency.
        try:
            out["speed_mps"] = float(payload["vel"]) * 1000.0 / 3600.0
        except (TypeError, ValueError):
            pass
    if "cog" in payload:
        out["heading_deg"] = payload["cog"]
    if "tst" in payload:
        # tst = unix timestamp; surface as client_ts ISO string.
        try:
            from datetime import datetime, timezone
            out["client_ts"] = datetime.fromtimestamp(int(payload["tst"]), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OSError):
            pass
    return out


def ingest(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Process one Owntracks payload. Returns the stored record, or None if
    the payload is not a location event (waypoints, transitions, etc. are
    ignored for now)."""
    if not is_location_event(payload):
        return None
    return location.record(translate(payload))
