"""Developer mode (ADR 0014): is the platform repo permitted as a run target?

MOVED FROM `services/app_runner.py` (target-architecture-2026-08-17.md §2.2,
ADR 0024). `app_runner.py` is a 1,426-line multi-service process supervisor;
10 modules imported it, and 8 of them wanted nothing from it but this ~40-line
boolean and its two guards. Confirmed by reading every call site before moving
anything: `app_runner.py`'s OWN business logic (`start_app`, `stop_app`,
`restart_service`, `app_status`, `manifest_dict`, `env_overview`) never once
called `dev_mode_on`, `is_self_dir`, `is_in_self_repo`, `self_dir_refusal` or
`assert_run_target` -- it only defined them for its importers to re-export.
That is the whole misplacement in one sentence: a repo-write safety interlock
(ADR 0014) living inside an app-process supervisor it was never wired into.

WHAT DID NOT MOVE, AND WHY. `is_self_project` (registry `self`-flag based)
STAYS in `app_runner.py`: it is genuinely app_runner's own business logic --
refusing to let the App-runner UI manage the running platform as if it were a
deployable app (`start_app`/`stop_app`/`restart_service`/`app_status`/
`manifest_dict`/`env_overview` all guard on it, 6 internal call sites) -- not a
chokepoint any external caller reaches for the write-safety question this
module answers. `WORKSHOP_ROOT` is computed independently here and in
`app_runner.py` (each a direct call to `paths.workshop_root()`, the one real
source of the value), the same pattern `routes/autonomy.py` already uses
rather than a shared module-level constant -- an import across the two would
go stale under `importlib.reload()` of only one of them (see
`tests/test_paths_are_portable.py`), and it is one function call, not a
computation worth deduplicating at the cost of that.

WHY THIS IS `wsplatform/`, NOT `platform/`. See `wsplatform/__init__.py` and
`wsplatform/http.py`'s header: `backend/` is on `sys.path` for every way this
app actually runs, so a top-level package literally named `platform` shadows
the stdlib `platform` module for the whole process (verified: pytest fails
before collecting a single test). This module lives beside `wsplatform/http.py`
under the same name for the same reason, not a new decision.

THE FASTAPI QUESTION -- resolved, not carried across. `assert_run_target` used
to raise `fastapi.HTTPException` directly, imported *inside the function*
specifically so this module did not need FastAPI at import time; its own
comment flagged that as foreign ("services must not need FastAPI to import").
Under ADR 0024, `platform` (`wsplatform`) sits below `api` and `domains` and
must not import the web framework at all -- a lower layer knowing the shape of
a higher one is the same defect the local import was already confessing to,
just not yet fixed. So: this module raises `DevModeRefusal`, a plain exception
carrying `.status` and `.message`, the same shape `app_runner.AppError`
already uses for exactly this reason (a caller-facing error the ROUTE maps to
HTTP). `wsplatform` now has zero dependency on FastAPI, full stop.

Three call sites translate `DevModeRefusal` to `HTTPException` today, and each
does it AT THE SITE, mirroring how `routes/base_apps.py` already catches
`app_runner.AppError` and re-raises `HTTPException(e.status, e.message)`:
`routes/tasks_all.py` and `routes/projects.py` (route handlers, `HTTPException`
already imported at module scope) and `services/build_streaming.py` (a service
module with no prior FastAPI dependency at all -- its translation uses a
function-local `from fastapi import HTTPException`, the exact convention
`app_runner.py` used to use for this same exception. The dependency has not
been deleted, it has moved from a module nothing should need FastAPI to import
to the one place actually producing an HTTP response). No global exception
handler was registered for `DevModeRefusal` because `main.py` is a sibling
agent's file this build must not touch; per-site translation is the option
available under that constraint, and it is also the pattern already proven by
`AppError`. The refusal a caller ultimately sees -- status code and message --
is byte-for-byte unchanged; `tests/test_dev_mode_gate.py` is what proves it.

UPDATE, same day: the transitional dependency named above is gone. This module's
`from services import paths` was exactly the `wsplatform.dev_mode ->
services.paths` upward edge `check_module_layout.py`'s new `wsplatform` layer
entry surfaced as a ratchet failure (42 violations against a baseline of 41).
The fix was the move the paragraph above already called for: `paths.py` is now
`wsplatform/paths.py`, so this import is `from wsplatform import paths`, a
same-layer edge, not an upward one.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from wsplatform import paths

WORKSHOP_ROOT = paths.workshop_root()


def is_self_dir(path: str | Path) -> bool:
    """True if a working dir resolves to the Workshop monorepo root (the self
    project). Since ADR 0014 this is the developer-mode predicate: the one thing
    every route to the platform repo has in common.

    For "would writing here touch the platform repo", use `is_in_self_repo`,
    which is the containment question rather than the identity one.

    IDENTITY IS THE TREE, NOT THE STRING. This compared resolved paths only,
    and `resolve()` does not see through a bind mount: on any box where the
    checkout is reachable under a second path (a container, a bind mount, a
    mount --rbind of a home directory), the same directory has two names, this
    returned False for one of them, and the developer-mode interlock was
    silently open on that path. Measured 2026-08-30 in an installation's own
    sandbox, where the repo was reachable both at the sandbox's mount point and
    under the host's home directory: `project:development` resolved to the
    platform repo, which ADR 0014 sec 4 exists to forbid. (The two paths are
    deliberately not written here -- they name one host, and
    check_no_hardcoding.sh counts that.)

    So the string comparison stays as the fast path, and a stat comparison
    (device + inode) is the fallback: two names for one directory are one
    directory, whatever they are spelled. A missing path simply is not the
    root."""
    try:
        candidate = Path(path).resolve()
    except OSError:
        return False
    if candidate == WORKSHOP_ROOT:
        return True
    try:
        a, b = candidate.stat(), WORKSHOP_ROOT.stat()
    except OSError:
        return False
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def is_in_self_repo(path: str | Path) -> bool:
    """True if `path` lies INSIDE the platform's git repo, at any depth.

    `is_self_dir` asks whether a path IS the root, which is the wrong question
    for anything about committing. `backend/`, `shared/` and `branches/` all
    resolve to the platform repo, and `git add -A` is repo-wide no matter which
    subdirectory it runs from -- so a turn working in any of them passed the
    exact-equality check and then auto-committed the entire monorepo, including
    whatever every other agent had open at that moment. Observed 2026-08-13 in
    commit cf3eabd.

    A NESTED REPO IS ITS OWN TREE and is deliberately not protected here:
    `projects/*` always carried their own `.git`, and `apps/` acquired one on
    2026-09-04 when the operator's apps left this repo. Committing there commits
    that repo and cannot reach the platform's index, which is the whole thing
    this predicate is defending. This paragraph used to list `apps/` among the
    paths that resolve to the platform repo; that was true until it wasn't, and
    the rule below never needed editing because it asks git rather than a list.

    Resolved through git rather than by string prefix so a symlinked or bind-
    mounted path cannot sneak past, and so a nested repo is correctly treated as
    its own tree.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return False
        return Path(r.stdout.strip()).resolve() == WORKSHOP_ROOT
    except Exception:
        # Undecidable means "do not auto-commit": the failure this guards is
        # committing other people's work, which is not undoable by waiting.
        return True


# ---- Developer mode (ADR 0014) ----------------------------------------------
#
# Developer mode is ONE question: is the platform repo permitted as a run target?
#
# The gate is the resolved working directory, not the scope name, because the
# platform repo is reachable under at least five names through four subsystems
# (docs/platform-redesign/dev-mode-and-development-scope-2026-08-09.md §2).
# Gating a name closes one door and leaves eight open; gating the directory
# closes all of them at once, with a predicate that already existed.
#
# THIS IS A PRODUCT CAPABILITY BOUNDARY AND A SAFETY INTERLOCK, NOT A SECURITY
# BOUNDARY. Anyone with a shell on this box can edit the source directly; real
# confinement is a separate execution identity ([SEC-A1], still open). What it
# buys is that a normal build cannot be used to modify the app it is running,
# that the always-on loops cannot silently rewrite the platform, and that "edit
# the Workshop" is a named visible mode instead of an emergent property of a
# default value. Do not describe it as confinement, here or in the UI.
#
# One flag per side, read in one place each: this function is the backend's,
# `dev_mode_on` in scripts/dev_mode_gate.sh is the shell's, and `DEV_MODE` in
# frontend/src/lib/devMode.ts is the build-time frontend's. All three apply the
# identical rule so there is one answer, not three.

DEV_MODE_ENV = "WORKSHOP_DEV_MODE"


def dev_mode_on() -> bool:
    """Is developer mode on for this deployment? `WORKSHOP_DEV_MODE=1`, and only
    the exact string "1", so a stray empty / "0" / "false" in the environment can
    never read as on. Read fresh on every call: the flag is deployment config,
    and a process that has to restart to notice it is a process that will be
    reported as broken."""
    return os.environ.get(DEV_MODE_ENV) == "1"


def self_dir_refusal(target: str | Path, who: str) -> str:
    """The message a chokepoint shows when it refuses the platform repo.

    One text, shared by every refusal, so the operator learns one sentence
    instead of four. It names the target, the caller, the flag, and — because
    this is the thing an audit must not have to unwind later — what kind of
    boundary this is."""
    return (
        f"{who} cannot run against {target}: that is the Workshop's own source "
        f"tree, and developer mode is off. Set {DEV_MODE_ENV}=1 for this "
        f"installation to allow it. This is a safety interlock, not a security "
        f"boundary. See ADR 0014."
    )


class DevModeRefusal(Exception):
    """Caller-facing refusal from `assert_run_target`. `.status` / `.message`
    mirror `services.app_runner.AppError`'s shape on purpose: a chokepoint that
    faces the operator over HTTP catches this and raises
    `HTTPException(e.status, e.message)`, exactly as `routes/base_apps.py`
    already does for `AppError`. See this module's docstring for why the
    translation happens at each caller instead of here."""

    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.message = message
        self.status = status


def assert_run_target(target: str | Path | None, who: str) -> None:
    """Refuse the platform repo as a run target unless developer mode is on.

    The Python half of `assert_run_target` in scripts/dev_mode_gate.sh, with the
    same two rules and the same wording. Raises `DevModeRefusal` rather than
    returning a bool, so the refusal carries a specific sentence instead of
    degrading into a confusing 404 further down -- the caller is responsible
    for turning that into the HTTP-shaped refusal the operator sees."""
    if target is None:
        raise DevModeRefusal(f"{who} cannot run: no working tree named (ADR 0014).")
    if is_self_dir(target) and not dev_mode_on():
        raise DevModeRefusal(self_dir_refusal(target, who))
