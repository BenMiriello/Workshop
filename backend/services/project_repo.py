"""An app's GitHub repository: whether Workshop manages it, and keeping a
managed one pushed.

MANAGED OR LINKED. A repository Workshop created for an app is MANAGED by
default: after a chat turn the backend pushes the app's default branch, so the
repo on GitHub follows the work without anyone remembering to push. A repository
the app was imported from, or bound to by hand, is LINKED: recorded and shown,
never pushed on its own. Either way the operator can flip it on the app page
(`set_managed`). The record is `routes/projects._repo_field` plus the two keys
this module adds: `managed` and `created_by` (`workshop` or `link`).

TWO KINDS OF APP, ONE PATH. A project app keeps its record on its registry row
(`repo`, and `repo_error` after a failed create). An installed app (its own tree
under the apps root) has no row to write, so its record lives in
`.runtime/app-repos.json`. `target()` hides the difference; everything below
works on a `Target`. An installed app whose folder is not its own repository
(one that sits inside another tree) has no repository to offer: `available`
is false and nothing here touches it, because creating from such a folder would
publish the tree it sits in.

ADOPTION. Apps made before this existed have an `origin` remote and no record.
`adopt()` records one from the app's own `.git`: a repository on the connected
account is taken as Workshop's own and starts managed, anything else starts
linked. Imports record their repo at import time, so only older apps take
this path.

NEVER FORCE. A push that GitHub refuses because the remote has commits this
app does not is reported as `diverged` and left alone. Resolving it is a
decision, not something a background push may take.

Push status is kept in `.runtime/repo-push.json`, not on the record: it changes
after every turn, and every registry write reconciles the app table.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from dataclasses import dataclass
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


def _runtime(name: str) -> Path:
    from wsplatform import paths

    return paths.runtime(name)


def _status_path() -> Path:
    return _runtime("repo-push.json")


def _apps_store_path() -> Path:
    return _runtime("app-repos.json")


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def _write_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


def parse_remote(url: str) -> tuple[str, str] | None:
    """`(owner, name)` for a github.com remote, None for anything else."""
    m = _GITHUB_REMOTE.search((url or "").strip())
    return (m.group(1), m.group(2)) if m else None


def repo_record(info: dict, created_by: str) -> dict:
    """A repository binding as stored: managed when Workshop made it."""
    return {**_projects()._repo_field(info), "created_by": created_by,
            "managed": created_by == "workshop"}


def _connected_login() -> str:
    try:
        from services import github_identity

        return github_identity.status().get("login") or ""
    except Exception:
        return ""


def _own_repo(d: Path) -> bool:
    r = subprocess.run(["git", "-C", str(d), "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, timeout=10)
    return r.returncode == 0 and Path(r.stdout.strip()).resolve() == d.resolve()


# ---- where an app's record lives ---------------------------------------------

@dataclass
class Target:
    kind: str  # "project" or "app"
    slug: str
    dir: Path

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.slug}"

    def read(self) -> tuple[dict | None, str]:
        """`(repo, error)`."""
        if self.kind == "project":
            raw = (_projects()._load_registry().get("projects") or {}).get(self.slug) or {}
            return raw.get("repo"), raw.get("repo_error") or ""
        rec = _read_json(_apps_store_path()).get(self.slug) or {}
        return rec.get("repo"), rec.get("error") or ""

    def write(self, repo: dict | None, error: str = "") -> None:
        if self.kind == "project":
            P = _projects()
            data = P._load_registry()
            raw = data["projects"][self.slug]
            if repo is not None:
                raw["repo"] = repo
            if error:
                raw["repo_error"] = error
            else:
                raw.pop("repo_error", None)
            raw["last_touched"] = _now()
            P._save_registry(data)
            return
        data = _read_json(_apps_store_path())
        rec = data.get(self.slug) or {}
        if repo is not None:
            rec["repo"] = repo
        rec["error"] = error
        data[self.slug] = rec
        _write_json(_apps_store_path(), data)


def target(kind: str, slug: str) -> Target:
    """Raises LookupError for an app that does not exist."""
    if kind == "project":
        raw = (_projects()._load_registry().get("projects") or {}).get(slug)
        if not raw or not raw.get("dir"):
            raise LookupError(f"project not found: {slug}")
        return Target("project", slug, Path(raw["dir"]))
    from services import app_ui

    d = app_ui.app_dir(slug)
    if not d.is_dir():
        raise LookupError(f"app not found: {slug}")
    return Target("app", slug, d)


def adopt(t: Target) -> dict | None:
    """Record the repository an older app already pushes to. None when the app
    has no GitHub `origin`. Writes the record once; later calls read it."""
    repo, _ = t.read()
    if repo:
        return repo
    if not _own_repo(t.dir):
        return None
    r = subprocess.run(["git", "-C", str(t.dir), "remote", "get-url", "origin"],
                       capture_output=True, text=True, timeout=10)
    parsed = parse_remote(r.stdout) if r.returncode == 0 else None
    if not parsed:
        return None
    owner, name = parsed
    mine = owner.lower() == _connected_login().lower()
    repo = repo_record({"owner": owner, "name": name, "remote_url": r.stdout.strip(),
                        "default_branch": git_service.main_branch(t.dir)},
                       "workshop" if mine else "link")
    t.write(repo)
    return repo


def describe(t: Target, *, fetch: bool = True) -> dict:
    """What the app page shows: the repository, whether it is managed, how far
    the app is ahead of or behind GitHub, and how the last push went."""
    available = t.kind == "project" or _own_repo(t.dir)
    repo = adopt(t) if available else None
    _, error = t.read()
    out: dict = {"available": available, "connected": bool(repo), "error": error}
    if not repo:
        return out
    branch = repo.get("default_branch") or git_service.main_branch(t.dir)
    if fetch:
        git_service._run(t.dir, ["fetch", "-q", "origin", branch], check=False, timeout=30)
    ab = git_service.ahead_behind(t.dir, f"origin/{branch}", branch)
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
        "push": _read_json(_status_path()).get(t.key),
    }


def set_managed(t: Target, managed: bool) -> dict:
    repo = adopt(t)
    if not repo:
        raise ValueError("This app has no GitHub repository.")
    t.write({**repo, "managed": bool(managed)})
    if managed:
        push(t)
    return describe(t, fetch=False)


def provision(t: Target, *, scratch: bool) -> str:
    """Create the app's GitHub repository and record it, or record why not.
    Returns the error, empty on success.

    A folder that already has an `origin` (a create that made the repo on
    GitHub and then failed to push) is bound rather than created twice.
    """
    from services import github, github_identity

    P = _projects()
    try:
        if t.kind == "app" and not _own_repo(t.dir):
            raise ValueError("This app's folder is not its own repository.")
        existing = subprocess.run(["git", "-C", str(t.dir), "remote", "get-url", "origin"],
                                  capture_output=True, text=True, timeout=15)
        if existing.returncode == 0 and existing.stdout.strip():
            info = github.bind_repo(str(t.dir), existing.stdout.strip())
            subprocess.run(["git", "-C", str(t.dir), "push", "-q", "-u", "origin", "HEAD"],
                           env=git_service.git_env(), capture_output=True, timeout=180)
        else:
            github.reject_blocked(t.slug)
            name = github.unique_repo_name(github.repo_base_name(t.slug, scratch))
            info = github.create_repo(str(t.dir), name,
                                      github_identity.defaults()["default_visibility"])
        if t.kind == "project":
            P._assert_project_ignored(t.slug)
    except Exception as e:
        detail = getattr(e, "detail", None) or P._github_http_error(e).detail
        t.write(None, str(detail))
        return str(detail)
    t.write(repo_record(info, "workshop"))
    return ""


def link(t: Target, remote_url: str) -> None:
    """Bind the app to a repository that already exists. Linked, not managed."""
    from services import github

    if t.kind == "app" and not _own_repo(t.dir):
        raise ValueError("This app's folder is not its own repository.")
    github.reject_blocked(remote_url)
    info = github.bind_repo(str(t.dir), remote_url.strip())
    t.write(repo_record(info, "link"))


def push(t: Target) -> dict:
    """Push the app's default branch to GitHub, never forcing. Records and
    returns `{state, at, detail, sha}` where state is ok, diverged or failed."""
    with _locks_guard:
        lock = _locks.setdefault(t.key, threading.Lock())
    with lock:
        repo, _ = t.read()
        branch = (repo or {}).get("default_branch") or git_service.main_branch(t.dir)
        sha = git_service.rev_parse(t.dir, branch) or ""
        r = git_service.push(t.dir, "origin", branch)
        detail = r["detail"]
        if r["ok"]:
            state = "ok"
        elif any(k in detail for k in _REFUSED):
            state = "diverged"
        else:
            state = "failed"
        entry = {"state": state, "at": _now(), "sha": sha,
                 "detail": "" if r["ok"] else detail[-300:]}
        data = _read_json(_status_path())
        data[t.key] = entry
        _write_json(_status_path(), data)
        return entry


# ---- after a chat turn ------------------------------------------------------

def _all_targets() -> list[Target]:
    out = [Target("project", s, Path(r["dir"]))
           for s, r in (_projects()._load_registry().get("projects") or {}).items()
           if r.get("dir")]
    try:
        from services import app_ui

        installed = json.loads(_runtime("installed-apps.json").read_text()).get("installed") or []
        out += [Target("app", s, app_ui.app_dir(s)) for s in installed if isinstance(s, str)]
    except (OSError, ValueError, AttributeError):
        pass
    return out


def target_for(cwd: str) -> Target | None:
    """The app whose repository `cwd` is in, worktrees included."""
    r = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute",
                        "--git-common-dir"], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return None
    root = Path(r.stdout.strip()).parent.resolve()
    for t in _all_targets():
        try:
            if t.dir.resolve() == root:
                return t
        except OSError:
            continue
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
            t = target_for(cwd)
            if not t:
                return
            info = describe(t, fetch=False)
            if info.get("connected") and info.get("managed") and info.get("ahead", 0) > 0:
                push(t)
        except Exception as e:
            log.info("auto-push after turn in %s skipped: %s", cwd, e)

    threading.Thread(target=run, name="repo-push", daemon=True).start()
