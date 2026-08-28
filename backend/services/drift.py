"""What is running versus what is on disk, and what is holding the gap open.

THE FACT THIS MODULE EXISTS TO SURFACE. A build publishes the WORKING TREE, so
on a shared checkout `scripts/apply.sh` refuses to build while somebody's UI
edits are uncommitted -- otherwise a backend-only change would drag a stranger's
half-written screen onto the origin the operator is looking at. That refusal is
correct. What was missing is that it was only ever printed into one agent's
terminal: nothing in the product said it, so uncommitted work could hold the
bundle stale for hours while every participant believed they had shipped.

So this is a READ of the same predicates `scripts/apply.sh --check` evaluates,
in a shape a UI can render. It changes nothing and applies nothing: the fix is
still to commit the files, or to run the apply step.

ONE DEFINITION OF "WHICH SOURCE IS THIS", AND IT IS NOT HERE. The source ids
come from `scripts/ui_source_id.sh` and `scripts/backend_source_id.sh` by
EXECUTING them, exactly as apply.sh does, rather than by reimplementing what
counts as an input. Those scripts open with the reason ("two implementations
would drift the moment one of them learned about a new input, and drift between
the checker and the thing it checks is worse than no checker at all"), and a
second opinion computed in Python would be precisely that.

WHAT IS DUPLICATED, AND THE GUARD ON IT. One thing cannot be borrowed: the
PATHSPECS that decide which uncommitted files hold a surface back. They live
inside apply.sh as two `git status --porcelain` invocations. They are repeated
below as `UI_PATHSPEC` and `BACKEND_PATHSPEC`, and
`tests/test_drift.py::test_the_pathspecs_still_match_apply_sh` reads apply.sh
and fails if they stop agreeing, so the copy cannot rot silently.

NOT A DEPLOYMENT MODEL. This answers "is the thing in front of me current", for
one installation, from its own disk. It does not know about environments,
releases or hosts, and it must not grow to: `docs/applying-changes.md` is the
apply contract and this is a window onto it.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

__all__ = [
    "UI_PATHSPEC", "BACKEND_PATHSPEC",
    "repo_root", "source_id", "dist_id", "blocking_files", "surfaces", "report",
]

# The pathspecs apply.sh passes to `git status --porcelain`. Kept as data so the
# test that pins them against apply.sh can read them.
#
# `frontend/index.html` is in the UI set because it is the SPA shell and
# `ui_source_id.sh` hashes it; `packages/ui` is in it because the kit compiles
# INTO the bundle rather than being resolved as a built dependency.
UI_PATHSPEC = ("frontend/src", "packages/ui", "frontend/index.html")

# `backend` narrowed to what uvicorn imports. `backend/tests/` is deliberately
# absent for the reason backend_source_id.sh gives at length: a test file cannot
# change what the server serves, so it must not be able to hold a restart.
BACKEND_PATHSPEC = ("backend",)

_TIMEOUT = 20


def repo_root() -> Path:
    """The checkout this installation runs from."""
    env = os.environ.get("WORKSHOP_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def _run(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                           timeout=_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return p.returncode, (p.stdout or "").strip()


def source_id(which: str, *, root: Path | None = None) -> str | None:
    """Run the one definition of "which version of this source is this".

    None when the script is missing or fails, which the caller must report as
    UNDECIDABLE rather than as in-sync -- apply.sh makes the same distinction
    ("Undecidable is NOT the same as in-sync and must never be reported as it").
    """
    root = root or repo_root()
    script = root / "scripts" / f"{which}_source_id.sh"
    if not script.exists():
        return None
    code, out = _run(["bash", str(script)], root)
    return out or None if code == 0 else None


def dist_id(*, root: Path | None = None) -> str | None:
    """The source id the built bundle was stamped with, or None if never built."""
    root = root or repo_root()
    try:
        data = json.loads((root / "frontend" / "dist" / "build-info.json").read_text())
    except (OSError, ValueError):
        return None
    return data.get("source_id") or None


def blocking_files(pathspec: tuple[str, ...], *, root: Path | None = None,
                   exclude_tests: bool = False) -> list[str]:
    """Uncommitted files under `pathspec`, as `git status --porcelain` sees them.

    UNTRACKED FILES COUNT. A brand-new component nobody has committed changes
    what the bundle would contain just as much as an edit does, and
    `ui_source_id.sh` hashes it for the same reason.
    """
    root = root or repo_root()
    code, out = _run(
        ["git", "status", "--porcelain", "-uall", "--", *pathspec], root)
    if code != 0:
        return []
    paths: list[str] = []
    for line in out.splitlines():
        # Porcelain v1: two status columns, a space, then the path. A rename
        # reads `R  old -> new`; the destination is the file that is dirty.
        path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip('"')
        if not path:
            continue
        if exclude_tests and path.startswith("backend/tests/"):
            continue
        if not (path.endswith(".py") or path.endswith(".txt")) and exclude_tests:
            # The backend guard only counts what uvicorn imports.
            continue
        if exclude_tests and path.endswith("workshop_public_app.py"):
            continue
        paths.append(path)
    return sorted(set(paths))


def _surface(name: str, *, running: str | None, source: str | None,
             pathspec: tuple[str, ...], root: Path,
             exclude_tests: bool = False) -> dict:
    """One comparison, plus the uncommitted work that would hold its fix back.

    `state` is one of:
      `current`      running matches source; nothing owed
      `stale`        they differ, and the tree is clean, so the apply step fixes it
      `blocked`      they differ AND uncommitted work under this surface means the
                     apply step will REFUSE to act. This is the one that cost
                     nine hours: a stale origin nobody could clear by rerunning.
      `undecidable`  a source id could not be computed. Never reported as current.
    """
    if source is None or running is None:
        return {"name": name, "state": "undecidable", "running": running,
                "source": source, "blocking_files": [], "detail":
                "could not compute a source id for this surface, so whether it "
                "is current is unknown"}
    if running == source:
        return {"name": name, "state": "current", "running": running,
                "source": source, "blocking_files": [], "detail": ""}
    files = blocking_files(pathspec, root=root, exclude_tests=exclude_tests)
    if files:
        return {"name": name, "state": "blocked", "running": running,
                "source": source, "blocking_files": files, "detail":
                "the apply step will not publish this surface while these files "
                "are uncommitted, because a build carries the working tree and "
                "would put unfinished work in front of whoever is looking"}
    return {"name": name, "state": "stale", "running": running,
            "source": source, "blocking_files": [], "detail":
            "the source moved and the running copy has not caught up; the apply "
            "step fixes this"}


def surfaces(*, running_backend_id: str | None = None,
             root: Path | None = None) -> list[dict]:
    """The two surfaces that can serve something other than what is on disk.

    The UI bundle is compared against the BUILT stamp rather than a live
    process, because a bundle is a file the edge serves and has no runtime to
    ask. The backend is compared against the id the running process reported at
    startup, which the caller passes in -- this module must not make an HTTP
    call to its own server to find out what it is.
    """
    root = root or repo_root()
    return [
        _surface("ui bundle", running=dist_id(root=root),
                 source=source_id("ui", root=root), pathspec=UI_PATHSPEC,
                 root=root),
        _surface("backend", running=running_backend_id,
                 source=source_id("backend", root=root),
                 pathspec=BACKEND_PATHSPEC, root=root, exclude_tests=True),
    ]


def report(*, running_backend_id: str | None = None,
           root: Path | None = None) -> dict:
    """Everything a client needs to say what is stale and who is holding it."""
    root = root or repo_root()
    rows = surfaces(running_backend_id=running_backend_id, root=root)
    blocked = [s for s in rows if s["state"] == "blocked"]
    stale = [s for s in rows if s["state"] == "stale"]
    # A LINKED WORKTREE CANNOT PUBLISH, and saying so is the difference between
    # a build that reaches nobody and one the operator can see. apply.sh warns
    # about this in the same words; the edge serves the canonical tree's dist by
    # absolute path, so a UI build from a linked worktree is invisible until the
    # branch merges.
    _, git_dir = _run(["git", "rev-parse", "--git-dir"], root)
    _, common = _run(["git", "rev-parse", "--git-common-dir"], root)
    canonical = bool(git_dir) and git_dir == common
    return {
        "surfaces": rows,
        "blocked": bool(blocked),
        "stale": bool(stale) or bool(blocked),
        "canonical": canonical,
        "apply_command": "bash scripts/apply.sh",
        "detail": (
            "Uncommitted work is holding a surface stale. Committing it is what "
            "publishes it."
            if blocked else
            "A surface moved and has not been applied yet."
            if stale else
            "Everything on disk is what is running."),
    }
