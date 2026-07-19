"""Deploys-scope autonomy override.

Parallel to services/autonomy_override.py but a SEPARATE kill switch. The
workshop-wide override pauses cron / event-driven agent dispatch; the
deploys override pauses only deploy runs (manual OR event-triggered). The
two are independent because the failure modes are different: an operator
freezing merges during a mobile-release cut still wants agents helping, but
does NOT want anyone shipping to prod.

Model: `.runtime/deploys-autonomy-override.json`. When `engaged: true`,
routes/deploys.py returns 409 on :deploy and :rollback. Autonomous
(webhook-driven, Phase 4) deploys get queued as blocked rather than fired.

File absent = clear; corrupt = engaged (fail-closed).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


WORKSHOP_ROOT = Path(os.environ.get("WORKSHOP_ROOT", str(Path(__file__).resolve().parents[2])))
OVERRIDE_PATH = WORKSHOP_ROOT / ".runtime" / "deploys-autonomy-override.json"


_CLEAR: dict[str, Any] = {
    "engaged": False,
    "reason": None,
    "engaged_at": None,
    "engaged_by": None,
}


def read() -> dict[str, Any]:
    try:
        raw = OVERRIDE_PATH.read_text()
    except FileNotFoundError:
        return dict(_CLEAR)
    except OSError:
        return {"engaged": True, "reason": "override file unreadable", "engaged_at": None, "engaged_by": "system"}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"engaged": True, "reason": "override file JSON invalid", "engaged_at": None, "engaged_by": "system"}
    if not isinstance(data, dict):
        return {"engaged": True, "reason": "override file not an object", "engaged_at": None, "engaged_by": "system"}
    return {
        "engaged": bool(data.get("engaged")),
        "reason": data.get("reason"),
        "engaged_at": data.get("engaged_at"),
        "engaged_by": data.get("engaged_by"),
    }


def is_engaged() -> bool:
    return bool(read().get("engaged"))


def engage(reason: str | None, by: str | None = "operator") -> dict[str, Any]:
    from datetime import datetime, timezone
    OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "engaged": True,
        "reason": (reason or "").strip() or None,
        "engaged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engaged_by": by,
    }
    tmp = OVERRIDE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, OVERRIDE_PATH)
    return payload


def clear() -> dict[str, Any]:
    try:
        OVERRIDE_PATH.unlink()
    except FileNotFoundError:
        pass
    return dict(_CLEAR)
