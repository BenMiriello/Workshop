"""Workshop-scope autonomy override (autonomy-model 2026-07-18, §1.2).

The override is a single JSON file at `.runtime/autonomy-override.json`. When
`engaged: true`, every scope answers `reactive` for background dispatch (§4).
Per-agent `autonomy` fields are never mutated: the override is an OVERLAY.
Clearing the override restores each agent's declared behavior without a
restore log.

This module is intentionally tiny — a single dict at rest — and stays small
so it can never fail closed for reasons unrelated to autonomy. File absent =
clear. Corrupt JSON = engaged (fail-closed), per §6.

C3 wires the read + write endpoints (routes/autonomy.py) and the dispatch
shim (scripts/dispatch_triggers.sh) to consult this file; this stub is the
minimum surface `services/triggers.effective_autonomy` depends on before C3
lands.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


WORKSHOP_ROOT = Path(os.environ.get("WORKSHOP_ROOT", str(Path(__file__).resolve().parents[2])))
OVERRIDE_PATH = WORKSHOP_ROOT / ".runtime" / "autonomy-override.json"

# The older off-switch: an empty flag file, still what actually gates the shell
# worker loop (cron_agent.sh, queue_runner.sh, project_ralph.sh) — none of which
# has ever read the JSON overlay above. So "is autonomy on?" had two answers in
# two formats, and asking the wrong one gave the wrong answer: the API could
# report autonomy clear while the flag kept every tick skipping, which is
# precisely how "the toggle does nothing" happens.
#
# Unified on the READ side only, and as a UNION. Adding the flag here can make
# this function answer "engaged" more often but never less, so it cannot open a
# gate that was previously shut — the one direction a kill switch is allowed to
# move under refactoring. The shell gates are left exactly as they are; nothing
# here changes what runs.
LEGACY_FLAG = WORKSHOP_ROOT / ".runtime" / "AUTONOMY_DISABLED"

_CLEAR: dict[str, Any] = {
    "engaged": False,
    "reason": None,
    "engaged_at": None,
    "engaged_by": None,
}


def _legacy_engaged() -> bool:
    """Is the older flag-file off-switch present? See LEGACY_FLAG."""
    try:
        return LEGACY_FLAG.exists()
    except OSError:
        return True  # cannot tell = treat as off, same fail-closed rule


def read() -> dict[str, Any]:
    """Return the current override state. Missing file = clear; corrupt file =
    engaged (fail-closed per autonomy-model §6).

    Reports the UNION with the legacy flag file, so one call answers "is
    background work permitted" for the whole system rather than for one of the
    two switches that gate it."""
    legacy = _legacy_engaged()
    try:
        raw = OVERRIDE_PATH.read_text()
    except FileNotFoundError:
        if legacy:
            return {"engaged": True, "reason": "legacy AUTONOMY_DISABLED flag present",
                    "engaged_at": None, "engaged_by": "operator"}
        return dict(_CLEAR)
    except OSError:
        # Filesystem trouble is treated as engaged: an operator investigating
        # storage problems should not have autonomous work compounding them.
        return {"engaged": True, "reason": "override file unreadable", "engaged_at": None, "engaged_by": "system"}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"engaged": True, "reason": "override file JSON invalid", "engaged_at": None, "engaged_by": "system"}
    if not isinstance(data, dict):
        return {"engaged": True, "reason": "override file not an object", "engaged_at": None, "engaged_by": "system"}
    engaged = bool(data.get("engaged"))
    if legacy and not engaged:
        # The JSON says clear but the flag file still gates the shell loop. Say
        # what is actually true, and name the switch that has to be cleared.
        return {"engaged": True, "reason": "legacy AUTONOMY_DISABLED flag present",
                "engaged_at": None, "engaged_by": "operator"}
    return {
        "engaged": engaged,
        "reason": data.get("reason"),
        "engaged_at": data.get("engaged_at"),
        "engaged_by": data.get("engaged_by"),
    }


def is_engaged() -> bool:
    """Convenience: True when background dispatch should be skipped."""
    return bool(read().get("engaged"))
