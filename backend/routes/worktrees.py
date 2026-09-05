"""HTTP surface for project branch worktrees (per ADR-009 section 7).

Each project can host N parallel branches as git worktrees. Build agents pin
to a branch by slug; the operator inspects, merges, or discards each from the
Project Detail "Branches" card.

URL path stays /api/worktrees/ since that's the literal git concept; the
user-facing slug is `branch_slug`.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services import worktrees

router = APIRouter()


class _CreateIn(BaseModel):
    branch_slug: str = Field(min_length=1, max_length=48)


class _MergeIn(BaseModel):
    message: str = ""


class _PublishIn(BaseModel):
    title: str = ""
    body: str = ""


# ---- response models (ADR 0023 clause 6) -----------------------------------
# Read off services/worktrees.py and the services/github.py helpers it calls.
# FastAPI FILTERS a return value through the declared model and drops any key
# it does not name, so an under-declared field here is a live API field that
# vanishes.
#
# A PR record STAYS OPEN: it is `gh pr list --json ...`'s output verbatim, so
# its keys belong to the GitHub CLI. The fields this repo reads are named; the
# rest ride through rather than being guessed at or dropped.


class BranchRow(BaseModel):
    """One worktree of this project. Only branches under the `wt/<project>/`
    prefix are listed, so an unrelated worktree on the same repo is invisible
    here rather than mistaken for a project branch."""

    project_slug: str
    branch_slug: str
    branch: str
    dir: str
    # The human-readable label derived from the ticket title, or None for trees
    # created before the naming fix (migration b9f3e2d07a16). ADR 0023 clause 6:
    # if this field is absent the route silently drops it and the UI shows the
    # raw t-<hex> slug instead.
    display_name: str | None = None
    head: str | None = None
    # The trunk this project's branches are cut from, BY ITS OWN NAME. Declared
    # for the reason display_name above it had to be (ADR 0023 clause 6: an
    # undeclared field is dropped): a control that lists branches also offers
    # the trunk, and without this it has to invent a word for it. Two of them
    # did, and disagreed. Same for every row of one project; the wire shape here
    # is a bare list, so it rides on the row.
    default_ref: str | None = None
    # git refuses to remove or prune a locked worktree, and every tree the task
    # allocator makes is locked, so a caller deciding whether one can be reaped
    # has to know.
    locked: bool = False


class BranchCreated(BaseModel):
    """`worktree_id` is the durable record this branch got (step B2). It is
    OPTIONAL because record-keeping is additive: an installation with no
    relational store still allocates the tree, and answers without an id rather
    than refusing. `/branches/{worktree_id}` is where it is then addressable."""

    project_slug: str
    branch_slug: str
    branch: str
    dir: str
    created: str
    worktree_id: str | None = None


class DiffFile(BaseModel):
    """One changed file with its FULL patch text. `status` is git's name-status
    letter (M, A, D, R...)."""

    path: str
    status: str
    diff: str


class BranchDiff(BaseModel):
    """`summary` is git's own shortstat line, or the literal "no changes vs
    main" when the branch is empty against the base."""

    summary: str
    files: list[DiffFile]


class MergeResult(BaseModel):
    """A fast-forward is attempted first and a real merge commit only if that
    fails. On failure the in-progress merge is ABORTED before answering, so the
    working tree is clean and `reason` describes a merge that left nothing
    behind. The route turns `ok: false` into a 409, so this body is the success
    case in practice."""

    ok: bool
    commit: str | None = None
    reason: str | None = None


class PullRequest(BaseModel):
    """A PR as `gh` reports it. Left open: the field set is the GitHub CLI's."""

    model_config = ConfigDict(extra="allow")

    number: int | None = None
    title: str | None = None
    headRefName: str | None = None
    url: str | None = None
    state: str | None = None
    isDraft: bool | None = None


class PublishResult(BaseModel):
    """Publish rebases onto the base BEFORE pushing. A rebase conflict answers
    `ok: false` with `conflict: true` and does not push, so the caller can park
    the task instead of looping -- which is why this is a body rather than an
    exception."""

    ok: bool
    branch: str | None = None
    base: str | None = None
    conflict: bool | None = None
    detail: str | None = None
    remote_url: str | None = None
    pr: PullRequest | None = None


class CleanupResult(BaseModel):
    """Branch slugs, not branch refs. A branch still ahead of the base is KEPT:
    cleanup is best-effort per branch and never discards unmerged work."""

    removed: list[str]
    kept: list[str]


class AheadBehind(BaseModel):
    """Counts against `origin/<base>`. Both read 0 when the rev-list could not
    run, which is indistinguishable from being level -- an honest limitation of
    the underlying command, recorded here rather than papered over."""

    ahead: int
    behind: int


class WorktreeState(BaseModel):
    """`task_id` is the branch slug: a branch is named after the ticket that
    owns it, so the two are the same string by construction."""

    branch_slug: str
    dir: str
    task_id: str
    ahead_behind: AheadBehind
    pr: PullRequest | None = None


class RepoState(BaseModel):
    """The canonical-state view. An UNBOUND project (no GitHub remote) answers
    with just `{bound: false}` and nothing else, which is why every other field
    is optional."""

    bound: bool
    default_branch: str | None = None
    remote_url: str | None = None
    worktrees: list[WorktreeState] | None = None
    open_prs: list[PullRequest] | None = None


class BranchDiscarded(BaseModel):
    """What a reap did, and where the work went.

    `ok: false` is an ANSWER, not an outage: the tree had uncommitted work that
    could not be snapshotted, or it is a directory with no git metadata that
    nothing can speak for, and it was left exactly as it was. `detail` says
    which. Retry with `force=true` once you have read `detail` and decided the
    contents are expendable.
    """

    ok: bool
    removed: bool = False
    # The ref the uncommitted work was committed to before the directory went
    # away, or null when there was nothing to save. Recoverable with
    # `git show <ref>` in the project's own repository.
    snapshot_ref: str | None = None
    snapshot_sha: str | None = None
    # How many entries `git status` reported before the reap. The size of what
    # the snapshot is holding.
    dirty_entries: int = 0
    detail: str = ""


@router.get("/{project_slug}", response_model=list[BranchRow], response_model_exclude_unset=True)
async def list_for_project(project_slug: str):
    try:
        return worktrees.list_branches(project_slug)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{project_slug}", response_model=BranchCreated, response_model_exclude_unset=True)
async def create(project_slug: str, body: _CreateIn):
    try:
        return worktrees.create_branch(project_slug, body.branch_slug)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{project_slug}/{branch_slug}/diff", response_model=BranchDiff, response_model_exclude_unset=True)
async def diff(project_slug: str, branch_slug: str):
    try:
        return worktrees.diff_branch(project_slug, branch_slug)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{project_slug}/{branch_slug}/merge", response_model=MergeResult, response_model_exclude_unset=True)
async def merge(project_slug: str, branch_slug: str, body: _MergeIn):
    try:
        result = worktrees.merge_branch(project_slug, branch_slug, message=body.message)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason") or "merge failed")
    return result


@router.post("/{project_slug}/{branch_slug}/publish", response_model=PublishResult, response_model_exclude_unset=True)
async def publish(project_slug: str, branch_slug: str, body: _PublishIn):
    """Push the branch to origin and open a PR (bound projects only)."""
    try:
        return worktrees.publish_branch(
            project_slug, branch_slug, title=body.title, body=body.body,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{project_slug}/cleanup", response_model=CleanupResult, response_model_exclude_unset=True)
async def cleanup(project_slug: str):
    """Remove worktrees whose branch is already merged into the canonical base
    (bound projects only; unbound returns empty)."""
    try:
        return worktrees.cleanup_merged(project_slug)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{project_slug}/repo-state", response_model=RepoState, response_model_exclude_unset=True)
async def repo_state(project_slug: str):
    """Canonical-state view: default branch, remote, active worktrees + PRs,
    ahead/behind, open PRs. Unbound projects return {bound: false}."""
    try:
        return worktrees.repo_state(project_slug)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{project_slug}/{branch_slug}", response_model=BranchDiscarded)
async def discard(project_slug: str, branch_slug: str, force: bool = False,
                  reason: str = ""):
    """Reap a branch tree, snapshotting anything uncommitted first.

    THE VERB `/branches` OFFERS. That page exists to surface trees holding work
    nobody owns, and until this could promise not to destroy that work it had
    nothing safe to point at: an abandoned tree could only be cleared by hand.
    Uncommitted content goes to `refs/wip/<branch_slug>` in the project's repo
    before the directory is removed, and the response says where.

    `force` covers the one case with nothing to snapshot -- a directory with no
    git metadata, which is what a teardown that fell through to `rmtree` leaves
    behind. Without it that is a refusal rather than a silent deletion.
    """
    try:
        return worktrees.discard_branch(project_slug, branch_slug,
                                        reason=reason, force=force)
    except ValueError as e:
        raise HTTPException(400, str(e))
