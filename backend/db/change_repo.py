"""Reads and writes for the `change` table
(docs/platform-redesign/changes-and-review-2026-08-22.md, step C1).

Same shape as `db/app_repo.py`: every function takes a `Session` so a caller
can compose several operations in one transaction, and rows come back as plain
dicts straight off raw SQL. The row shapes here are what `services/changes.py`
hands to `routes/changes.py`, and an ORM identity map buys nothing over that.

THIS MODULE KNOWS NOTHING ABOUT GIT. It stores what a change IS; deciding
whether a branch has anything to open a change for, and producing the diff, is
`services/changes.py`'s job, one layer up, through `services/worktrees.py`'s
published interface. Keeping that split is what stops the record layer growing
a second, divergent differ.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from db.ids import uuid7
from db.models import CHANGE_STATUSES

__all__ = [
    "create_change", "get_change", "open_change_for_branch", "list_changes",
    "update_change", "delete_change", "UPDATABLE",
]

# What a caller may PATCH. `head_sha` and `base_ref` are here because a change
# tracks a moving branch: a new commit on the branch is the same change at a
# new sha, not a new change. `project_slug`/`branch_slug` are NOT: they are the
# identity of the branch this record was opened for, and a change that could be
# repointed at another branch would silently relabel a reviewed diff.
UPDATABLE = (
    "app_id", "worktree_id", "base_ref", "head_sha", "title", "summary",
    "ticket_ids", "status", "gates", "remote", "merged_sha", "merged_at",
    "closed_reason",
)

# Columns whose value is JSON and therefore needs an explicit cast: psycopg
# will not adapt a Python list or dict to jsonb on its own.
_JSON_COLUMNS = ("gates", "remote")


def _row(r: Any) -> dict | None:
    return None if r is None else dict(r._mapping)


def _bind(column: str, value: Any) -> Any:
    if column in _JSON_COLUMNS:
        return None if value is None else json.dumps(value)
    if column in ("app_id", "worktree_id"):
        return None if value is None else str(value)
    return value


def _placeholder(column: str) -> str:
    return f"CAST(:{column} AS jsonb)" if column in _JSON_COLUMNS else f":{column}"


def create_change(s: Session, org_id: UUID | str, *, project_slug: str,
                  branch_slug: str, base_ref: str, head_sha: str, title: str,
                  summary: str = "", ticket_ids: list[str] | None = None,
                  status: str = "draft", app_id: UUID | str | None = None,
                  worktree_id: UUID | str | None = None,
                  gates: list[dict] | None = None,
                  remote: dict | None = None) -> dict:
    """Insert one change and return it.

    No upsert, deliberately, unlike `app_repo.upsert_app`. "One branch produces
    one change" is enforced by the partial unique index over the OPEN set, and
    a second call for a branch that already has an open change is a caller
    error worth raising on rather than a silent overwrite of a reviewed record.
    """
    if status not in CHANGE_STATUSES:
        raise ValueError(f"unknown change status: {status!r}")
    change_id = uuid7()
    s.execute(text(
        "INSERT INTO change (id, org_id, app_id, worktree_id, project_slug, "
        "branch_slug, base_ref, head_sha, title, summary, ticket_ids, status, "
        "gates, remote) VALUES (:id, :org, :app_id, :worktree_id, "
        ":project_slug, :branch_slug, :base_ref, :head_sha, :title, :summary, "
        ":ticket_ids, :status, CAST(:gates AS jsonb), CAST(:remote AS jsonb))"),
        {"id": change_id, "org": str(org_id),
         "app_id": _bind("app_id", app_id),
         "worktree_id": _bind("worktree_id", worktree_id),
         "project_slug": project_slug, "branch_slug": branch_slug,
         "base_ref": base_ref, "head_sha": head_sha, "title": title,
         "summary": summary, "ticket_ids": list(ticket_ids or []),
         "status": status,
         "gates": _bind("gates", list(gates or [])),
         "remote": _bind("remote", remote)})
    return get_change(s, org_id, change_id)


def get_change(s: Session, org_id: UUID | str,
               change_id: UUID | str) -> dict | None:
    return _row(s.execute(text(
        "SELECT * FROM change WHERE id = :id AND org_id = :org "
        "AND deleted_at IS NULL"),
        {"id": str(change_id), "org": str(org_id)}).one_or_none())


def open_change_for_branch(s: Session, org_id: UUID | str, project_slug: str,
                           branch_slug: str) -> dict | None:
    """The branch's live change, if it has one.

    "Open" means the same thing the unique index means: not merged, not closed,
    not soft-deleted. A branch whose change merged and which was then reused
    starts a new change rather than reopening the old one.
    """
    return _row(s.execute(text(
        "SELECT * FROM change WHERE org_id = :org AND project_slug = :p "
        "AND branch_slug = :b AND deleted_at IS NULL "
        "AND status NOT IN ('merged', 'closed')"),
        {"org": str(org_id), "p": project_slug, "b": branch_slug}
    ).one_or_none())


def list_changes(s: Session, org_id: UUID | str, *, status: str | None = None,
                 app_id: UUID | str | None = None,
                 project_slug: str | None = None,
                 ticket_id: str | None = None,
                 limit: int = 200) -> list[dict]:
    """Live changes for this org, newest activity first.

    `ticket_id` uses array containment (`@>`) so it rides the GIN index rather
    than unnesting every row, which is the query C2's status derivation runs.
    """
    where = ["org_id = :org", "deleted_at IS NULL"]
    params: dict[str, Any] = {"org": str(org_id), "limit": max(1, min(limit, 1000))}
    if status:
        where.append("status = :status")
        params["status"] = status
    if app_id:
        where.append("app_id = :app_id")
        params["app_id"] = str(app_id)
    if project_slug:
        where.append("project_slug = :project_slug")
        params["project_slug"] = project_slug
    if ticket_id:
        where.append("ticket_ids @> ARRAY[:ticket_id]::text[]")
        params["ticket_id"] = ticket_id
    rows = s.execute(text(
        f"SELECT * FROM change WHERE {' AND '.join(where)} "
        f"ORDER BY updated_at DESC LIMIT :limit"), params).all()
    return [dict(r._mapping) for r in rows]


def update_change(s: Session, org_id: UUID | str, change_id: UUID | str,
                  **fields: Any) -> dict | None:
    """Patch the named columns. Unknown or unwritable names raise.

    A no-op call still returns the row, so a caller that computed an empty
    patch does not have to special-case it.
    """
    unknown = set(fields) - set(UPDATABLE)
    if unknown:
        raise ValueError(f"not updatable on a change: {sorted(unknown)}")
    if "status" in fields and fields["status"] not in CHANGE_STATUSES:
        raise ValueError(f"unknown change status: {fields['status']!r}")
    if not fields:
        return get_change(s, org_id, change_id)
    assignments = ", ".join(f"{c} = {_placeholder(c)}" for c in fields)
    params = {c: _bind(c, v) for c, v in fields.items()}
    params.update({"id": str(change_id), "org": str(org_id)})
    s.execute(text(
        f"UPDATE change SET {assignments} WHERE id = :id AND org_id = :org "
        f"AND deleted_at IS NULL"), params)
    return get_change(s, org_id, change_id)


def delete_change(s: Session, org_id: UUID | str,
                  change_id: UUID | str) -> bool:
    """Soft delete, matching every other table in this schema: the row stays so
    the history that referenced it is not destroyed, and the partial unique
    index frees the branch slug for a new change."""
    res = s.execute(text(
        "UPDATE change SET deleted_at = now() WHERE id = :id AND org_id = :org "
        "AND deleted_at IS NULL"),
        {"id": str(change_id), "org": str(org_id)})
    return res.rowcount > 0
