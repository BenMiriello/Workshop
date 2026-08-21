"""The location app's chat assistant and its live snapshot.

Moved out of `services/app_personas.py`. Contributed through
`AppSpec.chat_provider` / `AppSpec.chat_snapshot`.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from services.app_persona import AppPersona
from wsplatform import paths

log = logging.getLogger("workshop.location_persona")


def _runtime_dir() -> Path:
    from services import stores as _stores

    return _stores._runtime_root()


def location_snapshot() -> str:
    """Latest position with an explicit freshness verdict.

    The phone tracker can silently stop posting for hours, so the presence of a
    'latest' ping is NOT proof of current whereabouts. Anything older than the
    staleness window is labeled stale."""
    from datetime import datetime, timezone

    STALE_AFTER_MIN = 30  # matches commute LOCATION_STALE_AFTER_S and the UI

    try:
        from services import location as location_svc  # local import: avoid cycle
        d = location_svc.latest()
    except Exception:  # noqa: BLE001 — no owner pinned / unreadable => unknown
        d = None
    if not d:
        return ("LOCATION: unknown. No recent location ping available. Do NOT "
                "guess or imply where the operator is.")

    ts = d.get("ts")
    age_min: float | None = None
    if ts:
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_min = (datetime.now(timezone.utc) - t).total_seconds() / 60
        except (ValueError, TypeError):
            age_min = None

    if age_min is None:
        return (f"LOCATION: last ping at {ts}, age unknown. Treat it as possibly "
                f"stale; do not assume it is the operator's current location.")

    if age_min < 60:
        age = f"{age_min:.0f} min ago"
    elif age_min < 1440:
        age = f"{age_min / 60:.1f} h ago"
    else:
        age = f"{age_min / 1440:.1f} d ago"

    coords = f"lat {d.get('lat')}, lng {d.get('lng')}, src={d.get('source','?')}"
    if age_min > STALE_AFTER_MIN:
        return (
            f"LOCATION (STALE. Last KNOWN position from {age}, NOT necessarily "
            f"where the operator is now): {coords}. The phone tracker may have "
            f"stopped updating. Do NOT state or imply this is his current "
            f"location. Call it the last known fix from {age} and say live "
            f"location is unavailable."
        )
    return f"LOCATION (live, {age}): {coords}."


# ============================================================================ #
# Budget context builders + plan-patch path resolution
# ============================================================================ #


class LocationContextProvider(AppPersona):
    key = "location"

    def live_context(self) -> str:
        return location_snapshot()
