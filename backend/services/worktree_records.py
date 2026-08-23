"""The worktree RECORD: a branch's durable identity and its timeline.

Step B2 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`.

WHY THIS EXISTS. `services/worktrees.py` made directories and git worktrees and
nothing durable remembered them. `list_branches` re-derived the entire set by
shelling out to `git worktree list`, which can answer "what is on disk right
now" and nothing else: not who allocated a tree, not when, not for which ticket,
not what became of it. A merge reaped the directory and the last trace went with
it. This module is the half that survives the disk.

VOCABULARY (§3 of the design, binding). The disk concept is **worktree** and the
user-facing concept is **branch**. The word "workspace" is not available for
either: `/workspace` is the bwrap mountpoint every agent sees
(`scoped_exec.py`), and `workspace_inputs` already means an app's heavy
dependency directories (`worktrees.py`). So the table is `worktree`, this module
is worktree records, and the HTTP resource is `/branches`.

LAYERING (§13.2). This module is the record store for the branches layer. It
sits BESIDE the git-facing half, not above it:

    changes ──► worktrees (naming/merge policy, git orchestration) ──► git_service
                    │
                    └────► worktree_records (this module) ──► db/worktree_repo

It knows nothing about git, nothing about projects on disk, and nothing about
changes. `services/worktrees.py` calls it; it never calls back.

IT IS ADDITIVE, AND THAT IS THE ONE RULE THAT MAKES IT SAFE TO ADD. A worktree
must still allocate on an installation with no database configured -- the sweep,
the CLI and every agent path predate this table and must not start depending on
it. So every writer here raises `WorktreesUnavailable` when there is no store,
and `services/worktrees.py` treats that as "no record today", never as a failed
allocation. That is the opposite of `services/changes.py`, which fails closed,
and the difference is deliberate: a change with no store is a review that cannot
happen, while a worktree with no store is a directory that works fine and is
merely unremembered.

WHAT IS DELIBERATELY NOT HERE. No lease: no holder, no generation, no expiry.
That is step B3 and §5 gives it its own table, because a lease has a different
lifetime from the resource it is taken on and folding it in would make releasing
one a write to the resource record. No presence either (B4).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from db import app_repo, worktree_repo
from db.engine import tenant_session
from db.models import WORKTREE_EVENT_KINDS, WORKTREE_STATUSES
from services import tenancy

__all__ = [
    "WorktreesUnavailable", "WorktreeNotFound",
    "WORKTREE_STATUSES", "WORKTREE_EVENT_KINDS",
    "record_allocation", "get_record", "record_for_branch", "list_records",
    "mark_provisioned", "mark_merged", "mark_discarded", "delete_record",
    "add_event", "timeline",
]


class WorktreesUnavailable(RuntimeError):
    """The relational store is not configured or not reachable.

    Callers inside `services/worktrees.py` swallow this: allocation predates the
    record and must keep working without it. Callers at the HTTP layer report it
    as a 503, for the reason `services/changes.py` gives about the change list --
    an empty `/branches` because the database is down must not read the same as
    an empty `/branches` because nothing is allocated.
    """


class WorktreeNotFound(KeyError):
    pass


def _require_db() -> tuple[UUID, UUID]:
    if not tenancy.configured() or not tenancy.available():
        raise WorktreesUnavailable("no relational store configured/reachable")
    return tenancy.org_and_user()


def available() -> bool:
    """Whether a record can be written at all. Cheap, and never raises."""
    try:
        _require_db()
        return True
    except Exception:
        return False


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat(timespec="seconds")
    return str(v)


def _shape(row: dict) -> dict:
    """The wire shape. Ids as strings, timestamps as ISO."""
    return {
        "id": str(row["id"]),
        "app_id": None if row["app_id"] is None else str(row["app_id"]),
        "project_slug": row["project_slug"],
        "scope": row["scope"],
        "branch_slug": row["branch_slug"],
        "branch": row["branch"],
        "path": row["path"],
        "base_ref": row["base_ref"],
        "base_sha": row["base_sha"],
        "ticket_id": row["ticket_id"],
        "allocated_by": row["allocated_by"],
        "status": row["status"],
        "merged_sha": row["merged_sha"],
        "removed_at": _iso(row["removed_at"]),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _shape_event(row: dict) -> dict:
    """One timeline entry, with its structured fields intact.

    `paths` comes back as a list rather than None so a client never has to
    distinguish "no paths" from "the column was null"; every other field keeps
    its null, because "no actor" and "the empty actor" are different facts.
    """
    return {
        "id": str(row["id"]),
        "worktree_id": str(row["worktree_id"]),
        "kind": row["kind"],
        "actor": row["actor"],
        "sha": row["sha"],
        "detail": row["detail"],
        "paths": list(row["paths"] or []),
        "at": _iso(row.get("created_at")),
    }


def _validated_kind(kind: str) -> str:
    k = str(kind or "").strip()
    if k not in WORKTREE_EVENT_KINDS:
        raise ValueError(
            f"unknown worktree event kind {k!r}; "
            f"one of {list(WORKTREE_EVENT_KINDS)}")
    return k


# --- allocation --------------------------------------------------------------

def record_allocation(*, project_slug: str, scope: str, branch_slug: str,
                      branch: str, path: str, base_ref: str,
                      base_sha: str | None = None, ticket_id: str | None = None,
                      allocated_by: str | None = None) -> dict:
    """Write the record for a tree that has just been created on disk.

    Called from `services/worktrees.py` immediately after `git worktree add`
    succeeds, so the record cannot describe a tree that failed to appear. The
    matching `allocated` event is written in the SAME transaction: a record with
    an empty timeline would say the tree exists and refuse to say where it came
    from, which is half of what the row is for.

    THE TICKET IS OPTIONAL BY DESIGN (§10.4): "writing with no ticket is common
    and not second-class: it gets a branch a ticket can adopt later."

    `app_id` is resolved from the project's `app` row when one is linked, and
    left NULL when it is not -- most project trees are not apps yet, and a
    branch on one is still a branch.
    """
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        app = app_repo.get_app_by_project_slug(s, org_id, project_slug)
        row = worktree_repo.create_worktree(
            s, org_id,
            project_slug=project_slug, scope=scope, branch_slug=branch_slug,
            branch=branch, path=path, base_ref=base_ref, base_sha=base_sha,
            ticket_id=ticket_id, allocated_by=allocated_by,
            app_id=None if app is None else app["id"])
        worktree_repo.append_event(
            s, org_id, row["id"], kind="allocated", actor=allocated_by,
            sha=base_sha, detail=base_ref)
    return _shape(row)


# --- reads -------------------------------------------------------------------

def get_record(worktree_id: UUID | str) -> dict:
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        row = worktree_repo.get_worktree(s, org_id, worktree_id)
    if row is None:
        raise WorktreeNotFound(str(worktree_id))
    return _shape(row)


def record_for_branch(project_slug: str, branch_slug: str) -> dict | None:
    """The branch's live record, or None. Does not touch git: this answers "is
    this tree remembered", not "is this tree on disk"."""
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        row = worktree_repo.live_worktree_for_branch(
            s, org_id, project_slug, branch_slug)
    return None if row is None else _shape(row)


def list_records(*, status: str | None = None, project_slug: str | None = None,
                 ticket_id: str | None = None,
                 app_id: UUID | str | None = None, live_only: bool = False,
                 limit: int = 200) -> list[dict]:
    org_id, user_id = _require_db()
    if status and status not in WORKTREE_STATUSES:
        raise ValueError(f"unknown worktree status: {status!r}")
    with tenant_session(org_id, user_id) as s:
        rows = worktree_repo.list_worktrees(
            s, org_id, status=status, project_slug=project_slug,
            ticket_id=ticket_id, app_id=app_id, live_only=live_only,
            limit=limit)
    return [_shape(r) for r in rows]


# --- lifecycle ---------------------------------------------------------------
#
# Each of these moves the STATUS and appends the matching EVENT in one
# transaction. Splitting them would let a record reach a terminal state with no
# timeline entry saying how it got there, which is the "status with no artifact"
# defect the change table was built to remove one layer up.

def _transition(worktree_id: UUID | str, fields: dict[str, Any], *, kind: str,
                actor: str | None = None, sha: str | None = None,
                detail: str | None = None,
                paths: list[str] | None = None) -> dict:
    org_id, user_id = _require_db()
    kind = _validated_kind(kind)
    with tenant_session(org_id, user_id) as s:
        if worktree_repo.get_worktree(s, org_id, worktree_id) is None:
            raise WorktreeNotFound(str(worktree_id))
        row = worktree_repo.update_worktree(s, org_id, worktree_id, **fields)
        worktree_repo.append_event(s, org_id, worktree_id, kind=kind,
                                   actor=actor, sha=sha, detail=detail,
                                   paths=paths)
    return _shape(row)


def mark_provisioned(worktree_id: UUID | str, *,
                     provisioned: list[str] | None = None,
                     actor: str | None = None) -> dict:
    """The tree's declared heavy inputs are hardlink-cloned into it.

    A separate state from `allocated` because the difference is real to whoever
    picks the tree up: an allocated tree is a source checkout with no
    dependencies installed, a provisioned one can be built. `provisioned` names
    the directories that were cloned; the ones that were SKIPPED are not
    recorded, because a dependency directory absent from the canonical tree is
    not an event that happened to this worktree.
    """
    return _transition(worktree_id, {"status": "provisioned"},
                       kind="provisioned", actor=actor,
                       paths=list(provisioned or []))


def mark_merged(worktree_id: UUID | str, *, commit: str | None = None,
                actor: str | None = None) -> dict:
    """The branch merged and the tree was reaped. Terminal.

    `removed_at` moves with the status because `ck_worktree_terminal_is_removed`
    makes "merged while still on disk" a constraint violation rather than a
    merely inconsistent row.
    """
    sha = (str(commit).strip() or None) if commit else None
    return _transition(
        worktree_id,
        {"status": "merged", "merged_sha": sha,
         "removed_at": datetime.now(timezone.utc)},
        kind="merged", actor=actor, sha=sha)


def mark_discarded(worktree_id: UUID | str, *, reason: str = "",
                   actor: str | None = None) -> dict:
    """The tree was force-removed without merging. Terminal."""
    return _transition(
        worktree_id,
        {"status": "discarded",
         "removed_at": datetime.now(timezone.utc)},
        kind="discarded", actor=actor, detail=(reason.strip() or None))


def delete_record(worktree_id: UUID | str) -> bool:
    """Soft delete. Frees the branch slug for a new record, keeps the row and
    its timeline."""
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        return worktree_repo.delete_worktree(s, org_id, worktree_id)


# --- the timeline ------------------------------------------------------------

def add_event(worktree_id: UUID | str, *, kind: str, actor: str | None = None,
              sha: str | None = None, detail: str | None = None,
              paths: list[str] | None = None) -> dict:
    """Append one structured event (§9).

    The lifecycle transitions above write their own events; this is the entry
    point for the ones that are not a status change -- `acquired`, `steered`,
    `preempted`, `snapshot <sha>`. Those verbs are B6 and B7 and their
    behaviour is not built here, but the timeline they write to is, because
    section 9's requirement is about where the record GOES: "structured events,
    not prose in a transcript".
    """
    org_id, user_id = _require_db()
    kind = _validated_kind(kind)
    with tenant_session(org_id, user_id) as s:
        if worktree_repo.get_worktree(s, org_id, worktree_id) is None:
            raise WorktreeNotFound(str(worktree_id))
        row = worktree_repo.append_event(
            s, org_id, worktree_id, kind=kind, actor=actor, sha=sha,
            detail=detail, paths=None if paths is None else list(paths))
    return _shape_event(row)


def timeline(worktree_id: UUID | str, *, limit: int = 500) -> list[dict]:
    """Every event on this worktree, oldest first."""
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        if worktree_repo.get_worktree(s, org_id, worktree_id) is None:
            raise WorktreeNotFound(str(worktree_id))
        rows = worktree_repo.list_events(s, org_id, worktree_id, limit=limit)
    return [_shape_event(r) for r in rows]
