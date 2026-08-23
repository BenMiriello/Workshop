"""The Change: the reviewable artifact a branch produces.

Step C1 of `docs/platform-redesign/changes-and-review-2026-08-22.md`.

WHY THIS EXISTS. A ticket says what should be true; a change says here is the
code that makes it true. Until this module, `in_review` meant a worker had SAID
it was finished, and the operator was asked to accept an assertion with no diff,
no gate results, and no record of what was touched versus what the ticket asked
for. A change is the artifact behind the claim: the branch, the sha it is at,
the tickets it carries, its diff, and a slot for the machine checks.

LAYERING (workspace-ownership-and-leases-2026-08-22.md sec 13.2). This module
sits ABOVE branches, which sit above git:

    changes  ->  worktrees (branch records, naming and merge policy)
             ->  git_service (repo-path-first git)  ->  git

It calls `services/worktrees.py` through that module's published surface only
(`branch_state`, `diff_branch`) and it never touches its private helpers, never
shells out to git, and never imports `git_service` to work around it. Nothing
below imports this module: `worktrees.py` and `git_service.py` do not know what
a change is, and they must not learn.

IT DOES NOT OWN A SECOND DIFFER. `worktrees.diff_branch` already returns a
shortstat summary plus per-file unified diffs, and that IS the review payload
(design doc sec 7). `diff_for` calls it and shapes nothing.

WHAT IS DELIBERATELY NOT HERE, because it is a later step and building it early
would collide with the agent doing it:

  * ticket status derivation (a ticket following its change) -- step C2.
  * gate PRODUCTION and the translation of a reviewer run into gate entries --
    `services/change_gates.py` (step C3). It calls `update_change` like any
    other caller; this module does not know what a reviewer is, and the
    validation of the `gates` shape stays here because its writers are scripts
    rather than HTTP callers.
  * merging, auto-merge, `merge_policy`, and the low-risk classifier -- steps
    C5 and C7. There is no merge entry point in this module and no route for
    one, because a verb that exists and does nothing is worse than one that
    does not exist: a caller cannot tell it apart from a real one.

POSTGRES ONLY, and it fails closed. A change is work data, and ADR 0019 puts
work data in Postgres under RLS. There is no file store to fall back to and one
must not be invented: an unpartitioned file is exactly the tenancy hole the
ticket cutover closed. No database means `ChangesUnavailable`, never an empty
list, so "no changes" and "the store is down" can never read the same.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from db import app_repo, change_repo
from db.engine import tenant_session
from db.models import CHANGE_STATUSES, GATE_STATUSES
from services import tenancy, worktrees

__all__ = [
    "ChangesUnavailable", "ChangeNotFound", "BranchUnknown", "NothingToReview",
    "CHANGE_STATUSES", "GATE_STATUSES",
    "open_change", "get_change", "list_changes", "update_change", "mark_merged",
    "delete_change", "diff_for", "refresh_change", "change_for_branch",
]


class ChangesUnavailable(RuntimeError):
    """The relational store is not configured or not reachable.

    Reported rather than answered as an empty list, for the reason
    `services/apps.py` gives about the app roster: an empty `/changes` because
    the database is down must not read the same as an empty `/changes` because
    nothing is open for review.
    """


class ChangeNotFound(KeyError):
    pass


class BranchUnknown(ValueError):
    """The project or branch does not resolve. Carries git's or the registry's
    own words, because "no such branch" and "no such project" are different
    problems for whoever is holding it."""


class NothingToReview(ValueError):
    """The branch has no commits ahead of its base, so there is nothing to
    review. This is the design's opening condition (sec 3, step C1: a change is
    created when a branch FIRST has commits ahead of base) and it is a refusal,
    not an error: an empty change would be a record asserting work that does
    not exist, which is the defect the whole table exists to remove."""


def _require_db() -> tuple[UUID, UUID]:
    if not tenancy.configured() or not tenancy.available():
        raise ChangesUnavailable("no relational store configured/reachable")
    return tenancy.org_and_user()


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat(timespec="seconds")
    return str(v)


def _shape(row: dict) -> dict:
    """The wire shape. Ids as strings, timestamps as ISO, JSON columns as they
    were stored -- `routes/changes.py` types `gates` and `remote` and validates
    them on the way out."""
    return {
        "id": str(row["id"]),
        "app_id": None if row["app_id"] is None else str(row["app_id"]),
        "worktree_id": (None if row["worktree_id"] is None
                        else str(row["worktree_id"])),
        "project_slug": row["project_slug"],
        "branch_slug": row["branch_slug"],
        "base_ref": row["base_ref"],
        "base_sha": row.get("base_sha"),
        "head_sha": row["head_sha"],
        "title": row["title"],
        "summary": row["summary"] or "",
        "ticket_ids": list(row["ticket_ids"] or []),
        "status": row["status"],
        "gates": list(row["gates"] or []),
        "remote": row["remote"],
        "merged_sha": row["merged_sha"],
        "merged_at": _iso(row["merged_at"]),
        "closed_reason": row["closed_reason"],
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _default_title(branch_slug: str) -> str:
    """A branch slug read back as a sentence, for a change whose author gave no
    title. Not empty: `title` is what a reviewer sees in a list, and a blank one
    makes the list unusable."""
    return branch_slug.replace("-", " ").strip().capitalize() or branch_slug


def _validated_gates(gates: list[dict]) -> list[dict]:
    """Gate entries, checked against the shape the design fixes:
    `{name, status, detail, at}`. Enforced here rather than only at the route,
    because C3's writers are services and scripts, not HTTP callers, and a gate
    list that has drifted into free-form dicts is a review surface that cannot
    render."""
    out: list[dict] = []
    for entry in gates:
        if not isinstance(entry, dict):
            raise ValueError("a gate must be an object with name and status")
        name = str(entry.get("name") or "").strip()
        status = str(entry.get("status") or "").strip()
        if not name:
            raise ValueError("a gate must name itself")
        if status not in GATE_STATUSES:
            raise ValueError(
                f"unknown gate status {status!r}; one of {list(GATE_STATUSES)}")
        out.append({
            "name": name,
            "status": status,
            "detail": (None if entry.get("detail") is None
                       else str(entry["detail"])),
            "at": (None if entry.get("at") is None else str(entry["at"])),
        })
    return out


def _validated_remote(remote: dict | None) -> dict | None:
    """`{kind, url, number}` or nothing. A mirrored pull request is optional by
    design (local-first); a half-filled one is not, because a review surface
    that shows a remote link with no url is worse than one that shows none."""
    if remote is None:
        return None
    if not isinstance(remote, dict):
        raise ValueError("remote must be an object or null")
    kind = str(remote.get("kind") or "").strip()
    if not kind:
        raise ValueError("a remote must name its kind")
    number = remote.get("number")
    if number is not None:
        number = int(number)
    return {
        "kind": kind,
        "url": None if remote.get("url") is None else str(remote["url"]),
        "number": number,
    }


def _branch_state(project_slug: str, branch_slug: str) -> dict:
    try:
        return worktrees.branch_state(project_slug, branch_slug)
    except ValueError as e:
        raise BranchUnknown(str(e)) from e


def open_change(project_slug: str, branch_slug: str, *, title: str = "",
                summary: str = "", ticket_ids: list[str] | None = None,
                status: str = "draft",
                worktree_id: UUID | str | None = None) -> dict:
    """Open the change a branch has earned, or return the one it already has.

    THE OPENING CONDITION IS COMMITS AHEAD OF BASE, and nothing else. A branch
    that exists but has committed nothing raises `NothingToReview`: the record
    is meant to be evidence, and a change with an empty diff is the same
    unbacked assertion `in_review` used to be.

    IDEMPOTENT ON PURPOSE. The sweep and the chat both reach this on every
    commit, so a branch that already has an open change gets that change back
    with its `head_sha` and `base_ref` brought up to date, rather than a
    duplicate or a raise. The AUTHORED fields (title, summary, ticket_ids) are
    left exactly as they are: they may have been edited by a reviewer since,
    and a re-open silently reverting them would lose that.

    `app_id` is resolved from the project's `app` row when one is linked, and
    left NULL when it is not -- most project trees are not apps yet, and a
    change on one is still a change.
    """
    org_id, user_id = _require_db()
    state = _branch_state(project_slug, branch_slug)
    if not state["exists"]:
        raise BranchUnknown(
            f"no such branch: {state['branch']} in project {project_slug}")
    if state["ahead"] <= 0:
        raise NothingToReview(
            f"{state['branch']} has no commits ahead of {state['base_ref']}")
    if status not in CHANGE_STATUSES:
        raise ValueError(f"unknown change status: {status!r}")

    with tenant_session(org_id, user_id) as s:
        existing = change_repo.open_change_for_branch(
            s, org_id, state["project_slug"], state["branch_slug"])
        if existing is not None:
            row = change_repo.update_change(
                s, org_id, existing["id"],
                head_sha=state["head_sha"], base_ref=state["base_ref"])
            return _shape(row)
        app = app_repo.get_app_by_project_slug(s, org_id, state["project_slug"])
        row = change_repo.create_change(
            s, org_id,
            project_slug=state["project_slug"],
            branch_slug=state["branch_slug"],
            base_ref=state["base_ref"],
            head_sha=state["head_sha"],
            title=(title or "").strip() or _default_title(state["branch_slug"]),
            summary=summary or "",
            ticket_ids=[str(t) for t in (ticket_ids or [])],
            status=status,
            app_id=None if app is None else app["id"],
            worktree_id=worktree_id)
        return _shape(row)


def change_for_branch(project_slug: str, branch_slug: str) -> dict | None:
    """The branch's open change, or None. Does not touch git: this answers
    "has one been opened", not "does one deserve to be"."""
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        row = change_repo.open_change_for_branch(
            s, org_id, project_slug, branch_slug)
    return None if row is None else _shape(row)


def get_change(change_id: UUID | str) -> dict:
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        row = change_repo.get_change(s, org_id, change_id)
    if row is None:
        raise ChangeNotFound(str(change_id))
    return _shape(row)


def list_changes(*, status: str | None = None,
                 app_id: UUID | str | None = None,
                 project_slug: str | None = None,
                 ticket_id: str | None = None,
                 limit: int = 200) -> list[dict]:
    org_id, user_id = _require_db()
    if status and status not in CHANGE_STATUSES:
        raise ValueError(f"unknown change status: {status!r}")
    with tenant_session(org_id, user_id) as s:
        rows = change_repo.list_changes(
            s, org_id, status=status, app_id=app_id, project_slug=project_slug,
            ticket_id=ticket_id, limit=limit)
    return [_shape(r) for r in rows]


def update_change(change_id: UUID | str, **fields: Any) -> dict:
    """Patch the authored and recorded fields of a change.

    `gates` and `remote` are validated into their declared shapes here rather
    than trusted, because they are JSONB and nothing at the database layer can
    check them.

    THE MERGE OUTCOME IS NOT PATCHABLE. `status='merged'`, `merged_sha` and
    `merged_at` are refused here: a change becomes merged by being merged, and
    the row has to record the commit that did it (`ck_change_merged_is_recorded`
    makes that a constraint, not a convention). Whatever performs the merge
    writes all three together in one transaction through `db/change_repo.py`.
    That writer is step C5 and does not exist yet.
    """
    org_id, user_id = _require_db()
    merge_outcome = {"merged_sha", "merged_at"} & set(fields)
    if fields.get("status") == "merged" or merge_outcome:
        raise ValueError(
            "the merge outcome (status='merged', merged_sha, merged_at) is "
            "written by whatever performs the merge, together, and is not "
            "patchable")
    if "gates" in fields:
        fields["gates"] = _validated_gates(fields["gates"] or [])
    if "remote" in fields:
        fields["remote"] = _validated_remote(fields["remote"])
    if "ticket_ids" in fields:
        fields["ticket_ids"] = [str(t) for t in (fields["ticket_ids"] or [])]
    with tenant_session(org_id, user_id) as s:
        if change_repo.get_change(s, org_id, change_id) is None:
            raise ChangeNotFound(str(change_id))
        row = change_repo.update_change(s, org_id, change_id, **fields)
    return _shape(row)


def mark_merged(change_id: UUID | str, commit: str, *,
                base_sha: str | None = None) -> dict:
    """Record that this change merged, as the commit that did it. Step C5.

    This is the writer `update_change` refuses to be. `status`, `merged_sha` and
    `merged_at` move together in one transaction, because
    `ck_change_merged_is_recorded` makes "merged without a recorded sha" a
    constraint violation rather than a merely inconsistent row, and because a
    change that says merged while pointing at nothing is exactly the
    self-reported completion this whole design exists to remove.

    It does NOT perform the merge and must not learn how: merging is a branch
    operation and belongs to `services/worktrees.py`. The caller merges first
    and records second, so a failed merge can never leave a change claiming it
    landed. `services/work_review.py` is that caller.
    """
    if not commit or not str(commit).strip():
        raise ValueError("a merged change must record the commit that merged it")
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        if change_repo.get_change(s, org_id, change_id) is None:
            raise ChangeNotFound(str(change_id))
        fields: dict[str, Any] = {
            "status": "merged",
            "merged_sha": str(commit).strip(),
            "merged_at": datetime.now(timezone.utc),
        }
        if base_sha:
            fields["base_sha"] = str(base_sha).strip()
        row = change_repo.update_change(s, org_id, change_id, **fields)
    return _shape(row)


def refresh_change(change_id: UUID | str) -> dict:
    """Bring the record back in step with the branch it points at.

    A change tracks a MOVING branch: another commit is the same change at a new
    sha, not a new change. This is the one operation that re-reads git for an
    existing record, and it is a POST rather than part of GET because a read
    that silently mutates the row it returns is how a "why did this update"
    question becomes unanswerable.
    """
    current = get_change(change_id)
    state = _branch_state(current["project_slug"], current["branch_slug"])
    if not state["exists"]:
        raise BranchUnknown(
            f"no such branch: {state['branch']} in project "
            f"{current['project_slug']}")
    return update_change(change_id, head_sha=state["head_sha"],
                         base_ref=state["base_ref"])


def diff_for(change_id: UUID | str) -> dict:
    """The review payload: `{summary, files: [{path, status, diff}]}`.

    Straight from `worktrees.diff_branch`, which already produces exactly this
    (design doc sec 7). Computed on demand rather than stored on the row: the
    branch moves, and a stored diff is a stale diff the moment it does -- which
    would reintroduce, at the artifact layer, the same "the record says
    something the repository does not" problem this table removes at the status
    layer.

    A MERGED CHANGE IS DIFFED base_sha..merged_sha INSTEAD. Merging reaps the
    worktree and deletes the branch, so the branch-addressed diff of accepted
    work answers "no files", which on a review surface reads as "this changed
    nothing" for exactly the work that landed. The record of what was accepted
    has to survive acceptance.

    It is base_SHA and not base_REF, and the difference is the whole point: by
    the time anyone asks, the base ref CONTAINS the change, so diffing against
    the name is guaranteed to be empty. `base_sha` is the commit the base stood
    at before the merge, pinned at merge time for exactly this.
    """
    change = get_change(change_id)
    merged_sha = change.get("merged_sha")
    base_sha = change.get("base_sha")
    try:
        if merged_sha and base_sha:
            return worktrees.diff_refs(change["project_slug"], base_sha, merged_sha)
        return worktrees.diff_branch(change["project_slug"],
                                     change["branch_slug"])
    except ValueError as e:
        raise BranchUnknown(str(e)) from e


def delete_change(change_id: UUID | str) -> bool:
    """Soft delete. Frees the branch to open a new change, keeps the row."""
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        return change_repo.delete_change(s, org_id, change_id)
