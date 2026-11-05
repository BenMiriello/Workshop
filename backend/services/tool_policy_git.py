"""Which git commands delete (`tool_policy_shell`): only what git cannot bring
back. `git rm` without force (git refuses to drop an unsaved change), and
`git checkout -- paths` or `git restore` of paths that are all tracked with
nothing unsaved, untracked or ignored under them, asked of git in the command's
own directory, are not deletes. Nor is `git worktree remove` of a copy holding
no uncommitted work; with `--force` over uncommitted files it is.

`keep` makes the rest recoverable before they run, by pointing a ref under
`refs/wip/` at what they would lose: a branch's tip, a stash, or the unsaved
changes a checkout, restore, reset or forced `git rm` discards (`git stash
create`, which records them without touching the tree). The policy hook calls
it, and a command it kept runs without asking.

Joined into the policy hook's own source (`tool_policy.hook_source`), so it
imports nothing of the backend.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import time

# What `git worktree remove --force` may take without it counting as work: the
# same rebuildable names as `storage.kinds`, and symlinks, whose targets stay.
_REBUILDABLE_DIRS = frozenset({
    "node_modules", ".venv", "venv", "__pycache__", "dist",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".next", ".turbo", ".parcel-cache", ".vite",
})


def _git(cwd: str, *args: str, timeout: float = 5) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=timeout)


def _in_git(paths: list[str], cwd: str | None) -> bool:
    """Whether git can bring back every path as it is now: all tracked, with
    no unsaved change and no untracked or ignored file under any of them."""
    if not cwd or not paths:
        return False
    try:
        tracked = subprocess.run(["git", "-C", cwd, "ls-files", "--error-unmatch", "--", *paths],
                                 capture_output=True, timeout=3)
        if tracked.returncode != 0:
            return False
        status = subprocess.run(["git", "-C", cwd, "status", "--porcelain", "--ignored",
                                 "--untracked-files=all", "--", *paths],
                                capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return False
    return status.returncode == 0 and not status.stdout.strip()


def _forced(rest: list[str]) -> bool:
    return any(a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a) for a in rest)


def _pathspec(rest: list[str], value_opts: tuple[str, ...] = ()) -> list[str]:
    """The paths of a git command: after `--` when given, else its operands."""
    if "--" in rest:
        return rest[rest.index("--") + 1:]
    out, skip = [], False
    for a in rest:
        if skip:
            skip = False
        elif a in value_opts:
            skip = True
        elif not a.startswith("-"):
            out.append(a)
    return out


def _worktree_has_work(tree: str, cwd: str | None) -> bool:
    """Whether a worktree holds anything uncommitted besides rebuildable
    folders and symlinks. Unknown counts as yes."""
    where = tree if os.path.isabs(tree) else (os.path.join(cwd, tree) if cwd else None)
    if not where or not os.path.isdir(where):
        return where is None
    try:
        status = _git(where, "status", "--porcelain", "--untracked-files=normal")
    except (OSError, subprocess.SubprocessError):
        return True
    if status.returncode != 0:
        return True
    for line in status.stdout.splitlines():
        path = line[3:].strip().strip('"').rstrip("/")
        if line.startswith("??") and (os.path.islink(os.path.join(where, path))
                                      or _REBUILDABLE_DIRS & set(path.split("/"))):
            continue
        return True
    return False


def _git_deletes(args: list[str], cwd: str | None = None) -> str | None:
    if not args:
        return None
    sub, rest = args[0], args[1:]
    if sub == "rm" and "--cached" not in rest:
        if not _forced(rest) or _in_git(_pathspec(rest), cwd):
            return None
        return "git rm"
    if sub == "clean":
        return None if any(a in ("-n", "--dry-run") for a in rest) else "git clean"
    if sub == "reset" and "--hard" in rest:
        return "git reset --hard"
    if sub == "branch" and any(a in ("-D", "-d", "--delete") for a in rest):
        return "git branch delete"
    if sub == "stash" and rest[:1] and rest[0] in ("drop", "clear"):
        return f"git stash {rest[0]}"
    if sub == "push" and any(a in ("-f", "--force", "--delete", "-d") or a.startswith("--force") or a.startswith(":") for a in rest):
        return "git push that deletes or overwrites"
    if sub == "worktree" and rest[:1] == ["remove"] and _forced(rest[1:]):
        trees = _pathspec(rest[1:])
        return "git worktree remove discarding changes" if any(
            _worktree_has_work(t, cwd) for t in trees) else None
    if sub == "restore" and not ("--staged" in rest or "-S" in rest) or \
            sub == "restore" and ("--worktree" in rest or "-W" in rest):
        paths = _pathspec(rest, ("-s", "--source"))
        return None if _in_git(paths, cwd) else "git restore discarding changes"
    if sub == "checkout" and any(a in ("--", ".", "-f", "--force") for a in rest):
        paths = rest[rest.index("--") + 1:] if "--" in rest else ["."]
        return None if _in_git(paths, cwd) else "git checkout discarding changes"
    return None


def _stamp() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def _save(cwd: str, ref: str, sha: str) -> bool:
    return _git(cwd, "update-ref", ref, sha).returncode == 0


def _keep_branches(names: list[str], cwd: str) -> bool:
    stamp = _stamp()
    for name in names:
        tip = _git(cwd, "rev-parse", "--verify", "-q", f"refs/heads/{name}^{{commit}}")
        if tip.returncode != 0:
            continue
        sha = tip.stdout.strip()
        holders = _git(cwd, "for-each-ref", "--contains", sha, "--format=%(refname)")
        if any(r and r != f"refs/heads/{name}" for r in holders.stdout.split()):
            continue
        if not _save(cwd, f"refs/wip/branches/{stamp}/{name}", sha):
            return False
    return True


def _keep_stashes(rest: list[str], cwd: str) -> bool:
    if rest[0] == "clear":
        listed = _git(cwd, "stash", "list", "--format=%H")
        shas = listed.stdout.split() if listed.returncode == 0 else []
    else:
        which = next((a for a in rest[1:] if not a.startswith("-")), "stash@{0}")
        found = _git(cwd, "rev-parse", "--verify", "-q", which)
        shas = [found.stdout.strip()] if found.returncode == 0 else []
    stamp = _stamp()
    return all(_save(cwd, f"refs/wip/stash/{stamp}-{i}", sha) for i, sha in enumerate(shas))


def _keep_changes(cwd: str) -> bool:
    made = _git(cwd, "stash", "create", "kept before a discard", timeout=30)
    if made.returncode != 0:
        return False
    sha = made.stdout.strip()
    return not sha or _save(cwd, f"refs/wip/discarded/{_stamp()}", sha)


def keep(args: list[str], cwd: str | None) -> bool:
    """Make a deleting git command recoverable before it runs. True when what it
    would lose is now under `refs/wip/` (or it loses nothing)."""
    if not cwd or not args:
        return False
    sub, rest = args[0], args[1:]
    try:
        if sub == "branch":
            return _keep_branches(
                [a for a in rest if not a.startswith("-") and a not in ("-D", "-d")], cwd)
        if sub == "stash" and rest[:1] and rest[0] in ("drop", "clear"):
            return _keep_stashes(rest, cwd)
        if sub in ("checkout", "restore", "reset", "rm"):
            return _keep_changes(cwd)
    except (OSError, subprocess.SubprocessError):
        return False
    return False
