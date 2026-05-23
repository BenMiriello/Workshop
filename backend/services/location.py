"""Operator location store.

The Workshop frontend and the Commute app POST the operator's browser-geolocation
position here. The commute layer (and any LLM-facing helpers) read the latest
position to answer questions like "am I on the train yet" / "did I leave for
the station yet."

Storage:
    .runtime/location.jsonl        — append-only history (one JSON object per line)
    .runtime/location-latest.json  — cached "latest" record for O(1) reads

Files are operator-local only — nothing is sent to a third party. Operator can
clear history via DELETE /location/history (truncates both files).

A record looks like:
    {
        "ts": "2026-05-21T15:51:29.691Z",   # ISO-8601 UTC (server-stamped on receipt)
        "client_ts": "2026-05-21T15:51:29.000Z",  # browser timestamp (optional)
        "lat": 37.4419, "lng": -122.143,
        "accuracy_m": 12.4,
        "altitude_m": 23.1,                       # optional
        "heading_deg": 91.0,                      # optional
        "speed_mps": 1.2,                         # optional
        "source": "workshop" | "commute-app" | "manual"
    }
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUNTIME_DIR = Path(os.environ.get(
    "WORKSHOP_LOCATION_RUNTIME",
    "/home/clawd-a/workshop/.runtime",
))
HISTORY_PATH = RUNTIME_DIR / "location.jsonl"
LATEST_PATH = RUNTIME_DIR / "location-latest.json"


def _ensure_dir() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def record(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate, stamp, persist a new location ping. Returns the stored record."""
    if "lat" not in payload or "lng" not in payload:
        raise ValueError("location payload must include 'lat' and 'lng'")
    try:
        lat = float(payload["lat"])
        lng = float(payload["lng"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"lat/lng must be numbers: {exc}") from exc
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
        raise ValueError("lat must be in [-90,90], lng in [-180,180]")

    rec: dict[str, Any] = {
        "ts": _now_iso(),
        "lat": lat,
        "lng": lng,
    }
    # Optional fields — keep only if present + numeric where applicable.
    for key in ("accuracy_m", "altitude_m", "heading_deg", "speed_mps"):
        v = payload.get(key)
        if v is None:
            continue
        try:
            rec[key] = float(v)
        except (TypeError, ValueError):
            continue
    if isinstance(payload.get("client_ts"), str):
        rec["client_ts"] = payload["client_ts"]
    src = payload.get("source")
    if isinstance(src, str) and src:
        rec["source"] = src[:32]
    else:
        rec["source"] = "unknown"

    _ensure_dir()
    # Append to history.
    with HISTORY_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    # Overwrite latest cache (atomic-ish — write tmp + rename).
    tmp = LATEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    os.replace(tmp, LATEST_PATH)
    return rec


def latest() -> dict[str, Any] | None:
    """Return the most-recent recorded position, or None if never recorded."""
    if not LATEST_PATH.exists():
        return None
    try:
        return json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def history(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """Return `limit` records starting `offset` from newest, newest-first.

    Returns {"records": [...], "total": int, "has_more": bool} so the UI can
    do "Load more" pagination without re-counting the file."""
    if not HISTORY_PATH.exists():
        return {"records": [], "total": 0, "has_more": False}
    limit = max(1, min(limit, 5000))
    offset = max(0, offset)
    # Whole-file read is fine at our scale (~tens of KB/day, kept indefinitely).
    with HISTORY_PATH.open("r", encoding="utf-8") as fh:
        lines = [ln for ln in fh.readlines() if ln.strip()]
    total = len(lines)
    # newest-first: take from the end, then page within that slice
    rev = list(reversed(lines))
    page_slice = rev[offset:offset + limit]
    out: list[dict[str, Any]] = []
    for line in page_slice:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return {
        "records": out,
        "total": total,
        "has_more": (offset + limit) < total,
    }


def clear() -> dict[str, int]:
    """Erase history + latest. Returns counts cleared."""
    n = 0
    if HISTORY_PATH.exists():
        try:
            with HISTORY_PATH.open("r", encoding="utf-8") as fh:
                n = sum(1 for line in fh if line.strip())
        except Exception:
            n = -1
        HISTORY_PATH.unlink(missing_ok=True)
    LATEST_PATH.unlink(missing_ok=True)
    return {"cleared": n}
