"""What happens to a copy made in the Workshop, kept on the server per user so
every device follows the same choice: `<user runtime>/copies/prefs.json`.

`save`: whether copies are reported to the apps subscribed to them.
`destination`: "system" puts a Workshop copy on the system clipboard as well,
"workshop" keeps it in the Workshop (frontend/src/lib/copyPrefs.ts holds it
there). Whether a native app also saves copies made in other apps is the
device's own choice and is not kept here.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from services import ownership

DESTINATIONS = ("system", "workshop")
DEFAULTS = {"save": True, "destination": "system"}

_LOCK = threading.Lock()


def store_path() -> Path:
    return ownership.user_runtime_dir() / "copies" / "prefs.json"


def _shape(data: dict) -> dict:
    save = data.get("save")
    dest = data.get("destination")
    return {
        "save": save if isinstance(save, bool) else DEFAULTS["save"],
        "destination": dest if dest in DESTINATIONS else DEFAULTS["destination"],
    }


def _load() -> dict:
    try:
        data = json.loads(store_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def current() -> dict:
    with _LOCK:
        return _shape(_load())


def update(save: bool | None = None, destination: str | None = None) -> dict:
    """Change what is given; anything left out keeps its value."""
    with _LOCK:
        prefs = _shape(_load())
        if save is not None:
            prefs["save"] = save
        if destination is not None:
            prefs["destination"] = destination
        path = store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(prefs))
        os.replace(tmp, path)
        return prefs
