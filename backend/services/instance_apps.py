"""This installation's own apps, as catalog entries, read from its config.

`app_registry` is the platform's built-in app library. An installation may add
apps of its own by listing them in `$WORKSHOP_CONFIG_DIR/instance-apps.json`
(default `~/.config/workshop`), which is untracked: which apps an installation
has, and where they are served, is that installation's configuration, never
source. `app_registry._build_catalog()` merges `SPECS` in; with no file there
are none and the platform catalog is unchanged.

    {"apps": [
      {"id": "<slug>", "title": "...", "tagline": "..."},
      {"id": "<slug>", "title": "...", "tagline": "...", "href": "https://..."}
    ]}

An entry without `href` is run by this platform and opens inside its own page
on the first screen its manifest exposes. An entry with `href` is served on its
own origin and its card links out. Being listed only makes an app installABLE;
installing it is still an `.runtime/installed-apps.json` edit.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from services.app_spec import AppSpec

log = logging.getLogger("workshop.instance_apps")

_PRIVATE = {"sensitivity": "private", "api_prefixes": [], "provides": [], "needs": []}

#: The launcher's generic glyph; an app with no icon of its own gets this.
_ICON = "app"


def _config_path() -> Path:
    base = os.environ.get("WORKSHOP_CONFIG_DIR") or str(Path.home() / ".config" / "workshop")
    return Path(base) / "instance-apps.json"


def _card(entry: dict) -> dict:
    card = {"title": entry.get("title") or entry["id"],
            "tagline": entry.get("tagline") or "", "icon": entry.get("icon") or _ICON}
    if entry.get("href"):
        card.update(href=entry["href"], external=True)
    return card


def _load() -> dict[str, AppSpec]:
    path = _config_path()
    if not path.is_file():
        return {}
    try:
        entries = json.loads(path.read_text(encoding="utf-8")).get("apps") or []
    except (OSError, ValueError, AttributeError) as e:
        log.warning("instance apps unreadable at %s: %s", path, e)
        return {}
    return {e["id"]: AppSpec(id=e["id"], identity=dict(_PRIVATE), card=_card(e))
            for e in entries if isinstance(e, dict) and e.get("id")}


SPECS: dict[str, AppSpec] = _load()
