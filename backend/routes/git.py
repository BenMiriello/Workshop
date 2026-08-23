"""The git resource: `/git/...`, against an explicit repository.

Section 13.1 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`
asks for git to be its own standalone resource rather than something reachable
only as a side effect of asking about a project. So every route here takes the
repository as an explicit argument and none of them knows what a project, an
app, a ticket or a lease is: resolving a slug to a directory is the caller's
job, one layer up (`/worktrees` does exactly that and then calls the same
service).

Reads are GETs with query parameters, writes are POST/DELETE with a body. A verb
whose failure is an ANSWER rather than an outage (a merge that conflicts, a
checkout that cannot happen, a push with no remote) answers 200 with `ok: false`
and git's own words in `detail`; only a malformed request or a path that is not
a repository is a 4xx. That split is what lets a caller drive a merge without
parsing error strings out of an exception.

Gated with the rest of the platform control surface in `main.py`
(`dependencies=_OPERATOR`).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services import git_service

router = APIRouter(prefix="/git", tags=["git"])


def _repo(repo: str) -> Path:
    """Resolve the repository argument, or 400 with why it is not usable."""
    if not (repo or "").strip():
        raise HTTPException(400, "repo is required")
    path = Path(repo).expanduser()
    if not path.exists():
        raise HTTPException(404, f"no such directory: {path}")
    if not path.is_dir():
        raise HTTPException(400, f"not a directory: {path}")
    if not git_service.is_repo(path):
        raise HTTPException(400, f"not a git repository: {path}")
    return path


def _guard(fn, *args, **kwargs):
    """Run a git_service call, turning a GitError into a 400 with git's words."""
    try:
        return fn(*args, **kwargs)
    except git_service.GitError as e:
        raise HTTPException(400, (e.stderr or e.stdout or str(e)).strip()[:400])


# ---- request bodies --------------------------------------------------------

class RepoBody(BaseModel):
    repo: str = Field(min_length=1)


class BranchCreateBody(RepoBody):
    name: str = Field(min_length=1)
    start_point: str | None = None


class WorktreeAddBody(RepoBody):
    path: str = Field(min_length=1)
    branch: str | None = None
    start_point: str | None = None


class WorktreeLockBody(RepoBody):
    path: str = Field(min_length=1)
    reason: str = ""


class CommitBody(RepoBody):
    message: str = Field(min_length=1)
    all: bool = False
    allow_empty: bool = False


class MergeBody(RepoBody):
    ref: str = Field(min_length=1)
    message: str = ""
    prefer_fast_forward: bool = True
    allow_merge_commit: bool = True


class SnapshotBody(RepoBody):
    ref: str | None = None
    message: str = ""


class CheckoutBody(RepoBody):
    ref: str = Field(min_length=1)
    create: bool = False
    force: bool = False


class CleanBody(RepoBody):
    directories: bool = True
    ignored: bool = False
    dry_run: bool = False


class PushBody(RepoBody):
    remote: str = "origin"
    ref: str = Field(min_length=1)
    set_upstream: bool = False


# ---- response models (ADR 0023 clause 6) -----------------------------------
# Every shape below was read off services/git_service.py, which is the only
# producer. FastAPI FILTERS a return value through the declared model and drops
# any key it does not name, so an under-declared field here is a live API field
# that vanishes.


class StatusEntry(BaseModel):
    """One porcelain line: git's two-character code verbatim (`??` untracked,
    ` M` unstaged modification, `M ` staged) and the path it applies to."""

    status: str
    path: str


class RepoStatus(BaseModel):
    """`branch` is null on a detached HEAD. `clean` is the whole point of the
    call: a caller deciding whether a workspace can be handed over reads it and
    nothing else."""

    repo: str
    branch: str | None = None
    clean: bool
    entries: list[StatusEntry]


class CommitRecord(BaseModel):
    sha: str
    subject: str
    author: str
    date: str


class CommitLog(BaseModel):
    """Newest first. An empty repository has no HEAD and answers with []."""

    repo: str
    commits: list[CommitRecord]


class DiffFile(BaseModel):
    """`status` is git's letter (A/M/D/R100/...); for a rename `path` is the
    new path."""

    status: str
    path: str


class DiffResult(BaseModel):
    """`summary` is the `--shortstat` line ("" when nothing changed). `patch` is
    populated only when the request asked for the unified text, which is why it
    is excluded when unset rather than sent as an empty string."""

    repo: str
    summary: str
    files: list[DiffFile]
    patch: str | None = None


class AheadBehind(BaseModel):
    """Zeros when either side is missing, so a caller never has to special-case
    an unborn or unfetched branch."""

    ahead: int
    behind: int


class RevParse(BaseModel):
    """`sha` is null when the rev does not resolve; that is an answer, not an
    error."""

    repo: str
    rev: str
    sha: str | None = None


class Ancestry(BaseModel):
    """True when `ancestor` is contained in `descendant` (already merged).
    False also covers "one of those refs does not exist"."""

    ancestor: str
    descendant: str
    is_ancestor: bool


class BranchList(BaseModel):
    repo: str
    branches: list[str]


class BranchRecord(BaseModel):
    repo: str
    branch: str


class Deleted(BaseModel):
    """Best-effort teardown. `ok: false` means git refused, which for a delete
    is usually "it was already gone"."""

    ok: bool


class WorktreeRecord(BaseModel):
    """One entry of `git worktree list --porcelain`, main worktree included.
    `branch` is the short name or null on a detached HEAD."""

    dir: str
    head: str | None = None
    branch: str | None = None
    locked: bool
    detached: bool
    prunable: bool


class WorktreeList(BaseModel):
    repo: str
    worktrees: list[WorktreeRecord]


class WorktreeAdded(BaseModel):
    repo: str
    dir: str
    branch: str | None = None


class CommitResult(BaseModel):
    """`ok: false` with a `detail` is the ordinary "nothing staged" answer, not
    an outage."""

    ok: bool
    commit: str | None = None
    detail: str = ""


class MergeResult(BaseModel):
    """A failed merge has already been ABORTED by the service, so the working
    tree is clean when this comes back false. `conflicts` is captured before the
    abort, which is the only moment those paths are knowable."""

    ok: bool
    fast_forward: bool
    conflict: bool
    conflicts: list[str]
    commit: str | None = None
    detail: str = ""


class SnapshotResult(BaseModel):
    """Section 9: a yield snapshots to a DANGLING commit, so `sha` is reachable
    from nothing unless `ref` was requested, and HEAD never moved. A clean tree
    is a success with no sha."""

    ok: bool
    sha: str | None = None
    ref: str | None = None
    clean: bool
    detail: str = ""


class CheckoutResult(BaseModel):
    ok: bool
    detail: str = ""


class CleanResult(BaseModel):
    """`removed` is what git reported; under `dry_run` it is what it WOULD
    remove and nothing was touched."""

    repo: str
    dry_run: bool
    removed: list[str]


class PushResult(BaseModel):
    ok: bool
    detail: str = ""


# ---- reads -----------------------------------------------------------------

@router.get("/status", response_model=RepoStatus)
def get_status(repo: str = Query(...)) -> dict:
    """Working-tree status: the branch, whether it is clean, and every entry."""
    path = _repo(repo)
    return {"repo": str(path), **git_service.status(path)}


@router.get("/log", response_model=CommitLog)
def get_log(repo: str = Query(...), ref: str | None = None,
            max_count: int = Query(50, ge=1, le=5000)) -> dict:
    """Commits newest-first. An empty repository answers with []."""
    path = _repo(repo)
    return {"repo": str(path),
            "commits": git_service.log(path, ref=ref, max_count=max_count)}


@router.get("/diff", response_model=DiffResult, response_model_exclude_unset=True)
def get_diff(repo: str = Query(...), a: str | None = None, b: str | None = None,
             three_dot: bool = False, unified: bool = False,
             path_filter: str | None = None) -> dict:
    """Summary + changed paths for `a..b` (or `a...b`), plus the unified patch
    when `unified` is set. `path_filter` limits the patch to one path."""
    repo_path = _repo(repo)
    paths = [path_filter] if path_filter else None
    out: dict = {
        "repo": str(repo_path),
        "summary": git_service.diff_stat(repo_path, a, b, three_dot=three_dot),
        "files": git_service.diff_name_status(repo_path, a, b, three_dot=three_dot),
    }
    if unified:
        out["patch"] = git_service.diff(repo_path, a, b, three_dot=three_dot,
                                        paths=paths)
    return out


@router.get("/ahead-behind", response_model=AheadBehind)
def get_ahead_behind(repo: str = Query(...), upstream: str = Query(...),
                     ref: str = "HEAD") -> dict:
    """How far `ref` is ahead of / behind `upstream`."""
    return git_service.ahead_behind(_repo(repo), upstream, ref)


@router.get("/rev-parse", response_model=RevParse)
def get_rev_parse(repo: str = Query(...), rev: str = "HEAD",
                  abbrev_ref: bool = False) -> dict:
    """Resolve a rev to a sha, or to its short symbolic name."""
    path = _repo(repo)
    return {"repo": str(path), "rev": rev,
            "sha": git_service.rev_parse(path, rev, abbrev_ref=abbrev_ref,
                                         verify=True)}


@router.get("/is-ancestor", response_model=Ancestry)
def get_is_ancestor(repo: str = Query(...), ancestor: str = Query(...),
                    descendant: str = Query(...)) -> dict:
    """Whether `ancestor` is already contained in `descendant`."""
    return {"ancestor": ancestor, "descendant": descendant,
            "is_ancestor": git_service.is_ancestor(_repo(repo), ancestor, descendant)}


# ---- branches --------------------------------------------------------------

@router.get("/branches", response_model=BranchList)
def list_branches(repo: str = Query(...), pattern: str | None = None) -> dict:
    """Local branch names, optionally filtered by a refname glob."""
    path = _repo(repo)
    return {"repo": str(path),
            "branches": git_service.branch_list(path, pattern=pattern)}


@router.post("/branches", response_model=BranchRecord)
def create_branch(body: BranchCreateBody) -> dict:
    """Create a branch, optionally at a start point. 400 when git refuses."""
    path = _repo(body.repo)
    _guard(git_service.branch_create, path, body.name, body.start_point)
    return {"repo": str(path), "branch": body.name}


@router.delete("/branches", response_model=Deleted)
def delete_branch(repo: str = Query(...), name: str = Query(...),
                  force: bool = True) -> dict:
    """Delete a branch. Best-effort: `ok: false` when git refused."""
    return {"ok": git_service.branch_delete(_repo(repo), name, force=force)}


# ---- worktrees -------------------------------------------------------------

@router.get("/worktrees", response_model=WorktreeList)
def list_worktrees(repo: str = Query(...)) -> dict:
    """Every worktree of the repository, the main one included."""
    path = _repo(repo)
    return {"repo": str(path), "worktrees": _guard(git_service.worktree_list, path)}


@router.post("/worktrees", response_model=WorktreeAdded)
def add_worktree(body: WorktreeAddBody) -> dict:
    """Add a worktree, creating a branch there when one is named."""
    path = _repo(body.repo)
    added = _guard(git_service.worktree_add, path, body.path, branch=body.branch,
                   start_point=body.start_point)
    return {"repo": str(path), **added}


@router.post("/worktrees/lock", response_model=Deleted)
def lock_worktree(body: WorktreeLockBody) -> dict:
    """Lock a worktree so a crashed holder's stale index cannot wedge siblings."""
    return {"ok": git_service.worktree_lock(_repo(body.repo), body.path,
                                            reason=body.reason)}


@router.delete("/worktrees", response_model=Deleted)
def remove_worktree(repo: str = Query(...), path: str = Query(...),
                    force: bool = True) -> dict:
    """Remove a worktree. A LOCKED worktree refuses even this, by design."""
    return {"ok": git_service.worktree_remove(_repo(repo), path, force=force)}


@router.post("/worktrees/prune", response_model=Deleted)
def prune_worktrees(body: RepoBody) -> dict:
    """Drop administrative metadata for worktrees whose directory is gone."""
    return {"ok": git_service.worktree_prune(_repo(body.repo))}


# ---- writes ----------------------------------------------------------------

@router.post("/commit", response_model=CommitResult)
def commit(body: CommitBody) -> dict:
    """Commit the index (plus tracked modifications when `all`)."""
    return git_service.commit(_repo(body.repo), body.message, all=body.all,
                              allow_empty=body.allow_empty)


@router.post("/merge", response_model=MergeResult)
def merge(body: MergeBody) -> dict:
    """Merge a ref into the checked-out branch, fast-forward preferred. A merge
    that cannot complete is aborted before this answers."""
    return git_service.merge(
        _repo(body.repo), body.ref, message=body.message,
        prefer_fast_forward=body.prefer_fast_forward,
        allow_merge_commit=body.allow_merge_commit,
    )


@router.post("/snapshot", response_model=SnapshotResult)
def snapshot(body: SnapshotBody) -> dict:
    """Snapshot the working tree to a dangling commit without moving HEAD."""
    return git_service.snapshot(_repo(body.repo), ref=body.ref, message=body.message)


@router.post("/checkout", response_model=CheckoutResult)
def checkout(body: CheckoutBody) -> dict:
    """Check out a ref, optionally creating it."""
    return git_service.checkout(_repo(body.repo), body.ref, create=body.create,
                                force=body.force)


@router.post("/clean", response_model=CleanResult)
def clean(body: CleanBody) -> dict:
    """Remove untracked files (or report what would go, under `dry_run`)."""
    path = _repo(body.repo)
    removed = git_service.clean(path, directories=body.directories,
                                ignored=body.ignored, dry_run=body.dry_run)
    return {"repo": str(path), "dry_run": body.dry_run, "removed": removed}


@router.post("/push", response_model=PushResult)
def push(body: PushBody) -> dict:
    """Push a ref to a remote. Git's transport only: anything that needs a pull
    request goes through the GitHub service instead."""
    return git_service.push(_repo(body.repo), body.remote, body.ref,
                            set_upstream=body.set_upstream)
