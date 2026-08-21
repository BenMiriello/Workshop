"""The commute app's chat assistant and its live snapshot.

Moved out of `services/app_personas.py`. Contributed through
`AppSpec.chat_provider` / `AppSpec.chat_snapshot`.

The assistant's context includes the operator's LOCATION, which commute declares
as a dependency (`AppSpec.identity.needs = ["location.read"]`) rather than
assuming. That import is call-time and degrades to nothing when the location app
is not installed, so commute stays usable without it instead of failing to build.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from services.app_persona import AppPersona
from wsplatform import paths

log = logging.getLogger("workshop.commute_persona")


def _runtime_dir() -> Path:
    from services import stores as _stores

    return _stores._runtime_root()


def _location_context() -> str:
    """The location app's snapshot, or nothing when it is not installed."""
    try:
        from services import location_persona
    except Exception:
        return ""
    try:
        return location_persona.location_snapshot()
    except Exception:
        log.debug("commute: location snapshot unavailable", exc_info=True)
        return ""


def commute_snapshot() -> str:
    """Latest commute decision + tick state from the commute runtime dir."""
    runtime = Path(os.environ.get("WORKSHOP_COMMUTE_RUNTIME", str(_runtime_dir() / "commute")))
    if not runtime.exists():
        return "COMMUTE: (no commute runtime dir)"
    latest_files = sorted(runtime.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    lines: list[str] = ["COMMUTE (latest snapshot):"]
    found_any = False
    for p in latest_files[:3]:
        try:
            d = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        ts = d.get("ts") or "?"
        agency = d.get("agency") or ""
        feeds = d.get("feeds") or {}
        trip_count = (feeds.get("trip_updates") or {}).get("entity_count")
        vehicle_count = (feeds.get("vehicle_positions") or {}).get("entity_count")
        lines.append(f"- {p.name} ts={ts} agency={agency} trips={trip_count} vehicles={vehicle_count}")
        found_any = True
        break
    if not found_any:
        lines.append("(no parseable snapshots)")

    sub_file = _runtime_dir() / "agent-subscriptions.json"
    if sub_file.exists():
        try:
            subs = json.loads(sub_file.read_text())
            if isinstance(subs, dict):
                real_sources = [k for k in subs if not k.startswith("_")]
                lines.append(f"  Subscribed sources: {len(real_sources)} ({', '.join(real_sources[:6])})")
        except (json.JSONDecodeError, OSError):
            pass
    return "\n".join(lines)


class CommuteContextProvider(AppPersona):
    key = "commute"

    def live_context(self) -> str:
        loc = _location_context()
        return commute_snapshot() + ("\n\n" + loc if loc else "")
