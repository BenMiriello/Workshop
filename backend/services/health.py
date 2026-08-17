"""Health Auto Export iOS ingest.

Health Auto Export (HAE, https://apps.apple.com/us/app/health-auto-export/id1115567069)
is a paid (~$5) iOS app that periodically exports Apple Health data —
HRV, steps, sleep, heart rate, workouts, etc. — to a configurable HTTP
webhook. Same pattern as Owntracks: native app with background
entitlements pushes to a self-hosted endpoint over Tailscale.

Auth: HTTP Basic, secret in ~/.config/clawdbot/health-auth.env.

Storage: append-only JSONL at .runtime/health.jsonl, plus a small
per-metric "latest" index for O(1) reads of "current HRV" etc.

This replaces the Open Wearables stack — same data, simpler pipeline,
no third-party server, no upstream Docker images going stale.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import ownership
from wsplatform import paths

AUTH_ENV_PATH = Path(
    paths.env_path("HEALTH_AUTH_ENV", "health-auth.env", base="config")
)

# Health history is partitioned per owner: `.runtime/users/<owner_id>/health.*`.


def _history_path():
    return ownership.user_runtime_dir() / "health.jsonl"


def _latest_path():
    return ownership.user_runtime_dir() / "health-latest.json"


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
    if not authorization or not authorization.startswith("Basic "):
        return False
    creds = _load_auth()
    expected_user = creds.get("HEALTH_USER", "")
    expected_pass = creds.get("HEALTH_PASS", "")
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_dir() -> None:
    ownership.user_runtime_dir()


def ingest(payload: dict[str, Any]) -> dict[str, Any]:
    """Append the full payload to history and update the per-metric latest
    index. HAE payload shape:
        {"data": {"metrics": [{"name": "heart_rate", "units": "...",
                               "data": [{"date": "...", "qty": 72}, ...]}]},
                  "workouts": [...]}}

    We don't transform the metric values — store as-received and let the
    UI / queries shape later. This avoids losing fields HAE adds across
    versions.
    """
    _ensure_dir()
    rec = {"ts": _now_iso(), "payload": payload}
    with _history_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")

    # Update per-metric latest index. Read current → merge → atomic write.
    latest = _read_latest()
    metrics = (payload.get("data") or {}).get("metrics") or []
    for m in metrics:
        name = m.get("name")
        if not name:
            continue
        rows = m.get("data") or []
        if not rows:
            continue
        # Take the newest row by `date`.
        try:
            rows = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)
        except Exception:
            pass
        newest = rows[0]
        latest[name] = {
            "ts": _now_iso(),
            "date": newest.get("date"),
            "qty": newest.get("qty") if "qty" in newest else newest,
            "units": m.get("units"),
        }
    tmp = _latest_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(latest, indent=2), encoding="utf-8")
    os.replace(tmp, _latest_path())
    return {"stored": True, "metrics_seen": len(metrics)}


def _read_latest() -> dict[str, Any]:
    if not _latest_path().exists():
        return {}
    try:
        return json.loads(_latest_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def latest() -> dict[str, Any]:
    """Return per-metric latest values keyed by metric name."""
    return _read_latest()


def history(limit: int = 25, offset: int = 0) -> dict[str, Any]:
    """Return last `limit` ingest records, newest-first."""
    if not _history_path().exists():
        return {"records": [], "total": 0, "has_more": False}
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    with _history_path().open("r", encoding="utf-8") as fh:
        lines = [ln for ln in fh.readlines() if ln.strip()]
    total = len(lines)
    rev = list(reversed(lines))
    out: list[dict[str, Any]] = []
    for line in rev[offset:offset + limit]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return {"records": out, "total": total, "has_more": (offset + limit) < total}


def clear() -> dict[str, int]:
    n = 0
    if _history_path().exists():
        try:
            with _history_path().open("r", encoding="utf-8") as fh:
                n = sum(1 for line in fh if line.strip())
        except Exception:
            n = 0
        _history_path().unlink()
    if _latest_path().exists():
        _latest_path().unlink()
    return {"cleared": n}
