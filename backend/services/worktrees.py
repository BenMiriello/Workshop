"""Git-worktree management for project branches (per ADR-009 section 7).

A "branch" here is a worktree-pinned development branch off a project's main.
A Build agent can pin to one of these branches so multiple agents can work on
the same project without stepping on each other's edits. Merging happens
through the UI: fast-forward when possible, falling back to a regular merge
commit with the neutral committer identity. Worktree + branch are reaped on
success.

The disk concept stays "worktree" (git's term); the URL path stays
/api/worktrees/. The user-facing concept is the branch.

LAYER. This module is branch RECORDS plus policy: which project a slug names,
what a branch is called and where its worktree lives, when a merge or a publish
is allowed and what happens after one. It speaks no git itself. Every git
invocation goes through `services/git_service.py`, which takes a repository path
and knows nothing about projects (section 13.1/13.2 of workspace-ownership-and-
leases-2026-08-22.md). The GitHub remote surface stays in `services/github.py`.

Public surface:
  create_branch(project_slug, branch_slug) -> dict
  allocate_task_worktree(project_slug, task_id) -> dict
  provision_worktree(scope, owner) -> dict
  list_branches(project_slug) -> list[dict]
  merge_branch(project_slug, branch_slug, *, message="") -> dict
  discard_branch(project_slug, branch_slug) -> dict
  diff_branch(project_slug, branch_slug) -> dict
  branch_state(project_slug, branch_slug) -> dict
  publish_branch(project_slug, branch_slug, *, title="", body="") -> dict
  cleanup_merged(project_slug) -> dict
  repo_state(project_slug) -> dict
  branch_dir(project_slug, branch_slug) -> Path
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from services import git_service
from services.app_runner import _read_manifest_file as _read_app_manifest, AppError as _AppError
from wsplatform import paths

BRANCHES_ROOT = paths.env_path("WORKSHOP_BRANCHES_ROOT", "branches", base="root")
PROJECTS_ROOT = paths.env_path("WORKSHOP_PROJECTS_ROOT_NEW", "projects", base="root")
# The canonical registry lives at .runtime/projects.json (registry.py is the one
# writer; routes/projects.py reads the same path). A bare projects.json next to
# the projects dir never existed, so every Branches lookup raised "registry
# missing". Honor the same WORKSHOP_REGISTRY_PATH override registry.py uses.
PROJECTS_REGISTRY = Path(os.environ.get(
    "WORKSHOP_REGISTRY_PATH",
    str(PROJECTS_ROOT.parent / ".runtime" / "projects.json"),
))

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,47}$")

# Guard against exhausting the volume mid-clone. Override with
# WORKSHOP_MIN_WORKTREE_FREE_BYTES if running on a smaller disk.
MIN_FREE_BYTES: int = int(
    os.environ.get("WORKSHOP_MIN_WORKTREE_FREE_BYTES", str(2 * 1024 * 1024 * 1024))
)

# Fallback (lockfile, dep-dir) pairs used when the app manifest declares none.
# Only pairs whose lockfile is actually present in the project tree are offered.
_PLATFORM_DEFAULT_INPUTS: list[tuple[str, str]] = [
    ("package-lock.json", "node_modules"),
    ("backend/requirements.txt", "backend/.venv"),
    ("requirements.txt", ".venv"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_slug(value: str, label: str) -> str:
    v = (value or "").strip().lower()
    if not SLUG_RE.match(v):
        raise ValueError(f"{label} must be kebab-case alphanumeric, start with a letter, max 48 chars")
    return v


def _kebab_branch_slug(value: str) -> str:
    """Normalize an arbitrary task id into a valid branch slug.

    Lowercase; runs of non-alphanumeric collapse to a single dash; trimmed and
    capped at 48 chars. A value that would not start with a letter (a uuid4 hex
    id can begin with a digit) is prefixed `t-` so it satisfies SLUG_RE.
    """
    v = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    if not v or not v[0].isalpha():
        v = f"t-{v}" if v else "t"
    return v[:48].rstrip("-")


def _project_record(project_slug: str) -> dict:
    """Return the registry record for a project (or raise)."""
    if not PROJECTS_REGISTRY.exists():
        raise ValueError(f"projects registry missing: {PROJECTS_REGISTRY}")
    try:
        reg = json.loads(PROJECTS_REGISTRY.read_text())
    except Exception as e:
        raise ValueError(f"projects registry unreadable: {e}")
    p = (reg.get("projects") or {}).get(project_slug)
    if not p or not p.get("dir"):
        raise ValueError(f"project not found: {project_slug}")
    return p


def _project_dir(project_slug: str) -> Path:
    """Locate the project's main directory via projects.json."""
    p = _project_record(project_slug)
    pd = Path(p["dir"])
    if not pd.exists():
        raise ValueError(f"project directory missing: {pd}")
    return pd


def _project_repo(project_slug: str) -> dict | None:
    """Return the project's `repo` binding (Phase G1) or None if unbound."""
    try:
        return _project_record(project_slug).get("repo")
    except ValueError:
        return None


def _canonical_branch(project_slug: str, project_dir: Path) -> str:
    """The canonical branch name: the tracked `repo.default_branch` when the
    project is bound to a GitHub remote, else the best-effort local guess."""
    repo = _project_repo(project_slug)
    if repo and repo.get("default_branch"):
        return repo["default_branch"]
    return git_service.main_branch(project_dir)


def _branch_name(project_slug: str, branch_slug: str) -> str:
    return f"wt/{project_slug}/{branch_slug}"


def _branch_path(project_slug: str, branch_slug: str) -> Path:
    return BRANCHES_ROOT / project_slug / branch_slug


def create_branch(project_slug: str, branch_slug: str) -> dict:
    """Create a new worktree-pinned branch off the project's main.

    Returns a record describing the new branch: project, branch_slug, full
    git ref name, dir, and the creation timestamp.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    project_dir = _project_dir(project_slug)
    branch = _branch_name(project_slug, branch_slug)
    target = _branch_path(project_slug, branch_slug)
    if target.exists():
        raise ValueError(f"branch dir already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        git_service.worktree_add(project_dir, target, branch=branch)
    except git_service.GitError as e:
        stderr = (e.stderr or "").strip()[:200]
        raise ValueError(f"git worktree add failed (exit {e.returncode}): {stderr}")
    return {
        "project_slug": project_slug,
        "branch_slug": branch_slug,
        "branch": branch,
        "dir": str(target),
        "created": _now(),
    }


def allocate_task_worktree(project_slug: str, task_id: str) -> dict:
    """Allocate an isolated, locked worktree for one task (Phase G3 concurrency).

    Creates branch wt/<slug>/<task-id> in its own worktree, off
    origin/<default_branch> when the project is bound (so an agent starts from
    the canonical remote base), else off the local canonical branch (today's
    behavior). The worktree is locked so a crashed agent's stale index cannot
    wedge sibling worktrees. The task-id is the branch slug (kebab-normalized
    and validated). Returns {branch, branch_slug, dir}.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(_kebab_branch_slug(task_id), "task_id")
    project_dir = _project_dir(project_slug)
    branch = _branch_name(project_slug, branch_slug)
    target = _branch_path(project_slug, branch_slug)
    if target.exists():
        raise ValueError(f"worktree dir already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    repo = _project_repo(project_slug)
    if repo:
        base = repo.get("default_branch") or git_service.main_branch(project_dir)
        start_point = f"origin/{base}"
    else:
        start_point = _canonical_branch(project_slug, project_dir)

    try:
        git_service.worktree_add(project_dir, target, branch=branch,
                                 start_point=start_point)
    except git_service.GitError as e:
        stderr = (e.stderr or "").strip()[:200]
        raise ValueError(f"git worktree add failed (exit {e.returncode}): {stderr}")

    # Lock the worktree so a crashed agent's stale index cannot block others.
    git_service.worktree_lock(project_dir, target)
    return {"branch": branch, "branch_slug": branch_slug, "dir": str(target)}


def list_branches(project_slug: str) -> list[dict]:
    """List active branches for a project by parsing `git worktree list`.

    Only returns worktrees whose branch starts with wt/<project_slug>/.
    The main worktree is excluded.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    project_dir = _project_dir(project_slug)
    try:
        entries = git_service.worktree_list(project_dir)
    except git_service.GitError:
        return []
    prefix = f"wt/{project_slug}/"
    items: list[dict] = []
    for entry in entries:
        branch = entry.get("branch") or ""
        if not branch.startswith(prefix):
            continue
        items.append({
            "project_slug": project_slug,
            "branch_slug": branch[len(prefix):],
            "branch": branch,
            "dir": entry.get("dir", ""),
            "head": entry.get("head"),
        })
    return items


def diff_branch(project_slug: str, branch_slug: str) -> dict:
    """Diff the branch against the project's main branch.

    Returns a summary line (shortstat-style) plus per-file unified diffs.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    project_dir = _project_dir(project_slug)
    branch = _branch_name(project_slug, branch_slug)
    main = _canonical_branch(project_slug, project_dir)
    summary = git_service.diff_stat(project_dir, main, branch, three_dot=True) \
        or "no changes vs main"
    files: list[dict] = []
    for entry in git_service.diff_name_status(project_dir, main, branch, three_dot=True):
        path = entry["path"]
        files.append({
            "path": path,
            "status": entry["status"],
            "diff": git_service.diff(project_dir, main, branch, three_dot=True,
                                     paths=[path]),
        })
    return {"summary": summary, "files": files}


def diff_refs(project_slug: str, base: str, head: str) -> dict:
    """Diff two refs in the project, in the same shape as `diff_branch`.

    PUBLISHED SO A MERGED CHANGE KEEPS ITS DIFF. Merging reaps the worktree and
    deletes the branch, so asking for a merged change's diff BY BRANCH answers
    "no files", which reads as "this changed nothing" for work that in fact
    landed. The commits are still in the trunk, so the review record is
    recoverable as base..merged_sha; it just cannot be addressed by a branch
    that no longer exists. Two-dot, not three: after the merge the base contains
    the change, so a merge-base diff would correctly report nothing.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    project_dir = _project_dir(project_slug)
    summary = git_service.diff_stat(project_dir, base, head) or "no changes"
    files: list[dict] = []
    for entry in git_service.diff_name_status(project_dir, base, head):
        path = entry["path"]
        files.append({
            "path": path,
            "status": entry["status"],
            "diff": git_service.diff(project_dir, base, head, paths=[path]),
        })
    return {"summary": summary, "files": files}


def branch_state(project_slug: str, branch_slug: str) -> dict:
    """Where a branch stands against the base it will merge into.

    `{project_slug, branch_slug, branch, base_ref, head_sha, ahead, behind,
    exists}`. `exists` is False when the ref does not resolve at all, and then
    `head_sha` is None and the counts are zero.

    PUBLISHED FOR THE LAYER ABOVE. `services/changes.py` has to ask "is this
    branch ahead of its base, and at what sha" before it can open a change, and
    the two things that answer it here -- which project a slug names, and what
    this project's canonical base ref is -- are branch-record policy that lives
    in this module (`_project_dir`, `_canonical_branch`). Exposing the answer
    is what stops the layer above reaching into those internals or growing its
    own second definition of "the base branch", which is the drift
    workspace-ownership-and-leases-2026-08-22.md sec 13.2 exists to prevent.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    project_dir = _project_dir(project_slug)
    branch = _branch_name(project_slug, branch_slug)
    base = _canonical_branch(project_slug, project_dir)
    head_sha = git_service.rev_parse(project_dir, branch, verify=True)
    counts = ({"ahead": 0, "behind": 0} if head_sha is None
              else git_service.ahead_behind(project_dir, base, branch))
    return {
        "project_slug": project_slug,
        "branch_slug": branch_slug,
        "branch": branch,
        "base_ref": base,
        # The base as a SHA, resolved now. A ref name moves; a merged change
        # needs the commit the base stood at BEFORE it absorbed the change, or
        # its own diff becomes unrecoverable (see changes.diff_for).
        "base_sha": git_service.rev_parse(project_dir, base, verify=True),
        "head_sha": head_sha,
        "ahead": counts["ahead"],
        "behind": counts["behind"],
        "exists": head_sha is not None,
    }


def merge_branch(project_slug: str, branch_slug: str, *, message: str = "") -> dict:
    """Merge the branch into the project's main worktree.

    Tries fast-forward first. If that fails (divergence), falls back to a
    regular merge commit under the neutral committer identity. On success the
    worktree is removed and the branch is deleted.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    project_dir = _project_dir(project_slug)
    branch = _branch_name(project_slug, branch_slug)
    main = _canonical_branch(project_slug, project_dir)

    # Bound projects merge through the remote: the branch was published (pushed
    # + PR opened), so squash-merge the PR and delete the remote branch, then
    # fast-forward the local main. This runs AFTER the reviewer/operator gate
    # (the same gate that authorizes any merge). Unbound projects keep the
    # local-only merge below.
    repo = _project_repo(project_slug)
    if repo:
        from services import github
        try:
            github.merge_pr(str(project_dir), head=branch, base=main)
        except ValueError as e:
            return {"ok": False, "reason": str(e)[:400]}
        head = git_service.rev_parse(project_dir, "HEAD") or ""
        _reap_worktree(project_dir, project_slug, branch_slug, branch)
        return {"ok": True, "commit": head}

    # Make sure we're operating from main in the project's main worktree.
    co = git_service.checkout(project_dir, main)
    if not co["ok"]:
        return {"ok": False, "reason": f"checkout {main} failed: {co['detail'][:200]}"}

    # Fast-forward when the history allows it, a merge commit under the neutral
    # identity when it does not. git_service aborts a failed merge for us, so
    # the working tree is clean either way.
    result = git_service.merge(
        project_dir, branch,
        message=message.strip() or f"Merge branch {branch_slug} into {main}",
    )
    if not result["ok"]:
        return {"ok": False, "reason": result["detail"][:400]}

    _reap_worktree(project_dir, project_slug, branch_slug, branch)
    return {"ok": True, "commit": result["commit"] or ""}


def _reap_worktree(project_dir: Path, project_slug: str, branch_slug: str, branch: str) -> None:
    """Remove the branch worktree and delete its local ref after a merge."""
    target = _branch_path(project_slug, branch_slug)
    git_service.worktree_remove(project_dir, target, force=True)
    if target.exists():
        # If worktree remove balked (e.g. already pruned), try to clean up the
        # leftover dir so a re-create with the same slug works.
        shutil.rmtree(target, ignore_errors=True)
    git_service.branch_delete(project_dir, branch, force=True)


def discard_branch(project_slug: str, branch_slug: str) -> dict:
    """Force-remove a branch worktree and delete its git ref."""
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    project_dir = _project_dir(project_slug)
    branch = _branch_name(project_slug, branch_slug)
    target = _branch_path(project_slug, branch_slug)
    git_service.worktree_remove(project_dir, target, force=True)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    git_service.branch_delete(project_dir, branch, force=True)
    return {"ok": True}


def publish_branch(project_slug: str, branch_slug: str, *,
                   title: str = "", body: str = "") -> dict:
    """Push the branch to origin and open a PR against the canonical branch.

    OUTWARD-FACING: pushes to GitHub and opens a PR. Only valid for a project
    bound to a GitHub remote. Runs `git push -u origin wt/<slug>/<branch>` then
    `gh pr create` via github.open_pr. Returns {branch, remote_url, pr}.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    project_dir = _project_dir(project_slug)
    repo = _project_repo(project_slug)
    if not repo:
        raise ValueError("project is not connected to GitHub; connect it first")
    branch = _branch_name(project_slug, branch_slug)
    remote_url = repo["remote_url"]
    base = repo.get("default_branch") or git_service.main_branch(project_dir)

    from services import github

    # Rebase the branch onto the canonical base before pushing so conflicts
    # surface here as ordinary git conflicts rather than at merge time. A
    # conflicting rebase leaves the worktree clean (github.rebase_onto aborts)
    # and we return without pushing so the caller can park the task, not loop.
    worktree_dir = _branch_path(project_slug, branch_slug)
    rebase = github.rebase_onto(str(worktree_dir), base)
    if not rebase.get("ok"):
        return {
            "ok": False,
            "conflict": bool(rebase.get("conflict")),
            "branch": branch,
            "base": base,
            "detail": rebase.get("detail", ""),
        }

    push = git_service.push(project_dir, "origin", branch, set_upstream=True)
    if not push["ok"]:
        raise ValueError(f"git push failed: {push['detail'][:300]}")

    pr_title = title.strip() or f"{branch_slug} ({project_slug})"
    pr_body = body.strip() or f"Publishes {branch} into {base}."
    pr = github.open_pr(
        str(project_dir), remote=remote_url, base=base, head=branch,
        title=pr_title, body=pr_body,
    )
    return {"ok": True, "branch": branch, "remote_url": remote_url, "base": base, "pr": pr}


def cleanup_merged(project_slug: str) -> dict:
    """Remove worktrees whose branch is already merged into the canonical base.

    For each wt/<slug>/ branch, if github.is_merged reports it an ancestor of
    origin/<default_branch>, the worktree is force-removed, its local ref
    deleted, and stale worktree metadata pruned. Unbound projects have no origin
    to compare against, so they are skipped. Returns {removed, kept} (branch
    slugs). Best-effort per branch: a branch still ahead of the base is kept.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    project_dir = _project_dir(project_slug)
    repo = _project_repo(project_slug)
    if not repo:
        return {"removed": [], "kept": []}
    base = repo.get("default_branch") or git_service.main_branch(project_dir)

    from services import github
    removed: list[str] = []
    kept: list[str] = []
    for wt in list_branches(project_slug):
        branch = wt["branch"]
        branch_slug = wt["branch_slug"]
        if github.is_merged(str(project_dir), branch, base):
            _reap_worktree(project_dir, project_slug, branch_slug, branch)
            removed.append(branch_slug)
        else:
            kept.append(branch_slug)
    git_service.worktree_prune(project_dir)
    return {"removed": removed, "kept": kept}


def repo_state(project_slug: str) -> dict:
    """Canonical-state view for the Repo panel and agent context (Phase G3).

    For a bound project: default branch, remote URL, each active worktree with
    its assigned task (= branch_slug), ahead/behind vs origin/<default_branch>,
    and the matched open PR (joined by headRefName), plus all open PRs. Every
    piece is best-effort and degrades to null/empty; an unbound project returns
    {bound: False} without raising.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    project_dir = _project_dir(project_slug)
    repo = _project_repo(project_slug)
    if not repo:
        return {"bound": False}
    base = repo.get("default_branch") or git_service.main_branch(project_dir)
    remote_url = repo.get("remote_url")

    from services import github
    open_prs = github.list_open_prs(remote_url) if remote_url else []
    pr_by_head = {
        pr.get("headRefName"): pr for pr in open_prs if pr.get("headRefName")
    }

    worktrees_out: list[dict] = []
    for wt in list_branches(project_slug):
        branch = wt["branch"]
        branch_slug = wt["branch_slug"]
        wdir = wt.get("dir") or str(_branch_path(project_slug, branch_slug))
        worktrees_out.append({
            "branch_slug": branch_slug,
            "dir": wdir,
            "task_id": branch_slug,
            "ahead_behind": github.ahead_behind(wdir, base),
            "pr": pr_by_head.get(branch),
        })

    return {
        "bound": True,
        "default_branch": base,
        "remote_url": remote_url,
        "worktrees": worktrees_out,
        "open_prs": open_prs,
    }


def branch_dir(project_slug: str, branch_slug: str) -> Path:
    """Public helper: return the on-disk path for a (project, branch) pair.

    Used by services/build_agents.py to resolve cwd when a build agent is
    pinned to a branch.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    return _branch_path(project_slug, branch_slug)


# ---------------------------------------------------------------------------
# Provisioning — §13.2 of branches-worktrees-and-environments-2026-08-20.md
# ---------------------------------------------------------------------------

def provision_worktree(scope: str, owner: str) -> dict:
    """Allocate and provision an isolated worktree for a unit of work.

    Given a scope (``project:<slug>`` or bare slug) and an owner id, creates
    a git worktree via ``allocate_task_worktree`` then hardlink-clones each
    dependency directory from the canonical project tree into the new worktree.
    The clone is keyed by the hash of the lockfile that produced it; a
    ``.dep-contract.json`` sibling records the read-only contract so any
    reinstall path knows to ``rm -rf`` before writing, never writing into shared
    inodes.

    Raises ``ValueError`` with a clear message if free disk on the project
    volume is below ``MIN_FREE_BYTES`` before any work is done.

    Returns the ``allocate_task_worktree`` dict extended with::

        provisioned: list[str]  # dep dirs successfully cloned (relative paths)
        skipped: list[str]      # dep dirs absent from canonical tree (not an error)
    """
    project_slug = _scope_to_project_slug(scope)
    project_dir = _project_dir(project_slug)

    free = _free_bytes(project_dir)
    if free < MIN_FREE_BYTES:
        raise ValueError(
            f"preflight failed: {free / (1024 ** 3):.1f} GiB free on "
            f"{project_dir} — need ≥{MIN_FREE_BYTES // (1024 ** 3)} GiB"
        )

    result = allocate_task_worktree(project_slug, owner)
    worktree_dir = Path(result["dir"])

    provisioned: list[str] = []
    skipped: list[str] = []
    for lockfile_rel, dep_rel in _workspace_inputs(project_dir):
        src = project_dir / dep_rel
        if not src.exists() or not src.is_dir():
            skipped.append(dep_rel)
            continue
        lhash = _lockfile_hash(project_dir / lockfile_rel) if lockfile_rel else None
        dst = worktree_dir / dep_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        _hardlink_clone(src, dst)
        _write_dep_contract(dst, lockfile_hash=lhash, cloned_from=str(src))
        provisioned.append(dep_rel)

    result["provisioned"] = provisioned
    result["skipped"] = skipped
    return result


def _scope_to_project_slug(scope: str) -> str:
    """``project:<slug>`` or bare slug → slug."""
    if ":" in scope:
        kind, _, slug = scope.partition(":")
        if kind != "project":
            raise ValueError(
                f"provision_worktree only supports project scopes, got {scope!r}"
            )
        return slug
    return scope


def _workspace_inputs(project_dir: Path) -> list[tuple[str | None, str]]:
    """Return (lockfile_rel_or_None, dep_dir_rel) pairs to clone into a worktree.

    Reads ``workspace_inputs`` from the app manifest (workshop.app.json) if
    declared; otherwise falls back to ``_PLATFORM_DEFAULT_INPUTS`` filtered to
    entries whose lockfile is actually present in the project tree.

    An entry without a ``lockfile`` key produces ``(None, dir)``; the clone
    contract is written with a null hash (asset and model-weight dirs are
    supported, not only dependency installs).
    """
    try:
        raw = _read_app_manifest(project_dir)
    except _AppError as e:
        raise ValueError(str(e)) from e

    declared = (raw or {}).get("workspace_inputs")
    if declared is not None:
        result: list[tuple[str | None, str]] = []
        for entry in declared:
            if isinstance(entry, dict) and entry.get("dir"):
                result.append((entry.get("lockfile") or None, str(entry["dir"])))
        return result

    return [
        (lockfile, dep)
        for lockfile, dep in _PLATFORM_DEFAULT_INPUTS
        if (project_dir / lockfile).exists()
    ]


def _free_bytes(path: Path) -> int:
    """Available bytes on the volume containing *path*."""
    st = os.statvfs(path)
    return st.f_frsize * st.f_bavail


def _lockfile_hash(lockfile: Path) -> str | None:
    """First 16 hex chars of the SHA-256 of *lockfile*; None if absent."""
    if not lockfile.exists():
        return None
    return hashlib.sha256(lockfile.read_bytes()).hexdigest()[:16]


def _hardlink_clone(src: Path, dst: Path) -> None:
    """``cp -al src dst`` — hardlink-clone *src* tree to *dst* (must not exist)."""
    if dst.exists():
        shutil.rmtree(dst)
    r = subprocess.run(
        ["cp", "-al", str(src), str(dst)],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        raise ValueError(
            f"hardlink-clone failed ({src} → {dst}): "
            f"{(r.stderr or r.stdout or '').strip()[:300]}"
        )


def _write_dep_contract(dep_dir: Path, *, lockfile_hash: str | None,
                        cloned_from: str) -> None:
    """Write ``<dep_dir>.dep-contract.json`` recording the read-only contract.

    The contract states that this directory is a hardlink-clone whose inodes
    are shared with *cloned_from*.  Any tool that needs to reinstall must
    ``rm -rf`` the directory first so it never writes into the shared inodes.
    """
    contract_path = dep_dir.parent / (dep_dir.name + ".dep-contract.json")
    contract_path.write_text(
        json.dumps({
            "readonly": True,
            "lockfile_hash": lockfile_hash,
            "cloned_from": cloned_from,
            "reinstall": "rm -rf this directory, then run the real install command",
        }, indent=2) + "\n",
        encoding="utf-8",
    )
