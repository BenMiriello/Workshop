"""Git, and nothing else.

Section 13.1 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`:
git and version-management tooling is its own dedicated service, and its REST
surface (`routes/git.py`, `/git/...`) is a standalone resource that takes an
explicit repository reference. So this module knows what a repository, a ref, a
worktree and a commit are, and it knows NOTHING else. It must never learn what a
project, an app, a ticket, a lease, a chat or a scope is, and it never reads a
registry. Every function takes the repository path as its first argument, which
is what makes it usable against an arbitrary repo by a route, a CLI, an agent
tool or a future desktop client rather than only by the one caller that can
resolve a project slug.

The remote half is deliberately absent: `services/github.py` owns the GitHub
surface (`gh` CLI, PRs, fetch/rebase against origin) and keeps it. The one
outward verb here is `push`, which is git's own transport rather than GitHub's
API, and it exists because publishing a branch pushes before it opens a PR.

Conventions, carried over verbatim from `services/worktrees.py::_run` so that
extracting this module changed no behaviour: `git -C <repo> ...` (never a `cwd`
kwarg), the neutral Workshop Projects committer identity layered over the
ambient environment, captured text output, and an explicit per-verb timeout.

Failure model, two shapes on purpose:

  * Plumbing that the caller cannot sensibly continue past raises `GitError`,
    which carries `returncode`, `stdout` and `stderr` so the caller can build
    its own message.
  * Verbs whose failure IS the answer (a merge that conflicts, a checkout that
    cannot happen, a best-effort teardown) return a structured dict or a bool
    and never raise for the expected failure.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

# The neutral committer identity, mirroring routes/projects.py::_git_init. Every
# commit this service makes lands under it so history reads consistently
# regardless of which operator or agent triggered the write.
IDENTITY_NAME = "Workshop Projects"
IDENTITY_EMAIL = "projects@workshop.local"

# Default timeout for a git invocation with no reason to want another one.
DEFAULT_TIMEOUT = 30


class GitError(RuntimeError):
    """A git invocation that failed where the caller expected it to work.

    Carries the pieces a caller needs to render its own message: `returncode`,
    `stdout`, `stderr`, and `cmd` (the argv actually run, minus the `git -C
    <repo>` prefix).
    """

    def __init__(self, cmd: list[str], returncode: int, stdout: str, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        detail = (self.stderr or self.stdout).strip()[:400]
        super().__init__(
            f"git {' '.join(cmd)} failed (exit {returncode}): {detail}"
        )


def git_env() -> dict:
    """Ambient environment plus the neutral committer identity."""
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": IDENTITY_NAME,
        "GIT_AUTHOR_EMAIL": IDENTITY_EMAIL,
        "GIT_COMMITTER_NAME": IDENTITY_NAME,
        "GIT_COMMITTER_EMAIL": IDENTITY_EMAIL,
    }


def _run(repo: Path | str, args: list[str], *, check: bool = True,
         timeout: int = DEFAULT_TIMEOUT,
         env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run `git -C <repo> <args>` and return the completed process.

    `check=True` raises `GitError` (not `CalledProcessError`) so callers depend
    on this module's own failure type rather than on subprocess internals.

    `env_extra` layers over `git_env()`. Exactly one caller needs it and the
    reason is worth stating: `snapshot(include_untracked=True)` stages into a
    THROWAWAY `GIT_INDEX_FILE` so it can capture untracked files without ever
    touching the repository's real index. An `env=` kwarg on the call site
    would have had to rebuild the committer identity too, which is how two
    slightly different environments start existing.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        env={**git_env(), **(env_extra or {})},
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise GitError(args, proc.returncode, proc.stdout, proc.stderr)
    return proc


def _detail(proc: subprocess.CompletedProcess) -> str:
    return (proc.stderr or proc.stdout or "").strip()


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------

def is_repo(repo: Path | str) -> bool:
    """True when *repo* is inside a git working tree."""
    try:
        r = _run(repo, ["rev-parse", "--is-inside-work-tree"], check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and (r.stdout or "").strip() == "true"


def rev_parse(repo: Path | str, rev: str = "HEAD", *, abbrev_ref: bool = False,
              verify: bool = False) -> str | None:
    """Resolve *rev*; None when it does not resolve.

    `abbrev_ref` returns the symbolic short name (HEAD -> the branch name),
    `verify` asks git to fail rather than echo an unresolvable argument back.
    """
    args = ["rev-parse"]
    if verify:
        args.append("--verify")
    if abbrev_ref:
        args.append("--abbrev-ref")
    args.append(rev)
    r = _run(repo, args, check=False, timeout=10)
    if r.returncode != 0:
        return None
    out = (r.stdout or "").strip()
    return out or None


def main_branch(repo: Path | str) -> str:
    """Best-effort detection of the repository's main branch name.

    Prefers `main`, falls back to `master`, then to whatever HEAD points at,
    then to the literal "main" for a repo with no commits at all.
    """
    for candidate in ("main", "master"):
        if rev_parse(repo, candidate, verify=True):
            return candidate
    return rev_parse(repo, "HEAD", abbrev_ref=True) or "main"


def status(repo: Path | str) -> dict:
    """Porcelain working-tree status.

    Returns `{branch, clean, entries: [{status, path}]}`. `branch` is None on a
    detached HEAD. `status` is the two-character porcelain code verbatim (`??`
    for untracked, ` M` for an unstaged modification, and so on).
    """
    r = _run(repo, ["status", "--porcelain", "--branch"], check=False, timeout=20)
    branch: str | None = None
    entries: list[dict] = []
    for line in (r.stdout or "").splitlines():
        if line.startswith("## "):
            head = line[3:].split("...", 1)[0].strip()
            branch = None if head.startswith("HEAD (no branch)") else head
            continue
        if not line.strip():
            continue
        entries.append({"status": line[:2], "path": line[3:].strip()})
    return {"branch": branch, "clean": not entries, "entries": entries}


def log(repo: Path | str, *, ref: str | None = None, max_count: int = 50,
        paths: list[str] | None = None) -> list[dict]:
    """Commits newest-first: `[{sha, subject, author, date}]`.

    Returns [] rather than raising when the ref does not resolve (an empty repo
    has no HEAD), because "no commits" is an answer the caller can use.
    """
    args = ["log", f"--max-count={int(max_count)}",
            "--format=%H%x1f%s%x1f%an%x1f%aI"]
    if ref:
        args.append(ref)
    if paths:
        args.append("--")
        args.extend(paths)
    r = _run(repo, args, check=False, timeout=60)
    if r.returncode != 0:
        return []
    out: list[dict] = []
    for line in (r.stdout or "").splitlines():
        if "\x1f" not in line:
            continue
        parts = line.split("\x1f")
        while len(parts) < 4:
            parts.append("")
        out.append({
            "sha": parts[0].strip(),
            "subject": parts[1],
            "author": parts[2],
            "date": parts[3].strip(),
        })
    return out


def tag_shas(repo: Path | str) -> set[str]:
    """Object shas every tag points at. Empty set when there are no tags."""
    r = _run(repo, ["tag", "--format=%(objectname)"], check=False, timeout=20)
    if r.returncode != 0:
        return set()
    return {s.strip() for s in (r.stdout or "").splitlines() if s.strip()}


def _range(ref_a: str | None, ref_b: str | None, three_dot: bool) -> list[str]:
    """The rev argument(s) for a diff: one ref, `a..b`, or `a...b`."""
    if ref_a and ref_b:
        return [f"{ref_a}{'...' if three_dot else '..'}{ref_b}"]
    if ref_a:
        return [ref_a]
    return []


def diff_stat(repo: Path | str, ref_a: str | None = None, ref_b: str | None = None,
              *, three_dot: bool = False) -> str:
    """The `--shortstat` summary line for a diff; "" when there is no change."""
    r = _run(repo, ["diff", "--shortstat", *_range(ref_a, ref_b, three_dot)],
             check=False, timeout=15)
    return (r.stdout or "").strip()


def diff_name_status(repo: Path | str, ref_a: str | None = None,
                     ref_b: str | None = None, *, three_dot: bool = False) -> list[dict]:
    """Changed paths as `[{status, path}]`.

    `status` is git's letter (A/M/D/R100/...). For a rename git prints both the
    old and the new path; the LAST field is kept, which is the new one.
    """
    r = _run(repo, ["diff", "--name-status", *_range(ref_a, ref_b, three_dot)],
             check=False, timeout=15)
    out: list[dict] = []
    for line in (r.stdout or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        out.append({"status": parts[0].strip(), "path": parts[-1].strip()})
    return out


def diff(repo: Path | str, ref_a: str | None = None, ref_b: str | None = None,
         *, three_dot: bool = False, paths: list[str] | None = None) -> str:
    """The unified patch text, optionally limited to *paths*."""
    args = ["diff", *_range(ref_a, ref_b, three_dot)]
    if paths:
        args.append("--")
        args.extend(paths)
    r = _run(repo, args, check=False, timeout=15)
    return r.stdout or ""


def ahead_behind(repo: Path | str, upstream: str, ref: str = "HEAD") -> dict:
    """How far *ref* is ahead of / behind *upstream*. `{ahead, behind}`.

    `{ahead: 0, behind: 0}` when either ref is missing, so a caller rendering a
    panel never has to special-case an unborn or unfetched branch.
    """
    r = _run(repo, ["rev-list", "--left-right", "--count", f"{upstream}...{ref}"],
             check=False, timeout=30)
    if r.returncode != 0:
        return {"ahead": 0, "behind": 0}
    parts = (r.stdout or "").split()
    if len(parts) != 2:
        return {"ahead": 0, "behind": 0}
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return {"ahead": 0, "behind": 0}
    return {"ahead": ahead, "behind": behind}


def is_ancestor(repo: Path | str, ancestor: str, descendant: str) -> bool:
    """True when *ancestor*'s tip is contained in *descendant* (already merged).

    False on any non-zero exit, which covers both "not merged" and "that ref
    does not exist".
    """
    r = _run(repo, ["merge-base", "--is-ancestor", ancestor, descendant],
             check=False, timeout=20)
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------

def branch_list(repo: Path | str, *, pattern: str | None = None) -> list[str]:
    """Local branch names, optionally filtered by a git refname glob."""
    args = ["branch", "--list", "--format=%(refname:short)"]
    if pattern:
        args.append(pattern)
    r = _run(repo, args, check=False, timeout=15)
    return [b.strip() for b in (r.stdout or "").splitlines() if b.strip()]


def branch_create(repo: Path | str, name: str, start_point: str | None = None) -> str:
    """Create branch *name* (at *start_point* when given). Raises on failure."""
    args = ["branch", name]
    if start_point:
        args.append(start_point)
    _run(repo, args, check=True, timeout=15)
    return name


def branch_delete(repo: Path | str, name: str, *, force: bool = True) -> bool:
    """Delete branch *name*. Best-effort: False when git refused, never raises.

    `force` is the default because the callers that delete a branch have already
    decided its content is either merged or unwanted, and an unforced delete
    would leave a ref behind that blocks re-creating the same name.
    """
    r = _run(repo, ["branch", "-D" if force else "-d", name],
             check=False, timeout=15)
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Worktrees
# ---------------------------------------------------------------------------

def worktree_list(repo: Path | str) -> list[dict]:
    """Every worktree of *repo*, main one included, from `--porcelain`.

    Each record: `{dir, head, branch, locked, detached, prunable}`. `branch` is
    the short name (`refs/heads/` stripped) or None on a detached HEAD.
    """
    r = _run(repo, ["worktree", "list", "--porcelain"], check=True, timeout=15)
    items: list[dict] = []
    current: dict = {}

    def flush() -> None:
        if current:
            items.append({
                "dir": current.get("dir", ""),
                "head": current.get("head"),
                "branch": current.get("branch"),
                "locked": bool(current.get("locked")),
                "detached": bool(current.get("detached")),
                "prunable": bool(current.get("prunable")),
            })

    for raw in (r.stdout or "").splitlines():
        line = raw.rstrip()
        if line.startswith("worktree "):
            flush()
            current = {"dir": line[len("worktree "):]}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            current["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            current["detached"] = True
        elif line == "locked" or line.startswith("locked "):
            current["locked"] = True
        elif line == "prunable" or line.startswith("prunable "):
            current["prunable"] = True
        elif line == "":
            flush()
            current = {}
    flush()
    return items


def worktree_add(repo: Path | str, path: Path | str, *, branch: str | None = None,
                 start_point: str | None = None, timeout: int = 60) -> dict:
    """Add a worktree at *path*, creating *branch* there when given.

    Raises `GitError` when git refuses (the path exists, the branch exists, the
    start point is unknown), because there is no useful partial outcome.
    """
    args = ["worktree", "add"]
    if branch:
        args.extend(["-b", branch])
    args.append(str(path))
    if start_point:
        args.append(start_point)
    _run(repo, args, check=True, timeout=timeout)
    return {"dir": str(path), "branch": branch}


def worktree_lock(repo: Path | str, path: Path | str, *, reason: str = "") -> bool:
    """Lock a worktree so a crashed holder's stale index cannot wedge siblings.

    Best-effort: False when git refused (already locked, unknown worktree).
    """
    args = ["worktree", "lock"]
    if reason:
        args.extend(["--reason", reason])
    args.append(str(path))
    return _run(repo, args, check=False, timeout=15).returncode == 0


def worktree_unlock(repo: Path | str, path: Path | str) -> bool:
    """Unlock a worktree. The other half of `worktree_lock`, and the reason it
    exists: `git worktree remove --force` REFUSES a locked worktree ("cannot
    remove a locked working tree"), and so does `git worktree prune`. Without
    this, a tree that was locked at allocation can only be removed by deleting
    the directory behind git's back, which leaves the administrative metadata
    saying the branch is still checked out -- and then `branch -D` fails too.

    Best-effort: False when git refused (not locked, unknown worktree).
    """
    return _run(repo, ["worktree", "unlock", str(path)],
                check=False, timeout=15).returncode == 0


def worktree_remove(repo: Path | str, path: Path | str, *, force: bool = True) -> bool:
    """Remove a worktree. Best-effort: False when git refused, never raises.

    A False here does NOT mean the directory is still there; callers that need
    the path free should check and clean up themselves.
    """
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    return _run(repo, args, check=False, timeout=20).returncode == 0


def worktree_prune(repo: Path | str) -> bool:
    """Drop administrative metadata for worktrees whose directory is gone."""
    return _run(repo, ["worktree", "prune"], check=False, timeout=15).returncode == 0


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def commit(repo: Path | str, message: str, *, all: bool = False,
           allow_empty: bool = False, paths: list[str] | None = None) -> dict:
    """Commit the index (plus tracked modifications when `all`).

    Returns `{ok, commit, detail}`. A commit with nothing staged is not an
    exception: `ok` is False and `detail` carries git's own words.
    """
    args = ["commit", "-m", message]
    if all:
        args.append("-a")
    if allow_empty:
        args.append("--allow-empty")
    if paths:
        args.append("--")
        args.extend(paths)
    r = _run(repo, args, check=False, timeout=60)
    if r.returncode != 0:
        return {"ok": False, "commit": None, "detail": _detail(r)}
    return {"ok": True, "commit": rev_parse(repo, "HEAD"), "detail": ""}


def conflicted_paths(repo: Path | str) -> list[str]:
    """Paths currently in a conflicted (unmerged) state."""
    r = _run(repo, ["diff", "--name-only", "--diff-filter=U"], check=False, timeout=15)
    return [p.strip() for p in (r.stdout or "").splitlines() if p.strip()]


def merge(repo: Path | str, ref: str, *, message: str = "",
          prefer_fast_forward: bool = True, allow_merge_commit: bool = True) -> dict:
    """Merge *ref* into the checked-out branch, fast-forward preferred.

    Fast-forward is attempted first; when the branches have diverged a real
    merge commit is made under the neutral identity. A merge that cannot be
    completed is ABORTED before returning, so the working tree is never left
    mid-merge for the next caller to discover.

    Returns `{ok, fast_forward, conflict, conflicts, commit, detail}`.
    `conflicts` is the list of unmerged paths as of the failure, captured before
    the abort, so the caller can say what collided without re-running the merge.
    """
    if prefer_fast_forward:
        ff = _run(repo, ["merge", "--ff-only", ref], check=False, timeout=30)
        if ff.returncode == 0:
            return {"ok": True, "fast_forward": True, "conflict": False,
                    "conflicts": [], "commit": rev_parse(repo, "HEAD"), "detail": ""}
        if not allow_merge_commit:
            return {"ok": False, "fast_forward": False, "conflict": False,
                    "conflicts": [], "commit": None, "detail": _detail(ff)}

    args = ["merge", "--no-ff"]
    if message:
        args.extend(["-m", message])
    args.append(ref)
    m = _run(repo, args, check=False, timeout=60)
    if m.returncode != 0:
        conflicts = conflicted_paths(repo)
        _run(repo, ["merge", "--abort"], check=False, timeout=20)
        return {"ok": False, "fast_forward": False, "conflict": bool(conflicts),
                "conflicts": conflicts, "commit": None,
                "detail": (m.stderr or m.stdout or "merge failed").strip()}
    return {"ok": True, "fast_forward": False, "conflict": False, "conflicts": [],
            "commit": rev_parse(repo, "HEAD"), "detail": ""}


def snapshot(repo: Path | str, *, ref: str | None = None, message: str = "",
             include_untracked: bool = False) -> dict:
    """Snapshot the working tree to a DANGLING commit without touching HEAD.

    Section 9 of workspace-ownership-and-leases-2026-08-22.md: yielding a
    workspace must not put a half-applied, possibly non-compiling commit onto
    the branch, and must not move HEAD for the next holder. `git stash create`
    produces exactly that: a real commit object, diffable and revertable and
    resumable, reachable from nothing. Passing *ref* (e.g. `refs/wip/<session>`)
    additionally points a ref at it so it survives gc and can be linked from a
    timeline.

    Returns `{ok, sha, ref, clean, detail}`. A clean tree has nothing to stash,
    which is a success with `sha=None` and `clean=True`, not an error. Note that
    `stash create` captures tracked modifications only; an untracked file is not
    part of the snapshot.

    `include_untracked=True` SWITCHES TO SECTION 9'S OTHER NAMED MECHANISM,
    `git commit-tree`, and it exists because of what happens NEXT in the quiesce
    (`services/control.quiesce`): the tree is restored and untracked files are
    deleted. Snapshotting with `stash create` and then cleaning would destroy
    every untracked file the holder had written, which is the opposite of "the
    snapshot is what makes cleaning it safe". `git stash create` cannot help
    here -- it takes a message and no options, so `-u` is read as the message
    (verified against git 2.39) -- so the untracked-inclusive path stages into a
    throwaway index and commits that tree directly. The real index, HEAD and the
    branch are all untouched, exactly as with `stash create`.
    """
    if include_untracked:
        return _snapshot_commit_tree(repo, ref=ref, message=message)
    args = ["stash", "create"]
    if message:
        args.append(message)
    r = _run(repo, args, check=False, timeout=60)
    if r.returncode != 0:
        return {"ok": False, "sha": None, "ref": None, "clean": False,
                "detail": _detail(r)}
    sha = (r.stdout or "").strip()
    if not sha:
        return {"ok": True, "sha": None, "ref": None, "clean": True, "detail": ""}
    if ref:
        u = _run(repo, ["update-ref", ref, sha], check=False, timeout=15)
        if u.returncode != 0:
            return {"ok": False, "sha": sha, "ref": None, "clean": False,
                    "detail": _detail(u)}
    return {"ok": True, "sha": sha, "ref": ref, "clean": False, "detail": ""}


def _snapshot_commit_tree(repo: Path | str, *, ref: str | None,
                          message: str) -> dict:
    """`snapshot(include_untracked=True)`: everything git would add, as a commit.

    Section 9 names this alternative directly ("`git stash create`, or `git
    commit-tree` into `refs/wip/<session-id>`"). The whole trick is
    `GIT_INDEX_FILE`: `git add -A` runs against a temporary index in a temp
    directory, so the repository's own `.git/index` is never written and a
    holder's staged-versus-unstaged split survives the snapshot. `.gitignore` is
    still honoured, which is what keeps `node_modules` out of it.

    Same return shape as `snapshot`, including the clean case: when the staged
    tree is identical to HEAD's there was nothing to record, so `sha` is None
    and `clean` is True rather than a commit that says nothing happened.
    """
    head = rev_parse(repo, "HEAD")
    head_tree = rev_parse(repo, "HEAD^{tree}") if head else None
    with tempfile.TemporaryDirectory(prefix="workshop-snapshot-") as td:
        env_extra = {"GIT_INDEX_FILE": str(Path(td) / "index")}
        if head:
            r = _run(repo, ["read-tree", "HEAD"], check=False, timeout=60,
                     env_extra=env_extra)
            if r.returncode != 0:
                return {"ok": False, "sha": None, "ref": None, "clean": False,
                        "detail": _detail(r)}
        r = _run(repo, ["add", "-A"], check=False, timeout=180,
                 env_extra=env_extra)
        if r.returncode != 0:
            return {"ok": False, "sha": None, "ref": None, "clean": False,
                    "detail": _detail(r)}
        w = _run(repo, ["write-tree"], check=False, timeout=60,
                 env_extra=env_extra)
        if w.returncode != 0:
            return {"ok": False, "sha": None, "ref": None, "clean": False,
                    "detail": _detail(w)}
        tree = (w.stdout or "").strip()
    if not tree:
        return {"ok": False, "sha": None, "ref": None, "clean": False,
                "detail": "git write-tree produced no tree"}
    if head_tree and tree == head_tree:
        return {"ok": True, "sha": None, "ref": None, "clean": True, "detail": ""}
    args = ["commit-tree", tree]
    if head:
        args += ["-p", head]
    args += ["-m", message or "workshop wip snapshot"]
    c = _run(repo, args, check=False, timeout=60)
    if c.returncode != 0:
        return {"ok": False, "sha": None, "ref": None, "clean": False,
                "detail": _detail(c)}
    sha = (c.stdout or "").strip()
    if not sha:
        return {"ok": False, "sha": None, "ref": None, "clean": False,
                "detail": "git commit-tree produced no commit"}
    if ref:
        u = _run(repo, ["update-ref", ref, sha], check=False, timeout=15)
        if u.returncode != 0:
            return {"ok": False, "sha": sha, "ref": None, "clean": False,
                    "detail": _detail(u)}
    return {"ok": True, "sha": sha, "ref": ref, "clean": False, "detail": ""}


def git_dir(repo: Path | str) -> str | None:
    """The absolute git directory for *repo*, or None if it is not a repo.

    NOT `<repo>/.git`, and the difference is the whole reason this exists: in a
    linked worktree `.git` is a FILE pointing at
    `<main>/.git/worktrees/<name>`, and that is where the index -- and the lock
    beside it -- actually lives. Anything that guessed `<repo>/.git/index.lock`
    would look in the wrong place for exactly the trees this design is about.
    """
    r = _run(repo, ["rev-parse", "--absolute-git-dir"], check=False, timeout=10)
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


def index_lock(repo: Path | str) -> str | None:
    """The path of this repo's `index.lock` if one is present, else None."""
    gd = git_dir(repo)
    if not gd:
        return None
    lock = Path(gd) / "index.lock"
    return str(lock) if lock.exists() else None


def clear_index_lock(repo: Path | str) -> bool:
    """Remove a leftover `index.lock`. True if one was there.

    Section 9: a force-stopped holder leaves a killed git's lock behind, and
    every subsequent write to that tree fails until it goes -- INCLUDING the
    snapshot, which is why the quiesce clears it first rather than last.

    THE PRECONDITION IS THE CALLER'S. Removing the lock of a git that is still
    running corrupts the index, so this may only be called once the holder has
    been stopped. It is not a periodic sweeper and nothing calls it on a read
    path.
    """
    lock = index_lock(repo)
    if not lock:
        return False
    try:
        os.unlink(lock)
    except OSError:
        return False
    return True


def reset_hard(repo: Path | str, ref: str | None = None) -> dict:
    """`git reset --hard [<ref>]`. Returns `{ok, detail}`.

    WITH NO REF THE BRANCH TIP DOES NOT MOVE, which is the only reason this is
    safe in the quiesce. `git reset --hard` resets the current branch head to
    the commit given, and that commit defaults to HEAD -- so the tracked half of
    the working tree and the index go back to what the branch already says,
    while the branch itself, and HEAD, stay exactly where the holder left them.
    Restoring with `checkout --force HEAD` would DETACH HEAD instead, handing
    the next holder a tree in a state nobody chose.
    """
    args = ["reset", "--hard"]
    if ref:
        args.append(ref)
    r = _run(repo, args, check=False, timeout=120)
    return {"ok": r.returncode == 0, "detail": _detail(r)}


def checkout(repo: Path | str, ref: str, *, create: bool = False,
             force: bool = False) -> dict:
    """Check out *ref*. Returns `{ok, detail}`; a refusal is data, not an error."""
    args = ["checkout"]
    if force:
        args.append("--force")
    if create:
        args.append("-b")
    args.append(ref)
    r = _run(repo, args, check=False, timeout=20)
    return {"ok": r.returncode == 0, "detail": _detail(r)}


def clean(repo: Path | str, *, directories: bool = True, ignored: bool = False,
          dry_run: bool = False) -> list[str]:
    """Remove untracked files; returns the paths removed (or that would be).

    Used by the quiesce path: once the working tree has been snapshotted to a
    dangling ref, the leftovers can be cleared so the next holder is not handed
    someone else's half-applied edits.
    """
    args = ["clean", "-n" if dry_run else "-f"]
    if directories:
        args.append("-d")
    if ignored:
        args.append("-x")
    r = _run(repo, args, check=False, timeout=60)
    out: list[str] = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        for prefix in ("Would remove ", "Removing "):
            if line.startswith(prefix):
                out.append(line[len(prefix):].strip())
                break
    return out


def push(repo: Path | str, remote: str, ref: str, *, set_upstream: bool = False,
         timeout: int = 180) -> dict:
    """Push *ref* to *remote*. Returns `{ok, detail}`.

    The only outward-facing verb here: it is git's transport, not GitHub's API.
    Anything that needs a PR goes through `services/github.py`.
    """
    args = ["push"]
    if set_upstream:
        args.append("-u")
    args.extend([remote, ref])
    r = _run(repo, args, check=False, timeout=timeout)
    return {"ok": r.returncode == 0, "detail": _detail(r)}
