"""GitHub helpers for project repos (Phase G1 of the worktree-orchestration plan).

Thin, testable wrappers over `gh` and `git` for the connect/publish/merge flow.
No FastAPI here so the pure logic (naming, uniqueness, owner policy) can be
unit-tested without a server.

Hard policy baked into the code, not left as a flippable default:
  - Platform-created repos are ALWAYS `--private`. There is no public path.
  - Every repo belongs to ONE configured account. A remote owned by anyone else
    is refused rather than silently accepted.

WHICH account, and which accounts are off limits, are properties of the
INSTALLATION rather than of the platform. An operator whose work org must never
be touched by this system names it once, in ~/.config/workshop/instance.env,
and every path here refuses it:

    WORKSHOP_GITHUB_OWNER          the account this install pushes to
    WORKSHOP_BLOCKED_REPO_OWNERS   comma-separated; refused anywhere in an
                                   owner, name or remote URL

Public surface:
  owner() -> str
  reject_blocked(*strings)
  repo_base_name(slug, scratch) -> str
  unique_repo_name(base) -> str
  create_repo(dir, name) -> dict
  bind_repo(dir, remote_url) -> dict
  open_pr(dir, remote, base, head, title, body) -> dict
  merge_pr(dir, head, base) -> dict
  enable_rerere(dir)
  rebase_onto(dir, base) -> dict
  list_open_prs(remote) -> list[dict]
  ahead_behind(dir, base) -> dict
  is_merged(dir, branch, base) -> bool
"""
from __future__ import annotations

import json
import os
import re
import subprocess

OWNER_ENV = "WORKSHOP_GITHUB_OWNER"
BLOCKED_OWNERS_ENV = "WORKSHOP_BLOCKED_REPO_OWNERS"


def owner() -> str:
    """The GitHub account this installation owns its repos under.

    Unset is an error rather than a guess: a repo created under the wrong
    account is not something to notice after the fact.
    """
    value = (os.environ.get(OWNER_ENV) or "").strip()
    if not value:
        raise ValueError(
            f"no GitHub owner configured: set {OWNER_ENV} in "
            "~/.config/workshop/instance.env to the account this install uses")
    return value


def blocked_owners() -> tuple[str, ...]:
    raw = os.environ.get(BLOCKED_OWNERS_ENV) or ""
    return tuple(s.strip().lower() for s in raw.split(",") if s.strip())


def reject_blocked(*strings: str | None) -> None:
    """Raise ValueError if any argument names an account this install must not touch.

    Substring match, case-insensitive, so naming an org also catches its repos
    and their remote URLs. An empty configuration blocks nothing.
    """
    blocked = blocked_owners()
    if not blocked:
        return
    for s in strings:
        if not s:
            continue
        low = s.lower()
        for name in blocked:
            if name in low:
                raise ValueError(
                    f"{name!r} is out of scope for this installation "
                    f"({BLOCKED_OWNERS_ENV}): refusing owner/name/remote containing it")


def repo_base_name(slug: str, scratch: bool) -> str:
    """Repo name for a platform-created repo.

    Scratch/blank projects go under a clean `workshop-` namespace; real projects
    keep their own slug.
    """
    return f"workshop-{slug}" if scratch else slug


def _run(args: list[str], *, cwd: str | None = None, check: bool = True,
         timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def _repo_exists(owner: str, name: str) -> bool:
    """Read-only existence check via `gh repo view <owner>/<name>`.

    Returns True if the repo exists, False if `gh` reports it missing. Any other
    failure (auth, network) is surfaced so we do not silently treat an outage as
    "free" and then collide on create.
    """
    r = subprocess.run(
        ["gh", "repo", "view", f"{owner}/{name}"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode == 0:
        return True
    err = (r.stderr or "") + (r.stdout or "")
    if "Could not resolve" in err or "not found" in err.lower() or "404" in err:
        return False
    raise ValueError(f"gh repo view failed for {owner}/{name}: {err.strip()[:200]}")


def unique_repo_name(base: str) -> str:
    """Return `base`, or `base-2`, `base-3`, ... for the first name free on GitHub.

    Read-only: it only runs `gh repo view`. The happy path (base does not exist)
    returns `base` unchanged with a single read.
    """
    reject_blocked(base)
    if not _repo_exists(owner(), base):
        return base
    n = 2
    while True:
        candidate = f"{base}-{n}"
        if not _repo_exists(owner(), candidate):
            return candidate
        n += 1


def _default_branch(owner: str, name: str) -> str:
    r = subprocess.run(
        ["gh", "repo", "view", f"{owner}/{name}",
         "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name"],
        capture_output=True, text=True, timeout=60,
    )
    b = (r.stdout or "").strip()
    return b or "main"


def create_repo(dir: str, name: str) -> dict:
    """Create a NEW private GitHub repo from an existing local repo and push it.

    OUTWARD-FACING: this mutates GitHub. Runs, in one call:
      gh repo create <owner>/<name> --private --source=<dir> --remote=origin --push

    The `--private` flag is mandatory and unconditional; there is no code path
    that creates a public repo. Returns {owner, name, remote_url, default_branch}.
    """
    reject_blocked(name, dir)
    _owner = owner()
    full = f"{_owner}/{name}"
    try:
        _run(
            ["gh", "repo", "create", full, "--private",
             "--source", dir, "--remote", "origin", "--push"],
            timeout=180,
        )
    except subprocess.CalledProcessError as e:
        err = ((e.stderr or "") + (e.stdout or "")).strip()[:300]
        raise ValueError(f"gh repo create failed for {full}: {err}")
    return {
        "owner": _owner,
        "name": name,
        "remote_url": f"https://github.com/{_owner}/{name}.git",
        "default_branch": _default_branch(_owner, name),
    }


_OWNER_RE = re.compile(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?/?$")


def _parse_remote(remote_url: str) -> tuple[str, str]:
    m = _OWNER_RE.search(remote_url.strip())
    if not m:
        raise ValueError(f"unrecognized GitHub remote URL: {remote_url}")
    return m.group(1), m.group(2)


def bind_repo(dir: str, remote_url: str) -> dict:
    """Bind an existing local repo to an existing GitHub remote (no create).

    OUTWARD-FACING (git fetch touches the network). Validates the owner is this
    install's configured account, refuses any blocked account, wires `origin`,
    fetches, and resolves the default branch.
    Returns {owner, name, remote_url, default_branch}.
    """
    reject_blocked(remote_url)
    remote_owner, name = _parse_remote(remote_url)
    reject_blocked(remote_owner, name)
    configured = owner()
    if remote_owner != configured:
        raise ValueError(f"owner must be {configured}; got {remote_owner}")

    # Point origin at the remote (add, or reset if one already exists).
    existing = subprocess.run(
        ["git", "-C", dir, "remote", "get-url", "origin"],
        capture_output=True, text=True, timeout=15,
    )
    verb = "set-url" if existing.returncode == 0 else "add"
    try:
        _run(["git", "-C", dir, "remote", verb, "origin", remote_url], timeout=30)
        _run(["git", "-C", dir, "fetch", "origin"], timeout=120)
    except subprocess.CalledProcessError as e:
        err = ((e.stderr or "") + (e.stdout or "")).strip()[:300]
        raise ValueError(f"binding remote failed: {err}")

    # Resolve default branch from the remote HEAD, falling back to gh, then main.
    head = subprocess.run(
        ["git", "-C", dir, "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True, timeout=15,
    )
    default_branch = ""
    if head.returncode == 0:
        default_branch = (head.stdout or "").strip().rsplit("/", 1)[-1]
    if not default_branch:
        default_branch = _default_branch(owner, name)
    return {
        "owner": owner,
        "name": name,
        "remote_url": remote_url,
        "default_branch": default_branch or "main",
    }


def open_pr(dir: str, remote: str, base: str, head: str, title: str, body: str) -> dict:
    """Open a PR for an already-pushed branch. OUTWARD-FACING.

    Runs: gh pr create --repo <remote> --base <base> --head <head>
                        --title <title> --body <body>
    Returns {url, number}.
    """
    reject_blocked(remote, head, base)
    try:
        r = _run(
            ["gh", "pr", "create", "--repo", remote,
             "--base", base, "--head", head,
             "--title", title, "--body", body],
            cwd=dir, timeout=120,
        )
    except subprocess.CalledProcessError as e:
        err = ((e.stderr or "") + (e.stdout or "")).strip()[:300]
        raise ValueError(f"gh pr create failed: {err}")
    url = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
    number = None
    m = re.search(r"/pull/(\d+)", url)
    if m:
        number = int(m.group(1))
    return {"url": url, "number": number}


def merge_pr(dir: str, head: str, base: str) -> dict:
    """Squash-merge the PR for <head>, delete the remote branch, and fast-forward
    the local base branch. OUTWARD-FACING.

    Runs: gh pr merge <head> --squash --delete-branch
    then:  git fetch origin && git checkout <base> && git merge --ff-only origin/<base>
    Returns {ok, base, head}.
    """
    reject_blocked(head, base)
    try:
        _run(["gh", "pr", "merge", head, "--squash", "--delete-branch"],
             cwd=dir, timeout=120)
    except subprocess.CalledProcessError as e:
        err = ((e.stderr or "") + (e.stdout or "")).strip()[:300]
        raise ValueError(f"gh pr merge failed: {err}")
    # Bring the local base up to the merged remote state.
    _run(["git", "-C", dir, "fetch", "origin"], timeout=120, check=False)
    _run(["git", "-C", dir, "checkout", base], timeout=30, check=False)
    ff = subprocess.run(
        ["git", "-C", dir, "merge", "--ff-only", f"origin/{base}"],
        capture_output=True, text=True, timeout=30,
    )
    return {
        "ok": True,
        "base": base,
        "head": head,
        "local_ff": ff.returncode == 0,
    }


def enable_rerere(dir: str) -> None:
    """Turn on git rerere in the repo at `dir` so recurring conflict resolutions
    replay automatically. Best-effort: a failure here never blocks connect.

    Runs: git -C <dir> config rerere.enabled true
    """
    subprocess.run(
        ["git", "-C", dir, "config", "rerere.enabled", "true"],
        capture_output=True, text=True, timeout=15,
    )


def rebase_onto(dir: str, base: str) -> dict:
    """Fetch `base` from origin and rebase the current branch onto it.

    OUTWARD-FACING (git fetch touches the network). On a conflict the rebase is
    aborted so the worktree is left clean, and {ok: False, conflict: True} is
    returned so the caller can park the task rather than loop.

    Runs: git -C <dir> fetch origin <base>
    then:  git -C <dir> rebase origin/<base>
    Returns {ok, conflict, detail}.
    """
    reject_blocked(base)
    fetch = subprocess.run(
        ["git", "-C", dir, "fetch", "origin", base],
        capture_output=True, text=True, timeout=180,
    )
    if fetch.returncode != 0:
        detail = ((fetch.stderr or "") + (fetch.stdout or "")).strip()[:300]
        return {"ok": False, "conflict": False, "detail": f"fetch failed: {detail}"}

    rebase = subprocess.run(
        ["git", "-C", dir, "rebase", f"origin/{base}"],
        capture_output=True, text=True, timeout=180,
    )
    if rebase.returncode == 0:
        return {"ok": True, "conflict": False, "detail": ""}

    detail = ((rebase.stderr or "") + (rebase.stdout or "")).strip()[:300]
    # A stopped rebase leaves the worktree mid-conflict. Abort so it is clean
    # again, then report the conflict for the caller to park on.
    subprocess.run(
        ["git", "-C", dir, "rebase", "--abort"],
        capture_output=True, text=True, timeout=30,
    )
    return {"ok": False, "conflict": True, "detail": detail or "rebase conflict"}


def list_open_prs(remote: str) -> list[dict]:
    """Open PRs for a remote via `gh pr list --json`. Empty list on any error.

    Runs: gh pr list --repo <remote>
          --json number,title,headRefName,url,state,isDraft
    Returns a list of PR dicts (empty on error so a missing/unreachable remote
    degrades to "no PRs" rather than raising).
    """
    r = subprocess.run(
        ["gh", "pr", "list", "--repo", remote,
         "--json", "number,title,headRefName,url,state,isDraft"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return []
    try:
        data = json.loads(r.stdout or "[]")
    except (json.JSONDecodeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def ahead_behind(dir: str, base: str) -> dict:
    """Commits HEAD is ahead/behind `origin/<base>`. {ahead: 0, behind: 0} on error.

    Runs: git -C <dir> rev-list --left-right --count origin/<base>...HEAD
    The left count (origin/base only) is `behind`; the right count (HEAD only)
    is `ahead`.
    """
    r = subprocess.run(
        ["git", "-C", dir, "rev-list", "--left-right", "--count",
         f"origin/{base}...HEAD"],
        capture_output=True, text=True, timeout=30,
    )
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


def is_merged(dir: str, branch: str, base: str) -> bool:
    """True when `branch` is an ancestor of `origin/<base>` (i.e. already merged).

    Runs: git -C <dir> merge-base --is-ancestor <branch> origin/<base>
    Returns False on any non-zero exit (not merged, or the ref is missing).
    """
    r = subprocess.run(
        ["git", "-C", dir, "merge-base", "--is-ancestor", branch, f"origin/{base}"],
        capture_output=True, text=True, timeout=30,
    )
    return r.returncode == 0
