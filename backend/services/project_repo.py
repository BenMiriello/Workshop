"""An app's GitHub repository: whether Workshop manages it, and keeping a
managed one pushed.

MANAGED OR LINKED. A repository Workshop created for an app is MANAGED by
default: after a chat turn the backend pushes the app's default branch, so the
repo on GitHub follows the work without anyone remembering to push. A repository
the app was imported from, or bound to by hand, is LINKED: recorded and shown,
never pushed on its own. Either way the operator can flip it on the app page
(`set_managed`). The record lives on the project's registry row as `repo`,
written by `routes/projects._repo_field` plus the two keys this module adds:
`managed` and `created_by` (`workshop` or `link`).

ADOPTION. Apps made before this existed have an `origin` remote and no `repo`
record, so the platform did not know about their repositories at all.
`adopt()` records one from the app's own `.git`: a repository on the connected
account is taken as Workshop's own and starts managed, anything else starts
linked. Imports record their repo at import time, so only older apps take
this path.

NEVER FORCE. A push that GitHub refuses because the remote has commits this
app does not is reported as `diverged` and left alone. Resolving it is a
decision, not something a background push may take.

Push status is kept in `.runtime/repo-push.json`, not on the registry row:
it changes after every turn, and every registry write reconciles the app table.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from services import git_service

log = logging.getLogger(__name__)

_GITHUB_REMOTE = re.compile(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?/?$")
_REFUSED = ("rejected", "non-fast-forward", "fetch first")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _projects():
    from routes import projects as projects_routes

    return projects_routes


def _status_path() -> Path:
    from wsplatform import paths

    return paths.runtime("repo-push.json")


def _read_status() -> dict:
    try:
        return json.loads(_status_path().read_text())
    except (OSError, ValueError):
        return {}


def _write_status(slug: str, entry: dict) -> None:
    data = _read_status()
    data[slug] = entry
    p = _status_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


def parse_remote(url: str) -> tuple[str, str] | None:
    """`(owner, name)` for a github.com remote, None for anything else."""
    m = _GITHUB_REMOTE.search((url or "").strip())
    return (m.group(1), m.group(2)) if m else None


def _connected_login() -> str:
    try:
        from services import github_identity

        return github_identity.status().get("login") or ""
    except Exception:
        return ""


def adopt(slug: str) -> dict | None:
    """Record the repository an older app already pushes to. None when the app
    has no GitHub `origin`. Writes the registry row once; later calls read it."""
    P = _projects()
    data = P._load_registry()
    raw = (data.get("projects") or {}).get(slug)
    if not raw or raw.get("repo"):
        return (raw or {}).get("repo")
    d = Path(raw.get("dir") or "")
    r = subprocess.run(["git", "-C", str(d), "remote", "get-url", "origin"],
                       capture_output=True, text=True, timeout=10)
    parsed = parse_remote(r.stdout) if r.returncode == 0 else None
    if not parsed or not d.is_dir():
        return None
    owner, name = parsed
    mine = owner.lower() == _connected_login().lower()
    raw["repo"] = {
        **P._repo_field({"owner": owner, "name": name, "remote_url": r.stdout.strip(),
                         "default_branch": git_service.main_branch(d)}),
        "created_by": "workshop" if mine else "link",
        "managed": mine,
    }
    data["projects"][slug] = raw
    P._save_registry(data)
    return raw["repo"]


def describe(slug: str, *, fetch: bool = True) -> dict:
    """What the app page shows: the repository, whether it is managed, how far
    the app is ahead of or behind GitHub, and how the last push went."""
    P = _projects()
    raw = P._ensure_project(slug)
    repo = raw.get("repo") or adopt(slug)
    out: dict = {"connected": bool(repo), "error": raw.get("repo_error") or ""}
    if not repo:
        return out
    d = Path(raw["dir"])
    branch = repo.get("default_branch") or git_service.main_branch(d)
    if fetch:
        git_service._run(d, ["fetch", "-q", "origin", branch], check=False, timeout=30)
    ab = git_service.ahead_behind(d, f"origin/{branch}", branch)
    owner, name = repo.get("owner") or "", repo.get("name") or ""
    return {
        **out,
        "owner": owner,
        "name": name,
        "web_url": f"https://github.com/{owner}/{name}" if owner and name else "",
        "default_branch": branch,
        "managed": bool(repo.get("managed")),
        "created_by": repo.get("created_by") or "link",
        "ahead": ab["ahead"],
        "behind": ab["behind"],
        "push": _read_status().get(slug),
    }


def set_managed(slug: str, managed: bool) -> dict:
    P = _projects()
    data = P._load_registry()
    raw = (data.get("projects") or {}).get(slug)
    if not raw or not (raw.get("repo") or adopt(slug)):
        raise ValueError("This app has no GitHub repository.")
    data = P._load_registry()
    data["projects"][slug]["repo"]["managed"] = bool(managed)
    P._save_registry(data)
    if managed:
        push(slug)
    return describe(slug, fetch=False)


def push(slug: str) -> dict:
    """Push the app's default branch to GitHub, never forcing. Records and
    returns `{state, at, detail, sha}` where state is ok, diverged or failed."""
    with _locks_guard:
        lock = _locks.setdefault(slug, threading.Lock())
    with lock:
        raw = _projects()._ensure_project(slug)
        repo = raw.get("repo") or {}
        d = Path(raw["dir"])
        branch = repo.get("default_branch") or git_service.main_branch(d)
        sha = git_service.rev_parse(d, branch) or ""
        r = git_service.push(d, "origin", branch)
        detail = r["detail"]
        if r["ok"]:
            state = "ok"
        elif any(k in detail for k in _REFUSED):
            state = "diverged"
        else:
            state = "failed"
        entry = {"state": state, "at": _now(), "sha": sha,
                 "detail": "" if r["ok"] else detail[-300:]}
        _write_status(slug, entry)
        return entry


def _slug_for(cwd: str) -> str | None:
    """The registered app whose repository `cwd` is in, worktrees included."""
    r = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute",
                        "--git-common-dir"], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return None
    root = Path(r.stdout.strip()).parent.resolve()
    for slug, raw in (_projects()._load_registry().get("projects") or {}).items():
        if raw.get("dir") and Path(raw["dir"]).resolve() == root:
            return slug
    return None


def push_after_turn(cwd: str, owner_id: int | None) -> None:
    """Called at the end of a chat turn. Pushes in the background when the
    turn's app is managed and has commits GitHub does not."""
    def run() -> None:
        from services import ownership

        try:
            if owner_id is not None:
                ownership.set_current_owner(owner_id)
            else:
                ownership.pin_operator()
            slug = _slug_for(cwd)
            if not slug:
                return
            info = describe(slug, fetch=False)
            if info.get("connected") and info.get("managed") and info.get("ahead", 0) > 0:
                push(slug)
        except Exception as e:
            log.info("auto-push after turn in %s skipped: %s", cwd, e)

    threading.Thread(target=run, name="repo-push", daemon=True).start()
