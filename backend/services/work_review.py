"""Where a ticket and its change meet: open one, and merge it on acceptance.

Steps C2 and C5 of `docs/platform-redesign/changes-and-review-2026-08-22.md`.

WHY THIS IS ITS OWN MODULE. `services/changes.py` owns the change record and
knows nothing about tickets. `services/tasks.py` owns the ticket and knows
nothing about changes. Neither may learn about the other: the layering rule in
`workspace-ownership-and-leases-2026-08-22.md` sec 13.2 says composition happens
in a thin orchestration layer or in the caller, never by one service reaching
into another. This is that thin layer, and it is the ONLY module that imports
both.

WHAT CHANGED ABOUT "DONE". Before this, an unattended worker that passed its
reviewer gate MERGED ITS BRANCH INTO THE TRUNK and then set the ticket
`in_review`. So "supervised" meant the code had already landed and the operator
was asked to bless it afterwards; declining did not unland anything. The review
gate was real about the STATUS and cosmetic about the CODE.

Now, in worktree mode under supervision, the success leg opens a change and
leaves the branch unmerged. The work is complete, isolated, diffable and
reviewable, and the trunk is untouched until a person accepts it. Accepting is
what merges. That is the ordinary model (Gerrit, Phabricator, the PR) and it is
what makes the isolation bought by worktrees actually pay for itself.

WHAT IS DELIBERATELY UNCHANGED, so this is additive rather than a rewrite:

  * An UNSUPERVISED (autonomous) sweep still merges on success. Autonomous is
    the mode whose whole point is landing work without a person, and gating it
    on a review nobody will perform would just stop the loop.
  * A sweep NOT in worktree mode has nothing to merge: the worker committed
    straight to the tree it was given. Accepting such a ticket is a plain status
    transition exactly as before. `merge_for_task` returns `merged: False` with
    a reason rather than inventing a branch.
  * The relational store may be absent (a file-backed scope). A change cannot be
    opened there; the caller keeps its previous behaviour and says so. This must
    never become "silently drop the work".

THE HONEST LIMIT ON "DERIVED". The design doc's C2 says a ticket's status is
derived from its change and nobody flips it by hand. Half of that is true here:
accepting a ticket merges its change, and the merge is what completes it. The
other half is not yet: the sweep still writes `in_review` itself, through the
claim-token-fenced transition that is the only safe way for a worker to move a
ticket it holds. Moving that write in here would take the fence with it, and the
fence is what stops a displaced worker clobbering an operator's edit. Doing it
properly means the lease refactor (B3), so it is left alone and named rather
than half-done.
"""
from __future__ import annotations

import logging

from services import changes, worktrees

log = logging.getLogger(__name__)

__all__ = ["MergeRefused", "open_for_task", "merge_for_task", "change_for_task"]


class MergeRefused(Exception):
    """The change exists but could not be merged. The ticket must NOT complete.

    Carries the reason verbatim so the caller can show it. A conflict is the
    expected case and is not an error in the system: it means the branch needs a
    person, which is `park` in the sweep and a refused acceptance in the API.
    """


def open_for_task(project_slug: str, branch_slug: str, *, task_id: str,
                  title: str = "", summary: str = "") -> dict | None:
    """Open the change a finished branch produces, or None if it cannot be.

    Returns None (never raises) when there is no relational store, when the
    branch has nothing ahead of its base, or when the change cannot be recorded
    for any other reason. The caller is the unattended sweep and a failure here
    must not lose the work: the branch and its commits are on disk either way,
    and the ticket transition the caller performs afterwards is unaffected.
    """
    try:
        change = changes.open_change(
            project_slug, branch_slug,
            title=title, summary=summary,
            ticket_ids=[task_id] if task_id else [],
            status="ready",
        )
    except changes.NothingToReview:
        log.info("work_review: %s/%s has nothing ahead of base; no change opened",
                 project_slug, branch_slug)
        return None
    except Exception as e:  # noqa: BLE001 - see docstring: never lose the work
        log.warning("work_review: could not open a change for %s/%s: %s",
                    project_slug, branch_slug, e)
        return None
    return change


def change_for_task(task_id: str) -> dict | None:
    """The open, unmerged change carrying this ticket, or None.

    None also covers "there is no relational store", because a caller asking
    this question wants to know whether there is something to merge, and there
    is not.
    """
    if not task_id:
        return None
    try:
        found = changes.list_changes(ticket_id=task_id)
    except Exception as e:  # noqa: BLE001
        log.warning("work_review: cannot list changes for %s: %s", task_id, e)
        return None
    for change in found:
        if change.get("status") not in ("merged", "closed"):
            return change
    return None


def merge_for_task(task_id: str, *, message: str = "") -> dict:
    """Merge the change carrying this ticket. Called when the work is accepted.

    Returns ``{"merged": bool, "reason": str, "commit": str, "change_id": str}``.
    ``merged: False`` with a reason is the normal answer for a ticket that never
    produced a change (work committed straight to the tree, or a file-backed
    scope); the caller proceeds with its ordinary acceptance.

    Raises `MergeRefused` when a change EXISTS and the merge did not happen.
    That is the case the caller must not paper over: the ticket has an artifact,
    the artifact did not land, and calling it done would be a lie.
    """
    change = change_for_task(task_id)
    if change is None:
        return {"merged": False, "reason": "no change: nothing to merge",
                "commit": "", "change_id": ""}

    project_slug = change.get("project_slug") or ""
    branch_slug = change.get("branch_slug") or ""
    change_id = str(change.get("id") or "")
    if not project_slug or not branch_slug:
        raise MergeRefused(
            f"change {change_id} does not name a branch, so it cannot be merged")

    result = worktrees.merge_branch(project_slug, branch_slug, message=message)
    if not result.get("ok"):
        reason = str(result.get("reason") or "merge failed")
        raise MergeRefused(reason)

    commit = str(result.get("commit") or "")
    try:
        changes.mark_merged(change_id, commit)
    except Exception as e:  # noqa: BLE001
        # The code IS merged. Losing the bookkeeping must not un-merge it or
        # refuse the acceptance; it must be loud instead.
        log.error("work_review: merged %s/%s as %s but could not mark change %s "
                  "merged: %s", project_slug, branch_slug, commit, change_id, e)

    return {"merged": True, "reason": "", "commit": commit, "change_id": change_id}
