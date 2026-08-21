"""Service layer for `/apps` and its nested collections
(app-is-the-entity-2026-08-20.md, stage B).

THE SPLIT THAT MATTERS. The `app` row itself and its chats are real Postgres
data (`db/app_repo.py`) -- a real, indexed, queryable relation, per the design
doc's sec 4.2. Tickets and notes are served straight off the file stores this
installation already has, resolved by slug (and, where one exists, the app's
`project_slug` link) through `services/app_links.py`; runs, deploys and
roster agents the same way, through the linked project_slug. NOTHING here
fabricates data: a collection an app genuinely has none of returns an empty
list, never another app's rows relabelled as this one's. That specific
failure mode -- pointing an app endpoint at project data and calling it
app data -- is the one this module exists to not repeat.

An app's ROSTER AGENTS (this collection) are not its chat persona. See
`services/app_persona.py`'s own docstring: an app persona is "not a roster
agent". `agents_for_app` returns agents from `services/agents_registry.py`
linked by `project_slug`, mirroring how the project build view lists an
app's agents today. If a caller also wants the persona, that is a different,
smaller piece of data (`services/app_persona.py` / `chat_context`), and
`routes/apps.py` exposes it under a distinctly-named field, never merged into
this list.
"""
from __future__ import annotations

from datetime import timezone
from pathlib import Path
from typing import Any

from services import tenancy

__all__ = [
    "AppsUnavailable", "AppNotFound", "SlugTaken", "AppFilesNoRoot",
    "list_apps", "get_app", "get_app_by_slug", "create_app", "update_app",
    "delete_app", "chats_for_app", "tickets_for_app", "notes_for_app",
    "runs_for_app", "deploys_for_app", "agents_for_app",
    "file_tree_for_app", "file_content_for_app", "write_file_for_app",
]


class AppsUnavailable(RuntimeError):
    """The relational store is not configured or not reachable.

    Apps are a Postgres entity as of stage A; unlike chats (which fold back
    to a file store when Postgres is down) there is no file fallback for the
    app roster itself, so this is reported rather than silently answering an
    empty list -- an empty `/apps` because the database is down must not
    read the same as an empty `/apps` because nothing is installed.
    """


class AppNotFound(KeyError):
    pass


class SlugTaken(ValueError):
    pass


class AppFilesNoRoot(RuntimeError):
    """Raised by the file CONTENT/write routes (never the tree listing --
    that one reports the same fact as a 200 with `reason`, per the design
    instruction to never 404 "this app has no tree") when an app has no file
    root to read or write against at all."""


def _require_db() -> tuple[Any, Any]:
    if not tenancy.configured() or not tenancy.available():
        raise AppsUnavailable("no relational store configured/reachable")
    return tenancy.org_and_user()


def _iso(v):
    return None if v is None else v.astimezone(timezone.utc).isoformat(timespec="seconds")


def _shape(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "slug": row["slug"],
        "name": row["name"],
        "icon": row.get("icon"),
        "kind": row["kind"],
        "parent_id": str(row["parent_id"]) if row.get("parent_id") else None,
        "project_slug": row.get("project_slug"),
        "archived_at": _iso(row.get("archived_at")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


# --- the app entity itself ---------------------------------------------------

def list_apps(*, kind: str | None = None, archived: bool | None = None) -> list[dict]:
    from db import app_repo
    from db.engine import tenant_session
    org_id, _ = _require_db()
    with tenant_session(org_id) as s:
        rows = app_repo.list_apps(s, org_id, kind=kind, archived=archived)
    return [_shape(r) for r in rows]


def get_app(app_id: str) -> dict | None:
    from db import app_repo
    from db.engine import tenant_session
    org_id, _ = _require_db()
    with tenant_session(org_id) as s:
        row = app_repo.get_app(s, org_id, app_id)
    return None if row is None else _shape(row)


def get_app_by_slug(slug: str) -> dict | None:
    from db import app_repo
    from db.engine import tenant_session
    org_id, _ = _require_db()
    with tenant_session(org_id) as s:
        row = app_repo.get_app_by_slug(s, org_id, slug)
    return None if row is None else _shape(row)


def create_app(*, slug: str, name: str, icon: str | None = None,
               kind: str = "app", parent_id: str | None = None,
               project_slug: str | None = None) -> dict:
    """`project_slug` defaults to whatever `services.app_links` already
    computes for this slug -- the same merge rule the stage-A backfill
    applies to existing rows (design doc sec 2.1: a slug in both registries
    is one app, not two). A caller may still pass one explicitly (a brand
    new app genuinely created through this route has no registry entry yet
    to derive from)."""
    from db import app_repo
    from db.engine import tenant_session
    from services import app_links

    org_id, _ = _require_db()
    if project_slug is None:
        project_slug = app_links.linked_project_slug(slug)
    with tenant_session(org_id) as s:
        if app_repo.get_app_by_slug(s, org_id, slug) is not None:
            raise SlugTaken(f"an app with slug {slug!r} already exists")
        row = app_repo.upsert_app(s, org_id, slug=slug, name=name, icon=icon,
                                  kind=kind, parent_id=parent_id,
                                  project_slug=project_slug)
    return _shape(row)


def update_app(app_id: str, **fields: Any) -> dict | None:
    from db import app_repo
    from db.engine import tenant_session
    org_id, _ = _require_db()
    with tenant_session(org_id) as s:
        if app_repo.get_app(s, org_id, app_id) is None:
            return None
        if "slug" in fields and fields["slug"] is not None:
            other = app_repo.get_app_by_slug(s, org_id, fields["slug"])
            if other is not None and str(other["id"]) != str(app_id):
                raise SlugTaken(f"an app with slug {fields['slug']!r} already exists")
        row = app_repo.update_app(s, org_id, app_id, **fields)
    return None if row is None else _shape(row)


def delete_app(app_id: str) -> bool:
    from db import app_repo
    from db.engine import tenant_session
    org_id, _ = _require_db()
    with tenant_session(org_id) as s:
        return app_repo.delete_app(s, org_id, app_id)


# --- nested collections -------------------------------------------------------

def chats_for_app(app_id: str, *, archived: bool = False) -> list[dict] | None:
    """None means "no such app"; [] means "no chats yet", and the route
    tells the two apart (404 vs 200)."""
    from db import app_repo
    from db.engine import tenant_session
    from services.chat_store import DEFAULT_CHAT_MODE

    org_id, _ = _require_db()
    with tenant_session(org_id) as s:
        if app_repo.get_app(s, org_id, app_id) is None:
            return None
        rows = app_repo.list_chats_for_app(s, org_id, app_id, archived=archived)

    out = []
    for r in rows:
        title = r.get("title") or r.get("seed_title") or "New chat"
        out.append({
            "id": r.get("external_id") or str(r["id"]),
            "context_key": r.get("context_key"),
            "scope": r.get("scope"),
            "title": title,
            "mode": r.get("mode") or DEFAULT_CHAT_MODE,
            "model": r.get("model"),
            "agent_id": r.get("agent_slug"),
            "created_at": _iso(r.get("created_at")),
            "updated_at": _iso(r.get("updated_at")),
            "archived_at": _iso(r.get("archived_at")),
        })
    return out


def _app_or_404(app_id: str) -> dict:
    app = get_app(app_id)
    if app is None:
        raise AppNotFound(app_id)
    return app


def tickets_for_app(app_id: str) -> list[dict]:
    """The app's real tickets, off the JSON store (services.tasks), reached
    through its task-store scope (`app:<slug>`). Empty -- never an error --
    when the app has no linked project: `services.task_scope.root_for_context`
    returns None for an installed-only app, which is the honest answer (see
    `services/app_links.linked_project_slug`), not a 404 or a 500."""
    from services import task_scope, tasks

    app = _app_or_404(app_id)
    root = task_scope.root_for_context(f"app:{app['slug']}")
    if root is None:
        return []
    return tasks.list_tasks(root)


def notes_for_app(app_id: str) -> list[dict]:
    """The app's notes, off `services/knowledge.py`, which already resolves
    `app:<slug>` scopes today (HANDOFF-app-platform-and-github-2026-08-20.md
    sec "the problem, measured"). No project link required: a knowledge
    scope's directory is keyed by the app slug alone."""
    from services import knowledge

    app = _app_or_404(app_id)
    return knowledge.list_notes(f"app:{app['slug']}")


def runs_for_app(app_id: str, *, limit: int = 50) -> list[dict]:
    """The app's runs, off `services/jobs.py`, through the app's linked
    project (runs are `project_slug`-keyed files -- see the design doc's
    stage-A instruction to use the app<->project link rather than a second
    mapping). Empty when unlinked, same reasoning as `tickets_for_app`."""
    from services import app_links, jobs

    app = _app_or_404(app_id)
    project_slug = app_links.linked_project_slug(app["slug"])
    if not project_slug:
        return []
    records = jobs.read(project_slug=project_slug, limit=limit)
    try:
        from routes.jobs import _enrich, _name_map
        names = _name_map()
        cache: dict = {}
        return [_enrich(r, names, cache) for r in records]
    except Exception:  # noqa: BLE001 -- display enrichment is best-effort
        return records


def deploys_for_app(app_id: str) -> list[dict]:
    """The app's deploys, off `services/deploys.py`, through the linked
    project. Empty when unlinked."""
    from services import app_links, deploys

    app = _app_or_404(app_id)
    project_slug = app_links.linked_project_slug(app["slug"])
    if not project_slug:
        return []
    return deploys.list_by_project(project_slug)


def agents_for_app(app_id: str) -> list[dict]:
    """Roster agents LINKED to this app's project (maker agents with a
    matching `project_slug`), mirroring the project build view's agent list.
    NOT the app's chat persona -- see this module's docstring. Empty when the
    app has no linked project, since a roster agent's link is a project_slug
    and an unlinked app has none to match."""
    from services import agents_registry, app_links

    app = _app_or_404(app_id)
    project_slug = app_links.linked_project_slug(app["slug"])
    if not project_slug:
        return []
    return [a for a in agents_registry.list_agents()
            if a.get("project_slug") == project_slug]


# --- files (app-is-the-entity-2026-08-20.md sec 8) ---------------------------
#
# Same resolution as every other nested collection above: through the app's
# linked project (`services/app_links.linked_project_slug`), never a second
# mapping. Unlike tickets/notes/runs/deploys/agents, a missing root here is
# not always silently "empty" -- the tree LISTING reports it as a 200 with a
# `reason` (never a 404, per the contract: "this app has no tree" must never
# be confused with "this app does not exist" or fall back to another app's
# tree), while content read/write raise `AppFilesNoRoot`, since "read this
# file" with no file root at all is an honest 404, not an empty success.

def _file_root_for_app(app: dict) -> tuple[Path | None, str, str | None]:
    """(root, display_name, reason). `reason` is set exactly when `root` is
    None -- the app genuinely has no file tree. Never falls back to another
    app's or the platform's tree."""
    from services import app_files, app_links

    project_slug = app_links.linked_project_slug(app["slug"])
    if not project_slug:
        return None, app["slug"], "this app has no linked project, so it has no file tree"
    root = app_files.project_root(project_slug)
    if root is None:
        return None, app["slug"], (
            f"the app's linked project {project_slug!r} has no directory registered")
    if not root.is_dir():
        return None, app["slug"], (
            f"the app's linked project directory does not exist on disk: {root}")
    return root, root.name, None


def file_tree_for_app(app_id: str, path: str) -> dict:
    """Never 404s for "this app has no tree" -- see module note above. Can
    still raise `AppNotFound` (no such app), `AppFilePathError` (the path
    escapes), `FileNotFoundError`/`NotADirectoryError` (a bad subpath under a
    real root)."""
    from services import app_files

    app = _app_or_404(app_id)
    root, display, reason = _file_root_for_app(app)
    if root is None:
        return {"path": path, "root": display, "entries": [], "reason": reason}
    return app_files.list_tree(root, display, path)


def file_content_for_app(app_id: str, path: str) -> dict:
    from services import app_files

    app = _app_or_404(app_id)
    root, _display, reason = _file_root_for_app(app)
    if root is None:
        raise AppFilesNoRoot(reason)
    return app_files.read_file(root, path)


def write_file_for_app(app_id: str, path: str, content: str) -> dict:
    from services import app_files

    app = _app_or_404(app_id)
    root, _display, reason = _file_root_for_app(app)
    if root is None:
        raise AppFilesNoRoot(reason)
    return app_files.write_file(root, path, content)
