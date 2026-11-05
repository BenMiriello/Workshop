"""The last day of dictations, per user, as their pieces: what was said and
where each run of it went (docs/dictation-and-clipboard.md, "Where words
went"). `<user runtime>/speech/dictations.json`.

The client saves a dictation's pieces while it runs, a moment after each
change, and once more when it ends. So a tab closed or a page that crashed
mid-dictation still leaves every word here: a dictation that stopped being
saved without ending reads as "interrupted". Long-term history, with audio,
is an app's (the `dictated` event); this is the platform's short memory, so a
word is never lost because no app was installed.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services import ownership

_LOCK = threading.Lock()

KEEP = timedelta(days=1)
KEEP_MAX = 50
# A live dictation not saved for this long ended without saying so.
STALE = timedelta(minutes=3)


def store_path() -> Path:
    return ownership.user_runtime_dir() / "speech" / "dictations.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load() -> list[dict]:
    try:
        data = json.loads(store_path().read_text())
        return [d for d in data if isinstance(d, dict) and d.get("id")] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write(entries: list[dict]) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries))
    tmp.replace(p)


def _when(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def _kept(entries: list[dict], now: datetime) -> list[dict]:
    fresh = [e for e in entries if (_when(e.get("updated_at")) or now) > now - KEEP]
    fresh.sort(key=lambda e: e.get("updated_at") or "", reverse=True)
    return fresh[:KEEP_MAX]


def save(dictation_id: str, started_at: str, pieces: list[dict], ended_at: str | None = None,
         status: str = "live") -> None:
    """Write a dictation's pieces, replacing what was kept for it. An ended
    one is never put back to live by a save that arrives late."""
    now = _now()
    with _LOCK:
        entries = _load()
        was = next((e for e in entries if e["id"] == dictation_id), None)
        if was and was.get("status") != "live" and status == "live":
            return
        entry = {
            "id": dictation_id, "started_at": started_at, "updated_at": now.isoformat(),
            "ended_at": ended_at, "status": status, "pieces": pieces,
        }
        rest = [e for e in entries if e["id"] != dictation_id]
        _write(_kept([entry, *rest], now))


def recent() -> list[dict]:
    """Newest first; a live one no longer being saved reads as interrupted."""
    now = _now()
    with _LOCK:
        entries = _kept(_load(), now)
    for e in entries:
        updated = _when(e.get("updated_at"))
        if e.get("status") == "live" and updated and now - updated > STALE:
            e["status"] = "interrupted"
    return entries
