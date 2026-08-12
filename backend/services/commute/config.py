"""Read commute configuration from disk.

All four files are per-operator data (real home/work locations, live-data
source credentials) and live under .runtime/, never the tracked tree:
    schedule.json     — operator's daily schedule (home, work, AM/PM blocks)
    legs.json         — discrete legs of each direction
    targets.json      — arrival targets + 15-min flex + alert policy
    sources/registry.json — live-data source registry

Functions return parsed dicts. Re-read on every call so live edits via the
UI take effect without backend restart (small file, cheap read).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from services import paths

COMMUTE_ROOT = paths.env_path("WORKSHOP_COMMUTE_ROOT", "commute-config")

RUNTIME_ROOT = paths.env_path("WORKSHOP_COMMUTE_RUNTIME", "commute")


def _read_json(p: Path) -> dict[str, Any]:
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def load_schedule() -> dict[str, Any]:
    return _read_json(COMMUTE_ROOT / "schedule.json")


def load_legs() -> dict[str, Any]:
    return _read_json(COMMUTE_ROOT / "legs.json")


def load_targets() -> dict[str, Any]:
    return _read_json(COMMUTE_ROOT / "targets.json")


def load_sources() -> dict[str, Any]:
    return _read_json(COMMUTE_ROOT / "sources" / "registry.json")


def write_schedule(data: dict[str, Any]) -> None:
    """Atomic write of schedule.json. Used by UI editor."""
    p = COMMUTE_ROOT / "schedule.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def write_targets(data: dict[str, Any]) -> None:
    p = COMMUTE_ROOT / "targets.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def runtime_path(name: str) -> Path:
    """Get a path under the runtime dir (created on demand)."""
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    return RUNTIME_ROOT / name


def get_schedule_for_day(direction: str, day: str) -> dict[str, Any] | None:
    """Find the schedule entry matching direction ('to-work'/'to-home') and
    a day-key like 'mon'."""
    sch = load_schedule()
    for entry in sch.get("schedules", []):
        if entry.get("direction") == direction and day in entry.get("days", []):
            return entry
    return None


def get_legs_by_id(legs_id: str) -> list[dict[str, Any]] | None:
    legs = load_legs()
    for ls in legs.get("leg_sets", []):
        if ls.get("id") == legs_id:
            return ls.get("legs", [])
    return None


def get_endpoint_coords(name: str) -> tuple[float, float] | None:
    """Return (lat, lon) for 'home' or 'work' from schedule.json if both
    coords are set, else None. Used by location-aware commute scoring to
    detect 'already at destination' and 'never left origin' cases."""
    sch = load_schedule()
    entry = sch.get(name)
    if not isinstance(entry, dict):
        return None
    lat = entry.get("lat")
    lon = entry.get("lon")
    if lat is None or lon is None:
        return None
    try:
        return (float(lat), float(lon))
    except (TypeError, ValueError):
        return None
