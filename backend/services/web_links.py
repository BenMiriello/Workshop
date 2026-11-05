"""Where a web link in a chat opens: the system browser, or an installed app.

An app that opens web pages says so on the service that shows them, in its
manifest: `"opens": {"web": "#open={url}"}`, a suffix for that service's screen
(`/apps/<slug>/ui/<service>`) in which `{url}` stands for the page, encoded,
and an optional `{n}` for a number that differs on every click, so the same
link followed twice opens twice. `"native_pages": true` beside it says the app
shows web pages only inside the Workshop's native app, in its page views, so
in any other browser a link opens in a new window instead.
Which app, if any, opens the operator's web links is their choice, kept with
their preferences; with none chosen a link opens as it always did, in a new
window. Nothing here names an app: the declarations and the choice are data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from services import app_links, app_runner, user_prefs

log = logging.getLogger("workshop.web_links")

PREF_KEY = "web_link_app"
PLACEHOLDER = "{url}"


@dataclass(frozen=True)
class Handler:
    app: str
    label: str
    # The Workshop route with `{url}` in it, e.g. /apps/<slug>/ui/<service>#open={url}.
    route: str
    native_pages: bool = False


def _handler(app_slug: str) -> Handler | None:
    try:
        project = app_links.linked_project_slug(app_slug)
        if not project or app_runner.is_self_project(project):
            return None
        raw = app_runner._read_manifest_file(app_runner._project_dir(project))
        record = app_links.load().projects.get(project) or {}
    except Exception as e:
        log.debug("web links: no manifest readable for %r: %s", app_slug, e)
        return None
    for svc in (raw.get("services") if isinstance(raw, dict) else None) or []:
        if not isinstance(svc, dict) or not svc.get("expose"):
            continue
        opens = svc.get("opens") if isinstance(svc.get("opens"), dict) else {}
        suffix = str(opens.get("web") or "").strip()
        name = str(svc.get("name") or "").strip()
        if name and PLACEHOLDER in suffix and suffix[0] in "#?/":
            return Handler(app=app_slug, label=str(record.get("title") or app_slug),
                           route=f"/apps/{app_slug}/ui/{name}{suffix}",
                           native_pages=opens.get("native_pages") is True)
    return None


def handlers() -> list[Handler]:
    """Every installed app that declares it opens web pages, in roster order."""
    try:
        slugs = app_links.all_app_slugs()
    except Exception as e:
        log.warning("web links: roster unreadable: %s", e)
        return []
    return [h for h in (_handler(s) for s in slugs) if h is not None]


def chosen() -> Handler | None:
    """The app the operator's web links open in, or None for a new window. A
    choice whose app no longer declares it counts as none."""
    app = user_prefs.read().get(PREF_KEY)
    return _handler(app) if isinstance(app, str) and app else None


def choose(app: str | None) -> Handler | None:
    """Set the app web links open in; None for a new window. Raises ValueError
    for an app that does not open web pages."""
    if not app:
        user_prefs.write(PREF_KEY, None)
        return None
    handler = _handler(app)
    if handler is None:
        raise ValueError(f"{app!r} does not open web pages")
    user_prefs.write(PREF_KEY, app)
    return handler
