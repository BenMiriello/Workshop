"""Which apps are installed on THIS instance (instance state, not platform code).

The platform ships a catalog of built-in apps (see `app_registry.py`) but installs
none of them. An app is "installed" only when its id appears in this instance's
installed-apps registry — a small JSON file that lives under `.runtime/`
(gitignored) so a fresh clone starts with an **empty** roster.

    { "installed": ["<app-id>", "<app-id>", ...] }

The example is deliberately generic. It listed one operator's seven real app
ids until 2026-09-04, which put a roster of somebody's personal apps in a
platform docstring that every installation ships -- the same defect as an app
name in platform code, written as prose.

Installing or removing an app is an edit to this file (or the `install()` /
`remove()` helpers), never an edit to any platform-core file. A missing file means
nothing is installed, which is the correct state for a fresh container.

A PRESENT FILE THAT CANNOT BE PARSED IS NOT THAT STATE, and the read no longer
pretends it is: it raises `RegistryUnreadable`. See that class for the outage
this distinction exists to make visible.
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


class RegistryUnreadable(RuntimeError):
    """The registry file is present and cannot be understood.

    NOT the same condition as an absent file, and the whole point of the class
    is that the two used to be. A missing file means "nothing is installed",
    which is the correct and intentional state of a fresh clone. A file that
    exists but cannot be parsed means "this instance's app roster is unknown",
    which is not a state anybody configured -- and answering `[]` for it served
    zero apps while looking exactly like a fresh clone, which is how an
    every-app-404 outage stood for two days in 2026-09.
    """


def installed_app_ids() -> list[str]:
    """Ordered list of installed app ids. A MISSING file => [] (blank slate);
    a PRESENT but unreadable one raises `RegistryUnreadable`.

    Order is preserved from the file and drives launcher card order. Duplicates
    and non-string entries are dropped.
    """
    if not REGISTRY_PATH.exists():
        return []
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RegistryUnreadable(
            f"{REGISTRY_PATH} exists but could not be read as the installed-apps "
            f"registry ({type(e).__name__}: {e}). Refusing to report an empty "
            f"roster, which is indistinguishable from an instance with no apps "
            f"installed.") from e
    raw = data.get("installed") if isinstance(data, dict) else None
    if raw is None and (not isinstance(data, dict) or "installed" not in data):
        raise RegistryUnreadable(
            f"{REGISTRY_PATH} has no `installed` list. The file is the roster; a "
            f"file without one is malformed, not an empty roster -- write "
            f'`{{"installed": []}}` to mean "no apps".')
    if not isinstance(raw, list):
        raise RegistryUnreadable(
            f"{REGISTRY_PATH} has an `installed` that is {type(raw).__name__}, "
            f"not a list.")
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
