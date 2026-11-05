"""The test session runs against a throwaway copy of instance state.

THE RULE IS STRUCTURAL, NOT PER-TEST. Opting in one file at a time is how the
suite kept writing the operator's data: `test_inbox_answers_the_task.py` put a
real grant into `.runtime/resource-grants.json` on every run, 149 rows of it on
his Settings page, while `_isolate_usage_ledger` and `_isolate_jobs_log` fenced
two other files by name. `conftest.py` calls `install()` before any platform
module is imported. This module REDIRECTS; `real_state_guard` REFUSES and
COMPARES, so a path this misses is a failure, not a silent write.

  - `WORKSHOP_RUNTIME`, every env override whose default lives in `.runtime/`
    (`_ENV_FILES`), and any inherited env value pointing into it move to a
    session temp dir, which subprocesses inherit.
  - Module constants frozen from the repo root rather than from
    `runtime_dir()` are rewritten generically: any `Path` or `str` attribute of
    a platform module pointing inside the real `.runtime/` moves to the same
    relative path in the temp dir, whatever it is named. An import hook does it
    the moment a module executes (including a reload), and
    `redirect_frozen_paths()` re-checks before every test.
  - Functions that build such a path from the repo root at call time are
    wrapped (`_CALL_TIME_PATHS`), and private roots used only for `.runtime/`
    are pointed at a stand-in root (`_RUNTIME_ONLY_ROOTS`).
  - The temp auth database gets an operator account, because a configured
    instance has one and `ownership.pin_operator()` is how automation finds it.
"""
from __future__ import annotations

import os
import secrets
import sys
import tempfile
from pathlib import Path, PurePath

from tests import real_state_guard

REPO = real_state_guard.REPO

# Env overrides whose default is a file under `.runtime/`. Set explicitly so a
# subprocess gets the temp path too, and so `paths._state_file` never takes its
# legacy fallback into `backend/`.
_ENV_FILES = {
    "WORKSHOP_AUTH_DB": "auth.db",
    "WORKSHOP_INSTALLED_APPS": "installed-apps.json",
    "WORKSHOP_MEMORY_POLICY": "memory-policy.json",
    "WORKSHOP_SCOPE_TREE": "scope-tree.json",
    "WORKSHOP_SCOPE_VERSIONS": "scope-versions.json",
    "WORKSHOP_SUBSCRIPTIONS": "agent-subscriptions.json",
    "WORKSHOP_TOOLS": "tools.json",
    "WORKSHOP_GRANTS_PATH": "grants.json",
    "WORKSHOP_RESOURCE_GRANTS_PATH": "resource-grants.json",
    "WORKSHOP_APPS_MANIFEST": "apps.json",
    "WORKSHOP_STORE_POLICIES_PATH": "store-policies.json",
}

# (module, function) pairs that build a `.runtime/` path from the repo root at
# call time, so no env var or module constant reaches them.
_CALL_TIME_PATHS = (
    ("services.work_driver", "_lock_file"),
    ("services.work_driver", "pid_path"),
    ("services.merge_policy", "_store_path"),
    ("services.scope_execution", "_store_path"),
)

# A stand-in repo root whose `.runtime/` is the temp runtime, for modules that
# keep a private root constant used for nothing but `.runtime/` paths.
TEMP_ROOT = Path(tempfile.mkdtemp(prefix="workshop-tests-"))
TEMP_RUNTIME = TEMP_ROOT / ".runtime"
TEMP_RUNTIME.mkdir()
_RUNTIME_ONLY_ROOTS = (("services.background_runs", "_WORKSHOP_ROOT"),)

_scanned: set[int] = set()
_holders: list = []


def remap(value: str | PurePath) -> str | None:
    """The temp-dir twin of a path inside the real runtime, else None."""
    p = os.path.abspath(os.path.expanduser(os.fspath(value)))
    root = real_state_guard.root_of(p, real_state_guard.RUNTIME_ROOTS)
    if root is None:
        return None
    return str(TEMP_RUNTIME / os.path.relpath(p, root))


def redirect_env() -> None:
    for key, value in list(os.environ.items()):
        moved = remap(value) if os.sep in value else None
        if moved:
            os.environ[key] = moved
    os.environ["WORKSHOP_RUNTIME"] = str(TEMP_RUNTIME)
    for key, name in _ENV_FILES.items():
        os.environ[key] = str(TEMP_RUNTIME / name)


def _platform_module(mod) -> bool:
    f = getattr(mod, "__file__", None) or ""
    return f.startswith(str(REPO)) and "/.venv/" not in f and "/tests/" not in f


def _wrap_call_time(fn):
    def redirected(*args, **kwargs):
        out = fn(*args, **kwargs)
        moved = remap(out)
        return type(out)(moved) if moved else out
    redirected.__wrapped__ = fn
    redirected.__name__ = fn.__name__
    return redirected


def _adopt(mod) -> None:
    """Redirect one fully executed platform module."""
    found = False
    for name, value in list(vars(mod).items()):
        if isinstance(value, (PurePath, str)) and not name.startswith("__"):
            moved = remap(value) if os.sep in os.fspath(value) else None
            if moved:
                setattr(mod, name, type(value)(moved))
                found = True
    for modname, attr in _CALL_TIME_PATHS:
        fn = getattr(mod, attr, None) if mod.__name__ == modname else None
        if fn is not None and not hasattr(fn, "__wrapped__"):
            setattr(mod, attr, _wrap_call_time(fn))
            found = True
    for modname, attr in _RUNTIME_ONLY_ROOTS:
        if mod.__name__ == modname:
            setattr(mod, attr, TEMP_ROOT)
            found = True
    _scanned.add(id(mod))
    if found and mod not in _holders:
        _holders.append(mod)


class _RedirectOnImport:
    """A meta-path finder that redirects a platform module the moment its code
    has run, so a module first imported inside a test body (or reloaded by
    one) never runs a line with its real-state constants in place."""

    def find_spec(self, name, path=None, target=None):
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(name, path, target)
            if spec is not None:
                break
        else:
            return None
        loader = spec.loader
        origin = spec.origin or ""
        if (loader is None or not hasattr(loader, "exec_module")
                or not origin.startswith(str(REPO)) or "/.venv/" in origin
                or "/tests/" in origin):
            return spec
        run = loader.exec_module

        def exec_module(module):
            run(module)
            _adopt(module)

        loader.exec_module = exec_module
        return spec


def redirect_frozen_paths() -> None:
    """Adopt any platform module the import hook did not see, and re-check the
    ones that ever held a real path, since `monkeypatch.undo()` or a direct
    assignment can put one back."""
    for mod in list(sys.modules.values()):
        if mod is None or id(mod) in _scanned or not _platform_module(mod):
            continue
        if getattr(getattr(mod, "__spec__", None), "_initializing", False):
            continue
        _adopt(mod)
    for mod in _holders:
        _adopt(mod)


# Registries copied from the real runtime at session start, READ-ONLY at the
# source. A configured instance has them, and the tests that need one
# (`conftest.requires_registry` / `requires_app`, the development scope that
# resolves through the self project) keep asserting against this instance's
# shape instead of quietly skipping. Writes land in the copies.
_SEED_COPIES = ("projects.json", "installed-apps.json")


def _seed_registries() -> None:
    for name in _SEED_COPIES:
        src = real_state_guard.REAL_RUNTIME / name
        if src.is_file():
            (TEMP_RUNTIME / name).write_bytes(src.read_bytes())


def _seed_operator() -> None:
    from services import auth_store

    if auth_store.operator_id() is None:
        user = auth_store.create_user(
            "operator@example.test", secrets.token_urlsafe(16), "Test operator")
        auth_store.set_operator(user["id"])


def install() -> None:
    """Called once, at conftest import, before any platform module loads."""
    real_state_guard.start(TEMP_RUNTIME)
    redirect_env()
    sys.meta_path.insert(0, _RedirectOnImport())
    _seed_registries()
    _seed_operator()
    real_state_guard.arm()
