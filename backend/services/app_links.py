"""Where an app's identity comes from: the file registries, never a hardcoded
roster.

Same rule `scripts/check_no_app_names.py` enforces on platform code, applied
here to data instead: an app installed tomorrow, or a project created five
minutes ago, must resolve correctly without an edit to this file.

Two registries, still unlinked on disk
(docs/platform-redesign/app-is-the-entity-2026-08-20.md sec 2.1):
`.runtime/installed-apps.json` (`services.installed_apps`) says which apps
this instance has; `.runtime/projects.json` (`routes.projects`) says which
working trees it has. Five slugs -- budget, birds, location, commute,
notes-app, measured at design time -- exist in both today, and the design's
diagnosis is that each is ONE thing wearing two records, not two things that
happen to share a name.

This module is where that merge is COMPUTED, so `db/app_backfill.py` (which
writes the merge into `app.project_slug`) and `services/task_scope.py` (which
resolves an `app:<slug>` chat scope to a task-store root at request time,
so an app can own tickets) can never disagree about which apps are linked.
Both call `linked_project_slug`; neither owns a second copy of the rule.

Read-only and deliberately dependency-light: no sqlalchemy import, so a
process with no database driver installed can still resolve `app:<slug>` to
a working tree, which is `task_scope`'s own standing requirement (see
`relational_scope_for_root`'s docstring: "no database, no driver, no import
that can fail and be mistaken for file-backed").
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from services import installed_apps
from wsplatform import paths

__all__ = ["AppRoster", "load", "all_app_slugs", "linked_project_slug"]

# The platform's own dev-loop project (`self: true` in the registry, and the
# one name ADR 0014 reserves for the platform repo). It sits in the same file
# as every real project, but it is not something the operator installed as an
# app -- it is this checkout's own source -- so it is excluded from the app
# roster on that ground, the same way `scope_tree` gives `development` its
# own kind rather than treating it as a project like any other.
_SELF_PROJECT_SLUG = "development"


@dataclass(frozen=True)
class AppRoster:
    """Everything computed from the registries, read once per call so a
    mid-session install or project creation is picked up on the next call --
    no caching layered on top of the freshness contract
    `installed_apps.installed_app_ids()` and the projects registry already
    have."""
    installed: tuple[str, ...]     # from .runtime/installed-apps.json, in order
    projects: dict[str, dict]       # slug -> project record, minus the self project
    linked: dict[str, str]          # app slug -> project slug, where both exist


def _load_projects() -> dict[str, dict]:
    path = paths.projects_registry()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    projects = data.get("projects")
    return projects if isinstance(projects, dict) else {}


def load() -> AppRoster:
    installed = tuple(installed_apps.installed_app_ids())
    projects = {slug: rec for slug, rec in _load_projects().items()
                if slug != _SELF_PROJECT_SLUG}
    linked = {slug: slug for slug in installed if slug in projects}
    return AppRoster(installed=installed, projects=projects, linked=linked)


def all_app_slugs() -> list[str]:
    """Every slug that should have an `app` row: every installed app, plus
    every project not already covered by one.

    Design doc sec 3.1: project is retired as a second entity, so a project
    with no matching catalog app is still an app -- one with no launcher card,
    just a working tree. Installed apps come first, in catalog order (they
    are the ones with a real identity); project-only slugs follow,
    alphabetically, since nothing else orders them.
    """
    roster = load()
    project_only = sorted(set(roster.projects) - set(roster.installed))
    return [*roster.installed, *project_only]


def linked_project_slug(app_slug: str) -> str | None:
    """The working tree this app owns, or None if it has none.

    Two ways an app slug resolves to a project:

      1. It names BOTH an installed app and a project -- the coincidence
         sec 2.1 documents by name (budget, birds, location, commute,
         notes-app at design time).
      2. It is itself a bare project slug with no catalog entry -- a project
         the operator made that was never registered as an app card. It
         still owns its own tree, so it is still linked to itself.

    An installed app with no matching project (`finance`, `health`,
    `wearables` at design time) returns None: it genuinely has no working
    tree, no ticket queue and no deploys, and every stage-B route for it
    returns an honest empty list rather than borrowing another app's data.
    """
    roster = load()
    if app_slug in roster.linked:
        return roster.linked[app_slug]
    if app_slug in roster.projects and app_slug not in roster.installed:
        return app_slug
    return None
