"""The storage settings: one document per installation.

Absent keys read as the defaults, and an unset limit means "the disk, less the
keep-free floor". A plan quota (`installation.storage_quota_gb`) caps whatever
limit is set here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from services import installation
from wsplatform import paths

DEFAULTS: dict = {
    "limit_gb": None,
    "keep_free_gb": None,
    "retention_days": 14,
    "trash_days": 30,
    "apps": {},
}
KEEP_FREE_SHARE = 0.10
KEEP_FREE_MIN_GB = 20.0
# A share of a large disk is more room than anything here needs kept back: 10%
# of 1.8T is 180 GB, which a working disk sits under permanently, so a floor
# that size would read as "always short" and empty the trash on every pass.
KEEP_FREE_MAX_GB = 50.0
GB = 1024 ** 3


def path() -> Path:
    return paths.runtime_dir() / "storage" / "policy.json"


def read() -> dict:
    try:
        stored = json.loads(path().read_text())
    except (OSError, json.JSONDecodeError):
        stored = {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def _gb(value: object, name: str, *, minimum: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise ValueError(f"{name} must be a number of at least {minimum}, or null")
    return float(value)


def _days(value: object, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 3650:
        raise ValueError(f"{name} must be a whole number of days from {minimum} to 3650")
    return value


def _app(slug: str, value: object) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"apps.{slug} must be an object or null")
    return {"protect": bool(value.get("protect", False)),
            "cap_gb": _gb(value.get("cap_gb"), f"apps.{slug}.cap_gb", minimum=0.1)}


def update(changes: dict) -> dict:
    """Merge `changes` and save. An app set to null drops its overrides."""
    doc = read()
    for key, value in changes.items():
        if key == "limit_gb":
            doc[key] = _gb(value, key, minimum=1)
        elif key == "keep_free_gb":
            doc[key] = _gb(value, key, minimum=0)
        elif key == "retention_days":
            doc[key] = _days(value, key, minimum=1)
        elif key == "trash_days":
            doc[key] = _days(value, key, minimum=0)
        elif key == "apps" and isinstance(value, dict):
            apps = dict(doc["apps"])
            for slug, entry in value.items():
                shaped = _app(slug, entry)
                if shaped is None:
                    apps.pop(slug, None)
                else:
                    apps[slug] = shaped
            doc["apps"] = apps
        else:
            raise ValueError(f"unknown storage setting {key!r}")
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, target)
    return doc


def keep_free_bytes(doc: dict, disk_total: int) -> int:
    if doc.get("keep_free_gb") is not None:
        return int(doc["keep_free_gb"] * GB)
    share = disk_total * KEEP_FREE_SHARE
    return int(min(max(share, KEEP_FREE_MIN_GB * GB), KEEP_FREE_MAX_GB * GB))


def limit_bytes(doc: dict) -> int | None:
    """The managed-bytes budget, or None when only the keep-free floor bounds it."""
    quota = installation.storage_quota_gb()
    caps = [v for v in (doc.get("limit_gb"), quota) if v is not None]
    return int(min(caps) * GB) if caps else None
