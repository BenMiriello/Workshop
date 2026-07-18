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

_CLEAR: dict[str, Any] = {
    "engaged": False,
    "reason": None,
    "engaged_at": None,
    "engaged_by": None,
}


def read() -> dict[str, Any]:
    """Return the current override state. Missing file = clear; corrupt file =
    engaged (fail-closed per autonomy-model §6)."""
    try:
        raw = OVERRIDE_PATH.read_text()
    except FileNotFoundError:
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
    return {
        "engaged": engaged,
        "reason": data.get("reason"),
        "engaged_at": data.get("engaged_at"),
        "engaged_by": data.get("engaged_by"),
    }


def is_engaged() -> bool:
    """Convenience: True when background dispatch should be skipped."""
    return bool(read().get("engaged"))
