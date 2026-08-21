"""Reads and writes for the `app` table (app-is-the-entity-2026-08-20.md,
stages A and B).

Same shape as `db/chat_repo.py`: every function takes a `Session` so a caller
can compose several operations in one transaction, and rows come back as
plain dicts straight off raw SQL -- the row shapes here are exactly what the
REST routes in `routes/apps.py` hand back, and an ORM identity map buys
nothing over that.

WHAT THIS MODULE DOES NOT DO. It only ever reads/writes the `app` table and
the `app_id` column on `chat_thread` (the two real Postgres collections from
the design's sec 4.2). Tickets, runs, deploys, notes and roster agents are
resolved from the file stores by `services/apps.py`, which calls this module
for the `app` row itself and then goes to `services/app_links.py` for the
rest. See that module's docstring for why.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from db.ids import uuid7

__all__ = [
    "upsert_app", "get_app", "get_app_by_slug", "list_apps", "update_app",
    "delete_app", "list_chats_for_app", "set_thread_app_id",
    "count_chats_for_app",
]


def _row(r: Any) -> dict | None:
    return None if r is None else dict(r._mapping)


def upsert_app(s: Session, org_id: UUID | str, *, slug: str, name: str,
               kind: str = "app", icon: str | None = None,
               project_slug: str | None = None,
               parent_id: UUID | str | None = None) -> dict:
    """Insert-or-update by `(org_id, slug)` among LIVE rows.

    Slug is the natural key here on purpose ("a slug is a label, never an
    identity" still means it is A label, and the backfill's whole job is to
    find the ONE row a given install/project pair should be). An UPDATE, not
    an insert-once-and-skip like `db/backfill.py`'s tickets: unlike a
    historical ticket, an app's name/icon/link legitimately changes between
    backfill runs (a rename, an install, a newly-created project), and the
    row should track the registry rather than freeze at its first read.
    """
    existing = get_app_by_slug(s, org_id, slug)
    if existing is not None:
        s.execute(text(
            "UPDATE app SET name = :name, kind = :kind, icon = :icon, "
            "project_slug = :project_slug, parent_id = :parent_id "
            "WHERE id = :id"),
            {"name": name, "kind": kind, "icon": icon,
             "project_slug": project_slug,
             "parent_id": str(parent_id) if parent_id else None,
             "id": existing["id"]})
        return get_app(s, org_id, existing["id"])
    app_id = uuid7()
    s.execute(text(
        "INSERT INTO app (id, org_id, slug, name, kind, icon, project_slug, "
        "parent_id) VALUES (:id, :org, :slug, :name, :kind, :icon, "
        ":project_slug, :parent_id)"),
        {"id": app_id, "org": str(org_id), "slug": slug, "name": name,
         "kind": kind, "icon": icon, "project_slug": project_slug,
         "parent_id": str(parent_id) if parent_id else None})
    return get_app(s, org_id, app_id)


def get_app(s: Session, org_id: UUID | str, app_id: UUID | str) -> dict | None:
    return _row(s.execute(text(
        "SELECT * FROM app WHERE id = :id AND org_id = :org "
        "AND deleted_at IS NULL"),
        {"id": str(app_id), "org": str(org_id)}).one_or_none())


def get_app_by_slug(s: Session, org_id: UUID | str, slug: str) -> dict | None:
    return _row(s.execute(text(
        "SELECT * FROM app WHERE org_id = :org AND slug = :slug "
        "AND deleted_at IS NULL"),
        {"org": str(org_id), "slug": slug}).one_or_none())


def list_apps(s: Session, org_id: UUID | str, *, kind: str | None = None,
             archived: bool | None = None) -> list[dict]:
    """Live apps for this org. `archived=None` (the default) returns both
    archived and unarchived; `True`/`False` filters on `archived_at`.
    Soft-deleted rows are never returned, regardless of `archived`."""
    where = ["org_id = :org", "deleted_at IS NULL"]
    params: dict[str, Any] = {"org": str(org_id)}
    if kind:
        where.append("kind = :kind")
        params["kind"] = kind
    if archived is not None:
        where.append("archived_at IS NOT NULL" if archived else "archived_at IS NULL")
    sql = f"SELECT * FROM app WHERE {' AND '.join(where)} ORDER BY name"
    return [dict(r._mapping) for r in s.execute(text(sql), params).all()]


def update_app(s: Session, org_id: UUID | str, app_id: UUID | str,
               **fields: Any) -> dict | None:
    allowed = ("name", "icon", "slug", "kind", "parent_id", "project_slug",
               "archived_at")
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return get_app(s, org_id, app_id)
    assignments = ", ".join(f"{k} = :{k}" for k in sets)
    params = dict(sets)
    params["id"] = str(app_id)
    params["org"] = str(org_id)
    s.execute(text(
        f"UPDATE app SET {assignments} WHERE id = :id AND org_id = :org "
        f"AND deleted_at IS NULL"), params)
    return get_app(s, org_id, app_id)


def delete_app(s: Session, org_id: UUID | str, app_id: UUID | str) -> bool:
    """Soft delete. `chat_thread.app_id` / `ticket.app_id` are left as-is by
    the FK's `SET NULL (app_id)` only on a HARD delete; a soft delete here
    does not cascade to them, so a deleted app's chats keep their app_id and
    would reappear correctly if the app is restored. Nothing restores it yet
    (no route for that -- deliberately out of scope for stage B)."""
    r = s.execute(text(
        "UPDATE app SET deleted_at = now() WHERE id = :id AND org_id = :org "
        "AND deleted_at IS NULL"),
        {"id": str(app_id), "org": str(org_id)})
    return r.rowcount > 0


def list_chats_for_app(s: Session, org_id: UUID | str, app_id: UUID | str, *,
                       archived: bool = False, limit: int = 200) -> list[dict]:
    rows = s.execute(text(
        "SELECT id, external_id, context_key, scope, title, seed_title, "
        "mode, model, agent_slug, created_at, updated_at, archived_at "
        "FROM chat_thread WHERE org_id = :org AND app_id = :app "
        "AND deleted_at IS NULL "
        "AND (archived_at IS NOT NULL) = :archived "
        "ORDER BY updated_at DESC LIMIT :limit"),
        {"org": str(org_id), "app": str(app_id), "archived": bool(archived),
         "limit": limit}).all()
    return [dict(r._mapping) for r in rows]


def count_chats_for_app(s: Session, org_id: UUID | str, app_id: UUID | str) -> int:
    return s.execute(text(
        "SELECT count(*) FROM chat_thread WHERE org_id = :org AND app_id = :app "
        "AND deleted_at IS NULL"),
        {"org": str(org_id), "app": str(app_id)}).scalar_one()


def set_thread_app_id(s: Session, org_id: UUID | str, thread_id: UUID | str,
                      app_id: UUID | str | None) -> None:
    s.execute(text(
        "UPDATE chat_thread SET app_id = :app WHERE id = :id AND org_id = :org"),
        {"app": str(app_id) if app_id else None, "id": str(thread_id),
         "org": str(org_id)})
