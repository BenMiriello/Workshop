"""Which apps are installed on THIS instance (instance state, not platform code).

The platform ships a catalog of built-in apps (see `app_registry.py`) but installs
none of them. An app is "installed" only when its id appears in this instance's
installed-apps registry — a small JSON file that lives under `.runtime/`
(gitignored) so a fresh clone starts with an **empty** roster.

    { "installed": ["finance", "budget", "commute", "location", "birds", "health", "wearables"] }

Installing or removing an app is an edit to this file (or the `install()` /
`remove()` helpers), never an edit to any platform-core file. A missing file means
nothing is installed, which is the correct state for a fresh container.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Default under .runtime/ (gitignored). Overridable so a deployment can point at
# instance state kept elsewhere. Never tracked in git.
REGISTRY_PATH = Path(os.environ.get(
    "WORKSHOP_INSTALLED_APPS",
    str(Path(__file__).resolve().parent.parent.parent / ".runtime" / "installed-apps.json"),
))


def installed_app_ids() -> list[str]:
    """Ordered list of installed app ids. Missing/corrupt file => [] (blank slate).

    Order is preserved from the file and drives launcher card order. Duplicates
    and non-string entries are dropped; the read never raises.
    """
    if not REGISTRY_PATH.exists():
        return []
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def is_installed(app_id: str) -> bool:
    return app_id in installed_app_ids()


def _write(ids: list[str]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"installed": ids}, indent=2) + "\n"
    tmp = REGISTRY_PATH.with_suffix(REGISTRY_PATH.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, REGISTRY_PATH)


def install(app_id: str) -> list[str]:
    """Add an app to the installed roster (no-op if already there). Returns the
    new roster. The app's routes/loops mount on the next backend start."""
    ids = installed_app_ids()
    if app_id not in ids:
        ids.append(app_id)
        _write(ids)
    return ids


def remove(app_id: str) -> list[str]:
    """Drop an app from the installed roster. Returns the new roster."""
    ids = [i for i in installed_app_ids() if i != app_id]
    _write(ids)
    return ids
