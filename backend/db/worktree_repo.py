"""Reads and writes for the `worktree` and `worktree_event` tables
(docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md, step B2).

Same shape as `db/change_repo.py`: every function takes a `Session` so a caller
can compose several operations in one transaction, and rows come back as plain
dicts straight off raw SQL.

THIS MODULE KNOWS NOTHING ABOUT GIT, and nothing about projects. It stores what
a worktree IS. Creating the directory, adding the git worktree, picking the
start point and reaping the tree afterwards are `services/worktrees.py`'s job,
one layer up, through `services/git_service.py`. Keeping that split is what
stops the record layer growing a second, divergent notion of where a branch
lives.

THE TIMELINE IS APPEND-ONLY HERE TOO. There is no `update_event` and no
`delete_event`, deliberately: section 9 makes the timeline the record of who
took a tree from whom, and a history that can be rewritten is not one.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from db.ids import uuid7
from db.models import WORKTREE_EVENT_KINDS, WORKTREE_STATUSES

__all__ = [
    "create_worktree", "get_worktree", "live_worktree_for_branch",
    "list_worktrees", "update_worktree", "delete_worktree",
    "append_event", "list_events", "UPDATABLE",
]

# What a caller may patch. `project_slug`, `branch_slug`, `branch` and `path`
# are NOT here: they are the identity of the tree this record was created for,
# and a record that could be repointed at another directory is a record that can
# silently disagree with the disk -- the one thing this table exists to prevent.
# `base_ref`/`base_sha` are equally fixed: they are what the tree was cut FROM,
# which does not change while the tree exists.
UPDATABLE = (
    "app_id", "ticket_id", "allocated_by", "status", "merged_sha", "removed_at",
)

_UUID_COLUMNS = ("app_id",)


def _row(r: Any) -> dict | None:
    return None if r is None else dict(r._mapping)


def _bind(column: str, value: Any) -> Any:
    if column in _UUID_COLUMNS:
        return None if value is None else str(value)
    return value


def create_worktree(s: Session, org_id: UUID | str, *, project_slug: str,
                    scope: str, branch_slug: str, branch: str, path: str,
                    base_ref: str, base_sha: str | None = None,
                    ticket_id: str | None = None,
                    allocated_by: str | None = None,
                    app_id: UUID | str | None = None,
                    status: str = "allocated") -> dict:
    """Insert one worktree record and return it.

    No upsert. "One live record per branch" is enforced by the partial unique
    index over the unfinished set, and a second call for a branch that already
    has a live record means the allocator and the disk disagree, which is worth
    raising on rather than overwriting.
    """
    if status not in WORKTREE_STATUSES:
        raise ValueError(f"unknown worktree status: {status!r}")
    worktree_id = uuid7()
    s.execute(text(
        "INSERT INTO worktree (id, org_id, app_id, project_slug, scope, "
        "branch_slug, branch, path, base_ref, base_sha, ticket_id, "
        "allocated_by, status) VALUES (:id, :org, :app_id, :project_slug, "
        ":scope, :branch_slug, :branch, :path, :base_ref, :base_sha, "
        ":ticket_id, :allocated_by, :status)"),
        {"id": worktree_id, "org": str(org_id),
         "app_id": _bind("app_id", app_id),
         "project_slug": project_slug, "scope": scope,
         "branch_slug": branch_slug, "branch": branch, "path": path,
         "base_ref": base_ref, "base_sha": base_sha, "ticket_id": ticket_id,
         "allocated_by": allocated_by, "status": status})
    return get_worktree(s, org_id, worktree_id)


def get_worktree(s: Session, org_id: UUID | str,
                 worktree_id: UUID | str) -> dict | None:
    return _row(s.execute(text(
        "SELECT * FROM worktree WHERE id = :id AND org_id = :org "
        "AND deleted_at IS NULL"),
        {"id": str(worktree_id), "org": str(org_id)}).one_or_none())


def live_worktree_for_branch(s: Session, org_id: UUID | str, project_slug: str,
                             branch_slug: str) -> dict | None:
    """The branch's live record, if it has one.

    "Live" means the same thing the unique index means: not merged, not
    discarded, not soft-deleted. A slug reused after a reap starts a new record
    rather than resurrecting the old one, which is what keeps the history of the
    previous tree readable.
    """
    return _row(s.execute(text(
        "SELECT * FROM worktree WHERE org_id = :org AND project_slug = :p "
        "AND branch_slug = :b AND deleted_at IS NULL "
        "AND status NOT IN ('merged', 'discarded')"),
        {"org": str(org_id), "p": project_slug, "b": branch_slug}
    ).one_or_none())


def list_worktrees(s: Session, org_id: UUID | str, *, status: str | None = None,
                   project_slug: str | None = None,
                   ticket_id: str | None = None,
                   app_id: UUID | str | None = None,
                   live_only: bool = False,
                   limit: int = 200) -> list[dict]:
    """Worktree records for this org, newest activity first."""
    where = ["org_id = :org", "deleted_at IS NULL"]
    params: dict[str, Any] = {"org": str(org_id),
                              "limit": max(1, min(limit, 1000))}
    if status:
        where.append("status = :status")
        params["status"] = status
    if live_only:
        where.append("status NOT IN ('merged', 'discarded')")
    if project_slug:
        where.append("project_slug = :project_slug")
        params["project_slug"] = project_slug
    if ticket_id:
        where.append("ticket_id = :ticket_id")
        params["ticket_id"] = ticket_id
    if app_id:
        where.append("app_id = :app_id")
        params["app_id"] = str(app_id)
    rows = s.execute(text(
        f"SELECT * FROM worktree WHERE {' AND '.join(where)} "
        f"ORDER BY updated_at DESC LIMIT :limit"), params).all()
    return [dict(r._mapping) for r in rows]


def update_worktree(s: Session, org_id: UUID | str, worktree_id: UUID | str,
                    **fields: Any) -> dict | None:
    """Patch the named columns. Unknown or unwritable names raise.

    A no-op call still returns the row, so a caller that computed an empty patch
    does not have to special-case it.
    """
    unknown = set(fields) - set(UPDATABLE)
    if unknown:
        raise ValueError(f"not updatable on a worktree: {sorted(unknown)}")
    if "status" in fields and fields["status"] not in WORKTREE_STATUSES:
        raise ValueError(f"unknown worktree status: {fields['status']!r}")
    if not fields:
        return get_worktree(s, org_id, worktree_id)
    assignments = ", ".join(f"{c} = :{c}" for c in fields)
    params = {c: _bind(c, v) for c, v in fields.items()}
    params.update({"id": str(worktree_id), "org": str(org_id)})
    s.execute(text(
        f"UPDATE worktree SET {assignments} WHERE id = :id AND org_id = :org "
        f"AND deleted_at IS NULL"), params)
    return get_worktree(s, org_id, worktree_id)


def delete_worktree(s: Session, org_id: UUID | str,
                    worktree_id: UUID | str) -> bool:
    """Soft delete, matching every other table in this schema: the row stays so
    the timeline and the changes that referenced it are not destroyed, and the
    partial unique index frees the branch slug for a new record."""
    res = s.execute(text(
        "UPDATE worktree SET deleted_at = now() WHERE id = :id "
        "AND org_id = :org AND deleted_at IS NULL"),
        {"id": str(worktree_id), "org": str(org_id)})
    return res.rowcount > 0


# --- the timeline ------------------------------------------------------------

def append_event(s: Session, org_id: UUID | str, worktree_id: UUID | str, *,
                 kind: str, actor: str | None = None, sha: str | None = None,
                 detail: str | None = None,
                 paths: list[str] | None = None) -> dict:
    """Append one structured event to a worktree's timeline.

    An INSERT, which is the entire reason the timeline is a table: two
    participants appending at the same moment both keep their row, where a
    read-modify-write of a JSON array on the worktree would lose one of them.
    """
    if kind not in WORKTREE_EVENT_KINDS:
        raise ValueError(
            f"unknown worktree event kind {kind!r}; "
            f"one of {list(WORKTREE_EVENT_KINDS)}")
    event_id = uuid7()
    s.execute(text(
        "INSERT INTO worktree_event (id, org_id, worktree_id, kind, actor, "
        "sha, detail, paths) VALUES (:id, :org, :wt, :kind, :actor, :sha, "
        ":detail, :paths)"),
        {"id": event_id, "org": str(org_id), "wt": str(worktree_id),
         "kind": kind, "actor": actor, "sha": sha, "detail": detail,
         "paths": None if paths is None else [str(p) for p in paths]})
    return _row(s.execute(text(
        "SELECT * FROM worktree_event WHERE id = :id AND org_id = :org"),
        {"id": str(event_id), "org": str(org_id)}).one())


def list_events(s: Session, org_id: UUID | str, worktree_id: UUID | str, *,
                limit: int = 500) -> list[dict]:
    """The timeline, oldest first.

    OLDEST FIRST, unlike every list in `change_repo`: this is a narrative of
    what happened to one tree, and reading a handover backwards is reading it
    wrong. `id` breaks ties because `created_at` is `now()`, which is constant
    within a transaction, and uuid7 is time-ordered so it is the correct
    tiebreak rather than an arbitrary one.
    """
    rows = s.execute(text(
        "SELECT * FROM worktree_event WHERE org_id = :org AND worktree_id = :wt "
        "ORDER BY created_at ASC, id ASC LIMIT :limit"),
        {"org": str(org_id), "wt": str(worktree_id),
         "limit": max(1, min(limit, 2000))}).all()
    return [dict(r._mapping) for r in rows]
