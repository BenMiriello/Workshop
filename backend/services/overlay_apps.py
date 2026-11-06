"""Apps that open over any page: the `overlay` block of an app's manifest.

An app declares, in its own `workshop.app.json`,

    "overlay": {"service": "app", "path": "/", "title": "...", "shortcut": "Mod+Alt+V"}

and the shell can open that service at `path` in a centred window over
whatever page the operator is on, from the shortcut (the operator may change
it per device) or from the shell's menu. The contract, including the messages
the framed app and the shell exchange, is docs/declaring-an-app-surface.md
("Opening over any page: overlay").

Tolerant, like the `events` block: a block without a service, or one that is
not an object, declares nothing, and an unreadable shortcut is dropped rather
than failing the block.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote

from services import app_events, app_runner

log = logging.getLogger("workshop.overlay_apps")

__all__ = ["frame_url", "parse_overlay", "parse_shortcut", "running_overlays"]

_MODIFIERS = {"mod", "ctrl", "control", "alt", "option", "shift", "meta", "cmd"}
_HOLDS = {"mod", "ctrl", "control", "alt", "option", "meta", "cmd"}
_NAMED_KEYS = {"space", "enter", "tab", "backspace", "up", "down", "left", "right",
               "-", "=", "[", "]", "\\", ";", "'", ",", ".", "/", "`"}
_F_KEY = re.compile(r"^f([1-9]|1[0-9]|2[0-4])$")
_SERVICE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def parse_shortcut(raw: object) -> str | None:
    """`raw` when it is a chord the shell can bind, else None.

    A chord is modifiers then one key, joined by `+` ("Mod+Alt+V"). `Mod` is
    Cmd on a Mac and Ctrl elsewhere. It must hold Mod, Ctrl, Alt or Cmd, or be
    a function key: anything else would fire while the operator types."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    parts = [p.strip() for p in text.split("+")]
    if any(not p for p in parts):
        return None
    *mods, key = parts
    low = [m.lower() for m in mods]
    if any(m not in _MODIFIERS for m in low) or len(set(low)) != len(low):
        return None
    k = key.lower()
    single = len(key) == 1 and (key.isalnum() or key in _NAMED_KEYS)
    if not (single or k in _NAMED_KEYS or _F_KEY.match(k)):
        return None
    if not _F_KEY.match(k) and not (set(low) & _HOLDS):
        return None
    return "+".join(parts)


def parse_overlay(raw: object) -> dict | None:
    """A manifest's `overlay` block as {service, path, title, shortcut,
    settings}, or None.

    `title` is None when the block names none (the caller uses the app's own
    name), and so is `shortcut` when absent or unreadable. `settings` is true
    when the app opens its own settings on `workshop/v1/openSettings`, so the
    shell offers a settings button in the overlay's title row."""
    block = raw.get("overlay") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return None
    service = str(block.get("service") or "").strip()
    if not _SERVICE.match(service):
        return None
    path = str(block.get("path") or "/").strip() or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    title = block.get("title")
    title = title.strip()[:80] if isinstance(title, str) and title.strip() else None
    return {"service": service, "path": path, "title": title,
            "shortcut": parse_shortcut(block.get("shortcut")),
            "settings": block.get("settings") is True}


def frame_url(slug: str, service: str, path: str) -> str:
    """Where the shell frames the service: the runner's proxy on this origin."""
    return (f"/api/base-apps/run/{quote(slug, safe='')}/proxy/"
            f"{quote(service, safe='')}/{path.lstrip('/')}")


def running_overlays() -> list[dict]:
    """Every app the runner has up whose overlay service is running, as
    {slug, title, shortcut, url, events}, sorted by title. `events` names the
    Workshop events it subscribes to, so the shell can point at the app that
    keeps, say, dictations, without naming it."""
    found: list[dict] = []
    for slug in list(app_runner._STATE.keys()):
        try:
            if app_runner.is_self_project(slug):
                continue
            raw = app_runner._read_manifest_file(app_runner._project_dir(slug))
            decl = parse_overlay(raw)
            if decl is None:
                continue
            status, _port = app_runner.service_target(slug, decl["service"])
        except Exception as e:  # noqa: BLE001 - one unreadable app skips only itself
            log.debug("overlay: %r skipped: %s", slug, e)
            continue
        if status != "running":
            continue
        name = raw.get("name") if isinstance(raw, dict) else None
        found.append({
            "slug": slug,
            "title": decl["title"] or (name if isinstance(name, str) and name.strip() else slug),
            "shortcut": decl["shortcut"],
            "url": frame_url(slug, decl["service"], decl["path"]),
            "events": sorted(app_events.subscriptions(raw)),
            "settings": decl["settings"],
        })
    return sorted(found, key=lambda o: o["title"].lower())
