"""GitHub helpers for project repos (Phase G1 of the worktree-orchestration plan).

Thin, testable wrappers over `gh` and `git` for the connect/publish/merge flow.
No FastAPI here so the pure logic (naming, uniqueness, veritus rejection) can be
unit-tested without a server.

Hard policy baked into the code, not left as a flippable default:
  - Owner is ALWAYS the operator's personal account, `BenMiriello`.
  - Platform-created repos are ALWAYS `--private`. There is no public path.
  - Anything whose owner/name/remote contains `veritus` (case-insensitive) is
    rejected. Veritus is the operator's work org and is strictly out of scope
    here: never a host, never mixed in.

Public surface:
  reject_veritus(*strings)
  repo_base_name(slug, scratch) -> str
  unique_repo_name(base) -> str
  create_repo(dir, name) -> dict
  bind_repo(dir, remote_url) -> dict
  open_pr(dir, remote, base, head, title, body) -> dict
  merge_pr(dir, head, base) -> dict
"""
from __future__ import annotations

import re
import subprocess

OWNER = "BenMiriello"


def reject_veritus(*strings: str | None) -> None:
    """Raise ValueError if any argument contains `veritus` (case-insensitive).

    Veritus is out of scope for the personal clawd-house system: no owner, name,
    or remote URL may reference it.
    """
    for s in strings:
        if s and "veritus" in s.lower():
            raise ValueError("veritus is out of scope: refusing owner/name/remote containing 'veritus'")


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
    reject_veritus(base)
    if not _repo_exists(OWNER, base):
        return base
    n = 2
    while True:
        candidate = f"{base}-{n}"
        if not _repo_exists(OWNER, candidate):
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
      gh repo create BenMiriello/<name> --private --source=<dir> --remote=origin --push

    The `--private` flag is mandatory and unconditional; there is no code path
    that creates a public repo. Returns {owner, name, remote_url, default_branch}.
    """
    reject_veritus(name, dir)
    full = f"{OWNER}/{name}"
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
        "owner": OWNER,
        "name": name,
        "remote_url": f"https://github.com/{OWNER}/{name}.git",
        "default_branch": _default_branch(OWNER, name),
    }


_OWNER_RE = re.compile(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?/?$")


def _parse_remote(remote_url: str) -> tuple[str, str]:
    m = _OWNER_RE.search(remote_url.strip())
    if not m:
        raise ValueError(f"unrecognized GitHub remote URL: {remote_url}")
    return m.group(1), m.group(2)


def bind_repo(dir: str, remote_url: str) -> dict:
    """Bind an existing local repo to an existing GitHub remote (no create).

    OUTWARD-FACING (git fetch touches the network). Validates the owner is the
    operator's personal account, rejects veritus, wires `origin`, fetches, and
    resolves the default branch. Returns {owner, name, remote_url, default_branch}.
    """
    reject_veritus(remote_url)
    owner, name = _parse_remote(remote_url)
    reject_veritus(owner, name)
    if owner != OWNER:
        raise ValueError(f"owner must be {OWNER}; got {owner}")

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
    reject_veritus(remote, head, base)
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
    reject_veritus(head, base)
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
