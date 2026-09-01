"""What an app OPENS ON, and which of the platform's per-app tabs it has.

The platform ships four generic per-app tabs (work, chats, memory, files), each
backed by a real `/api/apps/{id}/...` collection. It shipped SEVEN until
2026-09-01 (chats, agents, memory, files, tickets, runs, deploys); tickets, runs
and deploys are now rows inside Work, and `agents` is retired as an object kind,
per docs/platform-redesign/threads-branches-and-pages-2026-08-29.md sections 6
and 7. Their ROUTES still resolve, because a bookmark is still a bookmark; what
changed is which tabs an app is offered.
Until now every app got all seven and opened on the first of them, so an app
with no bundled UI landed on a chat list it never chose and carried tabs that
could never hold anything for it. This module is the declaration side of the
fix: an app says what it opens on and which tabs it has, and the platform reads
that. No branch here names an app.

Resolution order, most specific first -- the same shape
`scripts/preview_config.py` already uses for the preview declaration:

  1. a `surface` block in the app's own manifest (`workshop.app.json`), read
     through `app_runner._read_manifest_file`, which is the ONE reader of that
     file and already owns the filename set and the jsonc tolerance.
  2. a `surface` block on the app's project registry record. A tree the
     operator made is a record and nothing else -- it has no manifest to carry
     the block and no catalog entry -- so its declaration lives beside the
     `title`, `icon` and `dir` it already declares there.
  3. the built-in catalog card's `route`. An app that ships its own screens
     already declares where they are; that route IS its home and it needs no
     second declaration.
  4. nothing. The app has no surface of its own, so it opens on the first tab
     the platform can actually serve it.

The block:

    "surface": {
      "home": "tickets",                        // optional
      "tabs": ["tickets", "runs", "files"]      // optional, ORDERED
    }

`home` may be a bare tab id ("tickets"), a path under the app ("/tickets"), or
a full path ("/apps/<slug>/tickets"); anything else is taken as written, so an
app may open on a screen of its own. Absent, it is the first tab that survives
the availability filter below.

`tabs` is the app's own list, in the order it wants them. Absent, the app takes
every tab it CAN have. Either way the answer is filtered by availability: a tab
whose collection can never be non-empty for this app is not offered at all.
That filter is the same fact `unlinkedReason` states in words on the frontend
(`frontend/src/pages/apps/tabs/appTabState.ts`), which is where the rule lives
and is tested; this module supplies the FACTS it reads (does this app have a
linked project tree, does it have a file root) plus the resolved home, so the
launcher card and the shell cannot disagree about where an app opens.
"""
from __future__ import annotations

import logging

log = logging.getLogger("workshop.apps")

__all__ = ["TAB_IDS", "RETIRED_TAB_IDS", "REQUIRES_LINKED_PROJECT", "declared_surface",
           "available_tabs", "surface_for"]

# The seven generic tabs, in the platform's own order. `frontend/src/pages/
# apps/tabs/appTabState.ts` holds the same order with the labels and the route
# suffixes; this list is the backend half and only ever answers "which ids
# exist", never how they are drawn.
# WORK IS FIRST, AND THAT IS THE POINT. `_resolve_home` opens an app on
# `tabs[0]`, so an app with a working tree now opens on what is being BUILT in
# it rather than on a chat list it never chose. The operator reported landing on
# "the old chats page" on 2026-08-29, 2026-08-31 and 2026-09-01; the first two
# rounds changed how that chat list renders, which was never the complaint.
TAB_IDS: tuple[str, ...] = (
    "work", "chats", "memory", "files",
)

# Tabs the platform used to offer and no longer draws. Kept as a NAMED set,
# not deleted silently, because an app may still declare one in its manifest
# and a declaration that names a retired tab must be dropped rather than
# resolved into a dead landing (see `surface_for`).
RETIRED_TAB_IDS: frozenset[str] = frozenset({
    "agents", "tickets", "runs", "deploys",
})

# Mirrors `services/apps.py` collection by collection. Work is the branch,
# ticket, run and change view, and every one of those resolves through
# `app_links.linked_project_slug`, so an app with no linked tree can never have
# it. Chats and notes (memory) resolve through the app's slug alone. Files is
# its own question -- an app may declare its own root with a manifest instead
# (`app_files._app_declared_root`) -- so it is answered by `_has_file_root`
# rather than by this map.
REQUIRES_LINKED_PROJECT: dict[str, bool] = {
    "work": True,
    "chats": False,
    "memory": False,
}


def _manifest_surface(slug: str) -> dict | None:
    """The `surface` block in the app's own tree manifest, if it has one."""
    try:
        from services import app_files, app_runner

        ws = app_files.resolve(f"app:{slug}")
        raw = app_runner._read_manifest_file(ws.root)
    except Exception:
        # A missing tree, an unreadable one, or a malformed manifest is not
        # this module's error to raise: an app that cannot say what it opens on
        # gets the platform's answer, which is always a real place.
        return None
    block = raw.get("surface") if isinstance(raw, dict) else None
    return block if isinstance(block, dict) else None


def _registry_surface(slug: str) -> dict | None:
    """The `surface` block on the app's project registry record."""
    try:
        from services import app_links

        rec = app_links.load().projects.get(slug)
    except Exception:
        return None
    block = rec.get("surface") if isinstance(rec, dict) else None
    return block if isinstance(block, dict) else None


def declared_surface(slug: str) -> dict | None:
    """The app's own declaration, or None when it makes none."""
    return _manifest_surface(slug) or _registry_surface(slug)


def _linked_project(slug: str) -> str | None:
    try:
        from services import app_links

        return app_links.linked_project_slug(slug)
    except Exception:
        return None


def _has_file_root(slug: str) -> bool:
    """Whether the Files tab can show anything: the app declares its own root
    with a manifest, or it has a linked working tree. `app_files.resolve`
    answers both in one call and raises when the answer is neither."""
    try:
        from services import app_files

        app_files.resolve(f"app:{slug}")
        return True
    except Exception:
        return False


def available_tabs(slug: str) -> list[str]:
    """Every tab that could hold something for this app, in platform order.

    This is availability, not population: a linked app with no tickets yet
    keeps its Tickets tab, because the next ticket filed against it goes
    there. What is dropped is the tab that can never be anything but empty.
    """
    project = _linked_project(slug)
    files = _has_file_root(slug)
    out: list[str] = []
    for tab in TAB_IDS:
        if tab == "files":
            if files:
                out.append(tab)
            continue
        if REQUIRES_LINKED_PROJECT.get(tab, False) and not project:
            continue
        out.append(tab)
    return out


def _resolve_home(raw: object, base: str, tabs: list[str]) -> str:
    """A declared home as a concrete path, or the first tab the app has."""
    if isinstance(raw, str) and raw.strip():
        home = raw.strip()
        if home.startswith("/apps/") or home.startswith("http"):
            return home
        if home.startswith("/"):
            return f"{base}{home}"
        return f"{base}/{home}"
    return f"{base}/{tabs[0]}" if tabs else base


def surface_for(slug: str, *, own_route: str | None = None) -> dict:
    """This app's resolved surface.

        {"home": "/apps/x/tickets",
         "tabs": ["tickets", "runs", "files", "chats"],
         "declared": True,
         "project_slug": "x" | None,
         "files": True}

    `own_route` is the built-in catalog card's route, when the app has one: an
    app that ships its own screens has already declared its home there, and a
    `surface` block still overrides it (an app may prefer a platform tab to its
    own landing screen, and the block is the more specific statement).
    """
    base = f"/apps/{slug}"
    available = available_tabs(slug)
    block = declared_surface(slug) or {}

    raw_tabs = block.get("tabs")
    if isinstance(raw_tabs, list):
        wanted = [t for t in raw_tabs if isinstance(t, str)]
        # The app's own order, filtered by what it can actually have. A tab it
        # names but cannot hold is dropped rather than rendered empty, which is
        # the whole point; a tab it does not name is not offered.
        tabs = [t for t in wanted if t in available]
        # A STALE DECLARATION IS THE THING THAT GOES WRONG HERE, and it goes
        # wrong QUIETLY, which is why this logs in every case rather than only
        # the fatal one.
        #
        # Measured 2026-09-01 on this installation: an app declared
        # `{"home": "tickets", "tabs": ["tickets","runs","files","agents",
        # "chats"]}`, written when those were the tabs. Filtered against the
        # current set that is ["files","chats"] -- non-empty, so nothing was
        # obviously broken, and the app had simply lost Work, its primary
        # surface, without a word anywhere. Declaring is narrowing, so honouring
        # it is right; being silent about a declaration that has partly expired
        # is not.
        retired = [t for t in wanted if t in RETIRED_TAB_IDS]
        if retired:
            log.warning(
                "app %s declares retired tab(s) %s; they are dropped. Its tabs "
                "are now %s. Update or remove the surface declaration.",
                slug, retired, tabs or "(none)")
        # AND IT MUST NOT STRAND THE APP ENTIRELY. An empty list sends
        # `_resolve_home` to the app's BASE path, which is the redirect that
        # lands back on itself, so the app would never open at all.
        if not tabs and retired:
            log.warning("app %s declares only retired tabs; using the platform set",
                        slug)
            tabs = available
    else:
        tabs = available

    home = block.get("home")
    if not isinstance(home, str) or not home.strip():
        home = own_route if own_route else None
    resolved = _resolve_home(home, base, tabs)

    return {
        "home": resolved,
        "tabs": tabs,
        "declared": bool(block) or bool(own_route),
        "project_slug": _linked_project(slug),
        "files": "files" in available,
    }
