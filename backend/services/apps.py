"""Service layer for `/apps` and its nested collections
(app-is-the-entity-2026-08-20.md, stage B).

THE SPLIT THAT MATTERS. The `app` row itself and its chats are real Postgres
data (`db/app_repo.py`) -- a real, indexed, queryable relation, per the design
doc's sec 4.2. Tickets and notes are served straight off the file stores this
installation already has, resolved by slug (and, where one exists, the app's
`project_slug` link) through `services/app_links.py`; runs and roster
agents the same way, through the linked project_slug. NOTHING here
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

import logging
from datetime import timezone
from typing import Any

from services import app_links, app_registry, tenancy

log = logging.getLogger("workshop.apps")

__all__ = [
    "AppsUnavailable", "AppNotFound", "SlugTaken",
    "reconcile_from_registries",
    "list_apps", "get_app", "get_app_by_slug", "create_app", "update_app",
    "delete_app", "chats_for_app", "tickets_for_app", "notes_for_app",
    "runs_for_app", "agents_for_app",
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
        "dev_only": bool(row.get("dev_only", False)),
        "parent_id": str(row["parent_id"]) if row.get("parent_id") else None,
        "project_slug": row.get("project_slug"),
        "archived_at": _iso(row.get("archived_at")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


# --- keeping the table in step with the file registries ----------------------

def _display_name(slug: str, project_title: str | None) -> str:
    """The card title if the catalog ships one, else the project's own title,
    else a title-cased slug. Never a hardcoded per-app string -- every branch
    reads a name FROM something, and the fallback is generic string
    manipulation, not a lookup table of app names."""
    spec = app_registry.BUILTIN_APPS.get(slug)
    if spec is not None and spec.card and spec.card.get("title"):
        return str(spec.card["title"])
    if project_title and project_title != slug:
        return project_title
    return slug.replace("-", " ").replace("_", " ").title()


def _icon(slug: str, project_icon: str | None = None) -> str | None:
    """The catalog card's icon if this app ships one, else the icon the linked
    project DECLARES for itself.

    The second branch is the whole point. This read the built-in catalog and
    nothing else, so an app that does not ship UI in this bundle was iconless BY
    CONSTRUCTION: no declaration could ever reach it, and the frontend fell back
    to a two-letter monogram among a row of glyphs. That is the platform
    refusing to read a declaration, which is the same failure as the platform
    hardcoding one.

    Still names no app. Both branches read a value FROM something."""
    spec = app_registry.BUILTIN_APPS.get(slug)
    if spec is not None and spec.card and spec.card.get("icon"):
        return str(spec.card["icon"])
    icon = (project_icon or "").strip()
    return icon or None


def desired_rows() -> dict[str, dict]:
    """slug -> the `app` row the registries say should exist, right now.

    The ONE place the registry merge becomes column values, so the hand-run
    backfill (`db/app_backfill.py`, which imports this) and the live reconcile
    below cannot disagree about what a row should hold. Reads only files;
    touches no database.
    """
    roster = app_links.load()
    out: dict[str, dict] = {}
    for slug in app_links.all_app_slugs():
        project_slug = app_links.linked_project_slug(slug)
        entry = (roster.projects.get(project_slug) or {}) if project_slug else {}
        out[slug] = {
            "name": _display_name(slug, entry.get("title")),
            "icon": _icon(slug, entry.get("icon")),
            "project_slug": project_slug,
            "dev_only": app_links.is_self_slug(slug),
        }
    return out


def reconcile(org_id: str) -> dict:
    """Bring the `app` table into step with the two file registries, and ARCHIVE
    the rows the registries no longer name.

    WRITES ONLY WHAT DIFFERS, because this runs on a read path (`list_apps`) as
    well as after every projects-registry write. The steady state is one SELECT
    and no UPDATE.

    TAKES `org_id` RATHER THAN RESOLVING IT. `owner_session` is the admin
    connection and the serving process deliberately does not hold it -- the same
    trap `services/tenancy.org_for_owner` records having fallen into, where
    every resolution raised in the running backend and passed under pytest. The
    caller supplies the tenant it is already acting for.

    A DEPARTED APP IS ARCHIVED, NOT DELETED. `archived_at` is set instead of the
    row being removed, because the row is what the app's chats and tickets are
    keyed to (`chat_thread.app_id` FKs to it); dropping it would orphan them,
    and re-adding the app later would mint a NEW id, which is exactly the
    rename-is-a-fork bug the surrogate key exists to prevent. An app that comes
    back is un-archived by the next reconcile and keeps everything it owned.

    IT NEVER OVERWRITES A LABEL ON A ROW THAT ALREADY EXISTS. `slug`, `name` and
    `icon` are the user's to set (`PATCH /apps/{id}`, design doc sec 5.1: "a
    rename is a PATCH and not a migration"), and this loop runs on a read path.
    A reconcile that re-derived them would silently undo the rename on the very
    next request -- and, worse, it matches an existing row by the app's REGISTRY
    LINK (`project_slug`) before its slug, so a renamed app is recognised as
    itself rather than treated as a departure plus an arrival, which would
    archive its chats' owner and mint a new id for the same app. What a
    reconcile does write on an existing row is exactly the two DERIVED facts:
    the registry link and `dev_only`, plus a label that is still unset.

    `python -m db.app_backfill --apply` remains the force-resync: it upserts
    every field from the registries. That is the deliberate, hand-run sledge-
    hammer; this is the live loop, and the live loop is conservative.
    """
    from datetime import datetime

    from db import app_repo
    from db.engine import tenant_session

    desired = desired_rows()
    now = datetime.now(timezone.utc)
    created: list[str] = []
    written: list[str] = []
    archived: list[str] = []
    restored: list[str] = []

    with tenant_session(org_id) as s:
        rows = app_repo.list_apps(s, org_id, dev_only=None)
        by_slug = {r["slug"]: r for r in rows}
        by_project = {r["project_slug"]: r for r in rows if r.get("project_slug")}

        matched: set[str] = set()
        for slug, want in desired.items():
            row = by_project.get(want["project_slug"]) if want["project_slug"] else None
            if row is None:
                row = by_slug.get(slug)
            if row is None:
                app_repo.upsert_app(s, org_id, slug=slug, **want)
                created.append(slug)
                continue
            matched.add(row["slug"])
            fields = {k: want[k] for k in ("project_slug", "dev_only")
                      if row.get(k) != want[k]}
            # A label the user has never set is not a label the user set.
            for k in ("name", "icon"):
                if not row.get(k) and want[k]:
                    fields[k] = want[k]
            if fields:
                app_repo.update_app(s, org_id, row["id"], **fields)
                written.append(row["slug"])
            if row.get("archived_at"):
                app_repo.update_app(s, org_id, row["id"], archived_at=None)
                restored.append(row["slug"])

        for slug, row in by_slug.items():
            if slug not in matched and not row.get("archived_at"):
                app_repo.update_app(s, org_id, row["id"], archived_at=now)
                archived.append(slug)

    return {"apps": sorted(desired), "count": len(desired),
            "created": sorted(created), "written": sorted(written),
            "archived": sorted(archived), "restored": sorted(restored)}


def reconcile_from_registries() -> dict:
    """Re-derive the `app` roster from the two file registries. Never raises.

    THE ONE REGISTRY IS THE TABLE; the two files are still where an install and
    a project are WRITTEN, so something has to carry a change from them into it.
    Until this existed nothing did: the rows were written by a person running
    `python -m db.app_backfill --apply` and by nothing else, so an app installed
    or a project created after that run had no row, `/apps` did not list it, and
    its chats, tickets and notes had no owner -- with no error anywhere, because
    a stale roster is shaped exactly like a current one. That is the same defect
    as a silent empty read, only slower to see.

    Called from `GET /apps` (so a read is never served off a stale roster) and
    from `routes/projects._save_registry` (so a project write lands in the table
    immediately). Both run inside a request, which is what supplies the tenant:
    `owner_session` is the ADMIN connection and the serving process
    deliberately does not hold it, so there is no owner-less path to reconcile
    on -- see the same lesson recorded in `services/tenancy.org_for_owner`.

    Returns `{"ok": False, "reason": ...}` rather than raising when there is no
    relational store, no acting owner, or the write fails: an instance with no
    database is a supported instance, and reconciliation is a background
    correctness concern that must never turn a working read into an error.
    """
    if not tenancy.configured() or not tenancy.available():
        return {"ok": False, "reason": "no relational store configured/reachable"}
    try:
        org_id, _ = tenancy.org_and_user()
    except Exception as e:
        return {"ok": False, "reason": f"no acting tenant: {type(e).__name__}: {e}"}
    try:
        result = reconcile(str(org_id))
    except Exception as e:
        log.warning("app registry reconcile failed: %s", e)
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
    if any(result.get(k) for k in ("created", "written", "archived", "restored")):
        log.info("app registry reconciled: created %s; updated %s; archived %s; "
                 "restored %s", result["created"], result["written"],
                 result["archived"], result["restored"])
    return {"ok": True, **result}


# --- the app entity itself ---------------------------------------------------

def list_apps(*, kind: str | None = None, archived: bool | None = None) -> list[dict]:
    from db import app_repo
    from db.engine import tenant_session
    from wsplatform import dev_mode
    org_id, _ = _require_db()
    dev_only_filter: bool | None = None if dev_mode.dev_mode_on() else False
    with tenant_session(org_id) as s:
        rows = app_repo.list_apps(s, org_id, kind=kind, archived=archived,
                                  dev_only=dev_only_filter)
    return [_shape(r) for r in rows]


def get_app(app_id: str) -> dict | None:
    from db import app_repo
    from db.engine import tenant_session
    from wsplatform import dev_mode
    org_id, _ = _require_db()
    with tenant_session(org_id) as s:
        row = app_repo.get_app(s, org_id, app_id)
    if row is None:
        return None
    if row.get("dev_only") and not dev_mode.dev_mode_on():
        return None
    return _shape(row)


def get_app_by_slug(slug: str) -> dict | None:
    from db import app_repo
    from db.engine import tenant_session
    from wsplatform import dev_mode
    org_id, _ = _require_db()
    with tenant_session(org_id) as s:
        row = app_repo.get_app_by_slug(s, org_id, slug)
    if row is None:
        return None
    if row.get("dev_only") and not dev_mode.dev_mode_on():
        return None
    return _shape(row)


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

    # THE SAME SUMMARY THE GLOBAL CHAT LIST SHOWS, not a thinner one.
    #
    # These rows used to carry title/model/timestamps and nothing else, so the
    # per-app Chats tab could not show a preview, a turn count, or whether a
    # turn was running. It rendered a grid of bare titles instead, which is how
    # one conversation came to look like a different kind of object depending
    # on which page you opened it from.
    #
    # WHICH chats belong to this app is the DATABASE's answer (chat_thread.
    # app_id, resolved by the backfill from scope), and it is broader than any
    # scope filter: an app's chats can sit under several context keys. WHAT
    # each one looks like is the file fold's answer, and it is the same builder
    # `GET /api/chat/threads` uses, so the two lists cannot describe the same
    # chat differently. A row the fold does not have still comes back, thin,
    # rather than vanishing from its app.
    from services import chat_store

    try:
        fold = {t.get("id"): t for t in chat_store.list_threads(archived=archived)}
    except Exception:  # pragma: no cover - a chat listing must not 500 an app
        log.warning("could not read the chat fold for app %s", app_id, exc_info=True)
        fold = {}

    out = []
    for r in rows:
        tid = r.get("external_id") or str(r["id"])
        title = r.get("title") or r.get("seed_title") or "New chat"
        row = {
            "id": tid,
            "context_key": r.get("context_key"),
            "scope": r.get("scope"),
            "title": title,
            "mode": r.get("mode") or DEFAULT_CHAT_MODE,
            "model": r.get("model"),
            "agent_id": r.get("agent_slug"),
            "created_at": _iso(r.get("created_at")),
            "updated_at": _iso(r.get("updated_at")),
            "archived_at": _iso(r.get("archived_at")),
            "message_count": 0,
            "last_message": "",
            "last_user_at": None,
            "branch_slug": None,
        }
        rich = fold.get(tid)
        # A ROW THE FOLD DOES NOT HAVE IS NOT A CHAT. It is skipped.
        #
        # This module's docstring says nothing here fabricates data, and the
        # first version of this merge did: it kept such a row "thin, rather
        # than vanishing from its app", on the theory that the DB knew about a
        # conversation the file store had not caught up with. Measured
        # 2026-08-31, that theory was wrong in 59 of 126 rows on one app. Every
        # one of them carried the backfill's own timestamp, an empty title
        # rendered as "New chat", no messages, and `GET /chat/threads/<id>`
        # answering 404 -- so the tab listed 59 conversations that could not be
        # opened, while the global chat list, which reads the fold, correctly
        # showed none of them.
        #
        # The fold is the store that can actually serve a thread, so it decides
        # what exists. The DB still decides WHICH app a chat belongs to, which
        # is the part it is authoritative for and the reason this merge exists.
        if rich is None:
            continue
        row.update({
            "title": rich.get("title") or title,
            "model": rich.get("model") or row["model"],
            "updated_at": rich.get("updated_at") or row["updated_at"],
            "message_count": rich.get("message_count") or 0,
            "last_message": rich.get("last_message") or "",
            "last_user_at": rich.get("last_user_at"),
            "branch_slug": rich.get("branch_slug"),
        })
        out.append(row)
    # Newest conversation first, by when the operator last spoke, which is the
    # order the global list uses. Falls back to updated_at for a row the fold
    # did not have.
    out.sort(key=lambda t: t.get("last_user_at") or t.get("updated_at") or "", reverse=True)
    return chat_store.stamp_running(out)


def _app_or_404(app_id: str) -> dict:
    app = get_app(app_id)
    if app is None:
        raise AppNotFound(app_id)
    return app


def owned_scope(app: dict) -> str:
    """The scope key this app OWNS, resolved rather than spelled.

    `app:<slug>` was hardcoded at three call sites. That is right for an
    ordinary installed app and not universally: an app whose linked project is
    a ROOT scope owns that root scope, and `app:<its slug>` would name a
    directory nothing writes to, which is why such an app's pages opened empty.
    That is not a special case for one app, it is what the project link has
    always meant, so it resolves through the same
    `scope_tree.scope_for_context` every other scope question uses. An
    ordinary app resolves to an `app:`-prefixed scope; a root-scoped app
    resolves to the root scope itself.

    Spelling any slug here would put an app's name in platform source, which
    scripts/check_no_app_names.py counts and CLAUDE.md forbids -- including in
    a docstring example, which is how this function first failed that gate.
    """
    from services import scope_tree

    slug = app["slug"]
    link = app.get("project_slug")
    if link:
        resolved = scope_tree.scope_for_context(link)
        # ONLY when the link names a root scope IN ITS OWN RIGHT. `resolved ==
        # link` is exactly that test: a root kind resolves to itself, while an
        # ordinary app resolves to an `app:`-prefixed scope and an unknown slug
        # falls through to the workshop root. Without the equality check the
        # fallback silently pointed a linked app at `workshop`, which is another
        # app's data, and is the failure this module's docstring exists to not
        # repeat.
        if resolved == link:
            return resolved
    return f"app:{slug}"


def tickets_for_app(app_id: str) -> list[dict]:
    """The app's real tickets, off the JSON store (services.tasks), reached
    through its task-store scope (`app:<slug>`). Empty -- never an error --
    when the app has no linked project: `services.task_scope.root_for_context`
    returns None for an installed-only app, which is the honest answer (see
    `services/app_links.linked_project_slug`), not a 404 or a 500."""
    from services import task_scope, tasks

    app = _app_or_404(app_id)
    root = task_scope.root_for_context(owned_scope(app))
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
    return knowledge.list_notes(owned_scope(app))


def runs_for_app(app_id: str, *, limit: int = 50) -> list[dict]:
    """The app's runs, off `services/jobs.py`, through the app's linked
    project (runs are `project_slug`-keyed files -- see the design doc's
    stage-A instruction to use the app<->project link rather than a second
    mapping). Empty when unlinked, same reasoning as `tickets_for_app`."""
    from services import jobs

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
    raw = [a for a in agents_registry.list_agents()
           if a.get("project_slug") == project_slug]
    return [agents_registry.roster_projection(a) for a in raw]
