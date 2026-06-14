"""Git-worktree management for project branches (per ADR-009 section 7).

A "branch" here is a worktree-pinned development branch off a project's main.
A Build agent can pin to one of these branches so multiple agents can work on
the same project without stepping on each other's edits. Merging happens
through the UI: fast-forward when possible, falling back to a regular merge
commit with the neutral committer identity. Worktree + branch are reaped on
success.

The disk concept stays "worktree" (git's term); the URL path stays
/api/worktrees/. The user-facing concept is the branch.

Public surface:
  create_branch(project_slug, branch_slug) -> dict
  list_branches(project_slug) -> list[dict]
  merge_branch(project_slug, branch_slug, *, message="") -> dict
  discard_branch(project_slug, branch_slug) -> dict
  diff_branch(project_slug, branch_slug) -> dict
  branch_dir(project_slug, branch_slug) -> Path
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BRANCHES_ROOT = Path(os.environ.get(
    "WORKSHOP_BRANCHES_ROOT",
    str(Path.home() / "workshop" / "branches"),
))
PROJECTS_ROOT = Path(os.environ.get(
    "WORKSHOP_PROJECTS_ROOT_NEW",
    str(Path.home() / "workshop" / "projects"),
))
PROJECTS_REGISTRY = PROJECTS_ROOT.parent / "projects.json"

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,47}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git_env() -> dict:
    """Neutral committer env mirroring routes/projects.py::_git_init.

    All merge commits land under the Workshop Projects identity so the history
    reads consistently regardless of which operator triggered the merge.
    """
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "Workshop Projects",
        "GIT_AUTHOR_EMAIL": "projects@workshop.local",
        "GIT_COMMITTER_NAME": "Workshop Projects",
        "GIT_COMMITTER_EMAIL": "projects@workshop.local",
    }


def _validate_slug(value: str, label: str) -> str:
    v = (value or "").strip().lower()
    if not SLUG_RE.match(v):
        raise ValueError(f"{label} must be kebab-case alphanumeric, start with a letter, max 48 chars")
    return v


def _project_dir(project_slug: str) -> Path:
    """Locate the project's main directory via projects.json."""
    if not PROJECTS_REGISTRY.exists():
        raise ValueError(f"projects registry missing: {PROJECTS_REGISTRY}")
    try:
        reg = json.loads(PROJECTS_REGISTRY.read_text())
    except Exception as e:
        raise ValueError(f"projects registry unreadable: {e}")
    p = (reg.get("projects") or {}).get(project_slug)
    if not p or not p.get("dir"):
        raise ValueError(f"project not found: {project_slug}")
    pd = Path(p["dir"])
    if not pd.exists():
        raise ValueError(f"project directory missing: {pd}")
    return pd


def _branch_name(project_slug: str, branch_slug: str) -> str:
    return f"wt/{project_slug}/{branch_slug}"


def _branch_path(project_slug: str, branch_slug: str) -> Path:
    return BRANCHES_ROOT / project_slug / branch_slug


def _run(args: list[str], *, cwd: Path | None = None, check: bool = True,
         timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=_git_env(),
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def _main_branch(project_dir: Path) -> str:
    """Best-effort detection of the project's main branch name.

    Prefers `main`, falls back to `master`, then to whatever HEAD points at.
    """
    for candidate in ("main", "master"):
        r = subprocess.run(
            ["git", "-C", str(project_dir), "rev-parse", "--verify", candidate],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return candidate
    r = subprocess.run(
        ["git", "-C", str(project_dir), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    return (r.stdout or "main").strip() or "main"


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
        _run(
            ["git", "-C", str(project_dir), "worktree", "add", "-b", branch, str(target)],
            timeout=60,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()[:200]
        raise ValueError(f"git worktree add failed (exit {e.returncode}): {stderr}")
    return {
        "project_slug": project_slug,
        "branch_slug": branch_slug,
        "branch": branch,
        "dir": str(target),
        "created": _now(),
    }


def list_branches(project_slug: str) -> list[dict]:
    """List active branches for a project by parsing `git worktree list`.

    Only returns worktrees whose branch starts with wt/<project_slug>/.
    The main worktree is excluded.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    project_dir = _project_dir(project_slug)
    try:
        r = _run(["git", "-C", str(project_dir), "worktree", "list", "--porcelain"], timeout=15)
    except subprocess.CalledProcessError:
        return []
    items: list[dict] = []
    current: dict = {}
    prefix = f"wt/{project_slug}/"
    for raw in (r.stdout or "").splitlines():
        line = raw.rstrip()
        if line.startswith("worktree "):
            if current:
                _maybe_append(current, items, prefix)
            current = {"dir": line[len("worktree "):]}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            current["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "":
            if current:
                _maybe_append(current, items, prefix)
                current = {}
    if current:
        _maybe_append(current, items, prefix)
    return items


def _maybe_append(current: dict, items: list[dict], prefix: str) -> None:
    branch = current.get("branch") or ""
    if not branch.startswith(prefix):
        return
    branch_slug = branch[len(prefix):]
    items.append({
        "project_slug": prefix.split("/")[1],
        "branch_slug": branch_slug,
        "branch": branch,
        "dir": current.get("dir", ""),
        "head": current.get("head"),
    })


def diff_branch(project_slug: str, branch_slug: str) -> dict:
    """Diff the branch against the project's main branch.

    Returns a summary line (shortstat-style) plus per-file unified diffs.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    project_dir = _project_dir(project_slug)
    branch = _branch_name(project_slug, branch_slug)
    main = _main_branch(project_dir)
    base = f"{main}...{branch}"
    short = subprocess.run(
        ["git", "-C", str(project_dir), "diff", "--shortstat", base],
        env=_git_env(), capture_output=True, text=True, timeout=15,
    )
    summary = (short.stdout or "").strip() or "no changes vs main"
    name_status = subprocess.run(
        ["git", "-C", str(project_dir), "diff", "--name-status", base],
        env=_git_env(), capture_output=True, text=True, timeout=15,
    )
    files: list[dict] = []
    for line in (name_status.stdout or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0].strip()
        path = parts[-1].strip()
        diff_proc = subprocess.run(
            ["git", "-C", str(project_dir), "diff", base, "--", path],
            env=_git_env(), capture_output=True, text=True, timeout=15,
        )
        files.append({"path": path, "status": status, "diff": diff_proc.stdout})
    return {"summary": summary, "files": files}


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
    main = _main_branch(project_dir)

    # Make sure we're operating from main in the project's main worktree.
    co = subprocess.run(
        ["git", "-C", str(project_dir), "checkout", main],
        env=_git_env(), capture_output=True, text=True, timeout=20,
    )
    if co.returncode != 0:
        return {"ok": False, "reason": f"checkout {main} failed: {(co.stderr or '').strip()[:200]}"}

    ff = subprocess.run(
        ["git", "-C", str(project_dir), "merge", "--ff-only", branch],
        env=_git_env(), capture_output=True, text=True, timeout=30,
    )
    if ff.returncode != 0:
        msg = message.strip() or f"Merge branch {branch_slug} into {main}"
        merge = subprocess.run(
            ["git", "-C", str(project_dir), "merge", "--no-ff", "-m", msg, branch],
            env=_git_env(), capture_output=True, text=True, timeout=60,
        )
        if merge.returncode != 0:
            # Abort the in-progress merge so the working tree is clean again.
            subprocess.run(
                ["git", "-C", str(project_dir), "merge", "--abort"],
                env=_git_env(), capture_output=True, text=True, timeout=20,
            )
            return {"ok": False, "reason": (merge.stderr or merge.stdout or "merge failed").strip()[:400]}

    head = subprocess.run(
        ["git", "-C", str(project_dir), "rev-parse", "HEAD"],
        env=_git_env(), capture_output=True, text=True, timeout=10,
    ).stdout.strip()

    # Reap worktree + branch on success.
    target = _branch_path(project_slug, branch_slug)
    subprocess.run(
        ["git", "-C", str(project_dir), "worktree", "remove", "--force", str(target)],
        env=_git_env(), capture_output=True, text=True, timeout=20,
    )
    if target.exists():
        # If worktree remove balked (e.g. already pruned), try to clean up the
        # leftover dir so a re-create with the same slug works.
        shutil.rmtree(target, ignore_errors=True)
    subprocess.run(
        ["git", "-C", str(project_dir), "branch", "-D", branch],
        env=_git_env(), capture_output=True, text=True, timeout=15,
    )
    return {"ok": True, "commit": head}


def discard_branch(project_slug: str, branch_slug: str) -> dict:
    """Force-remove a branch worktree and delete its git ref."""
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    project_dir = _project_dir(project_slug)
    branch = _branch_name(project_slug, branch_slug)
    target = _branch_path(project_slug, branch_slug)
    subprocess.run(
        ["git", "-C", str(project_dir), "worktree", "remove", "--force", str(target)],
        env=_git_env(), capture_output=True, text=True, timeout=20,
    )
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    subprocess.run(
        ["git", "-C", str(project_dir), "branch", "-D", branch],
        env=_git_env(), capture_output=True, text=True, timeout=15,
    )
    return {"ok": True}


def branch_dir(project_slug: str, branch_slug: str) -> Path:
    """Public helper: return the on-disk path for a (project, branch) pair.

    Used by services/build_agents.py to resolve cwd when a build agent is
    pinned to a branch.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    return _branch_path(project_slug, branch_slug)
