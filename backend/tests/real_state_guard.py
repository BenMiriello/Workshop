"""Refuse, then detect, any test write to this installation's real state.

`instance_isolation` points the session at a temp runtime; this module makes a
path it missed fail instead of writing.

  REFUSE. An audit hook sees every write this process makes (open for writing,
  rename, remove, mkdir, utime, truncate, chmod, sqlite connect, rmtree,
  copyfile). A write into the real `.runtime/` or the real config dir is refused
  before it happens. It raises pytest's `Failed`, a `BaseException`, so the
  `except Exception` blocks that keep bookkeeping from failing a request cannot
  swallow it, and it is recorded so the session fails even if one swallows that.

  COMPARE. The audit hook cannot see a subprocess, so `.runtime/` is
  snapshotted (path, mtime, size) at session start and compared at the end; a
  file created, modified or deleted there fails the run.

EXCLUSIONS apply to the comparison only; the audit hook has none. The backend
serving this installation keeps running during the suite, and so do other
agents' turns, and both write `.runtime/` on their own schedule. Every pattern
in `LIVE_WRITES` was seen changing while no test process ran (2026-10-07) and
names its writer. A test writing one of them in-process is still refused; only
a test SUBPROCESS writing one goes unseen.
"""
from __future__ import annotations

import fnmatch
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REAL_RUNTIME = REPO / ".runtime"


def _both(p: Path) -> set[str]:
    return {os.path.abspath(p), os.path.realpath(p)}


def _config_dir() -> Path:
    env = os.environ.get("WORKSHOP_CONFIG_DIR")
    return Path(env).expanduser() if env else Path.home() / ".config" / "workshop"


# Read at import, before `instance_isolation` moves WORKSHOP_RUNTIME, so an
# inherited runtime path is protected too.
_runtime_roots = _both(REAL_RUNTIME)
if os.environ.get("WORKSHOP_RUNTIME"):
    _runtime_roots |= _both(Path(os.environ["WORKSHOP_RUNTIME"]).expanduser())
RUNTIME_ROOTS = tuple(sorted(_runtime_roots))
PROTECTED = tuple(sorted(_runtime_roots | _both(_config_dir())))

violations: list[str] = []


def root_of(path: str, roots: tuple[str, ...]) -> str | None:
    for root in roots:
        if path == root or path.startswith(root + os.sep):
            return root
    return None


LIVE_WRITES = (
    "auth.db", "auth.db-wal", "auth.db-shm",  # backend: session touch per request
    "users/*/chat/*",        # backend: the operator's live chats and drafts
    "users/*/semantic/*",    # backend: search index rebuilt behind a live search
    "trigger-state.json",    # backend: trigger loop
    "heartbeats/*",          # backend loops: liveness stamps
    "dispatch.log",          # backend: work dispatcher
    "circuit-breaker.json",  # backend: upstream circuit breaker
    "app-deploys.json", "app-deploys.boot", "ports.json",  # backend: app monitor
    "app-logs/*",            # running apps' own logs
    "apps.json", "installed-apps.json", "projects.json",  # backend: startup reconcile
    "deploy-logs/*", "restart-armed.pid*",  # agents' deferred restarts
    "apply-last.json", "applied-edge.sha",      # agents' apply.sh
    "turns/*",               # backend: live chat turn journals
    "usage.jsonl",           # backend: a cost row for every finished chat turn
    "background/*",          # other agents' detached runs
    "done-gate.log", "done-gate/*", "done-evidence.json",  # agents' done.sh
    "gpu-claims.json",       # backend and apps: GPU claims renewed every few seconds
    "speech/*",              # backend: speech worker logs
)

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_ONE_PATH = frozenset({
    "os.remove", "os.rmdir", "os.mkdir", "os.utime", "os.truncate",
    "os.chmod", "os.chown", "shutil.rmtree", "sqlite3.connect"})
_TWO_PATH = frozenset({"os.rename", "os.link", "os.symlink", "shutil.copyfile"})


# Events whose last argument(s) are directory descriptors a relative path is
# resolved against (`shutil.rmtree` walks with them, and so does pytest's own
# temp-dir cleanup). Resolving such a path against the working directory
# instead names the wrong file, and a temp `.runtime` read as the real one.
_DIR_FD_LAST = frozenset({"os.remove", "os.rmdir", "os.mkdir", "os.utime", "os.chmod",
                          "os.chown", "os.symlink"})


def _dir_fd(event: str, args: tuple, index: int):
    if event in ("os.rename", "os.link"):
        fd = args[2 + index] if len(args) > 2 + index else None
    elif event in _DIR_FD_LAST:
        fd = args[-1]
    else:
        return None
    return fd if isinstance(fd, int) and fd >= 0 else None


def _written_paths(event: str, args: tuple) -> list:
    """(path, dir_fd) for each path the event writes."""
    if event == "open":
        path, mode, flags = args
        if isinstance(mode, str):
            writing = any(c in mode for c in "wax+")
        else:
            writing = bool((flags or 0) & _WRITE_FLAGS)
        return [(path, None)] if writing else []
    if event == "sqlite3.connect":
        db = args[0]
        if isinstance(db, str) and db.startswith("file:") and "mode=ro" in db:
            return []
        return [(db, None)]
    if event in _ONE_PATH:
        return [(args[0], _dir_fd(event, args, 0))]
    if event in _TWO_PATH:
        if event == "os.symlink":
            return [(args[1], _dir_fd(event, args, 0))]
        return [(args[0], _dir_fd(event, args, 0)), (args[1], _dir_fd(event, args, 1))]
    return []


def _resolve(raw, dir_fd) -> str:
    path = os.fsdecode(raw)
    if dir_fd is not None and not os.path.isabs(path):
        try:
            return os.path.join(os.readlink(f"/proc/self/fd/{dir_fd}"), path)
        except OSError:
            pass
    return os.path.abspath(path)


def _audit(event: str, args: tuple) -> None:
    if event != "open" and event not in _ONE_PATH and event not in _TWO_PATH:
        return
    for raw, dir_fd in _written_paths(event, args):
        if isinstance(raw, int) or raw is None:
            continue
        try:
            path = _resolve(raw, dir_fd)
        except (TypeError, ValueError):
            continue
        if path.startswith("file:"):
            path = os.path.abspath(path[5:].split("?", 1)[0])
        if root_of(path, PROTECTED) is None:
            continue
        where = os.environ.get("PYTEST_CURRENT_TEST", "outside a test")
        msg = f"{path} by {event} in {where}"
        violations.append(msg)
        pytest.fail(
            f"the test session tried to write real installation state: {msg}. "
            f"Tests write under {_temp[0]}; see tests/instance_isolation.py.",
            pytrace=True)


def _snapshot() -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for dirpath, _dirs, files in os.walk(REAL_RUNTIME, onerror=lambda e: None):
        for name in files:
            path = os.path.join(dirpath, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            out[os.path.relpath(path, REAL_RUNTIME)] = (st.st_mtime_ns, st.st_size)
    return out


def _live(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, pat) for pat in LIVE_WRITES)


def changed_since(before: dict[str, tuple[int, int]]) -> list[str]:
    after = _snapshot()
    out = []
    for rel in sorted(set(before) | set(after)):
        if _live(rel):
            continue
        if rel not in before:
            out.append(f"created {rel}")
        elif rel not in after:
            out.append(f"deleted {rel}")
        elif before[rel] != after[rel]:
            out.append(f"modified {rel}")
    return out


_before: dict[str, tuple[int, int]] = {}
_temp: list[Path] = []


def start(temp_runtime: Path) -> None:
    """Snapshot the real runtime before anything can write to it."""
    _temp.append(temp_runtime)
    _before.update(_snapshot())


def arm() -> None:
    sys.addaudithook(_audit)


def report(session) -> None:
    """Fail the session on any write that reached the real state.

    Each finding is one `ISOLATION <what> <path>` line, which
    `scripts/check_backend_tests.py` counts as a failure.
    """
    findings = [f"refused {v}" for v in dict.fromkeys(violations)]
    findings += changed_since(_before)
    for temp in _temp:
        shutil.rmtree(temp.parent, ignore_errors=True)
    if not findings:
        return
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    if tr is not None:
        tr.ensure_newline()
        tr.section("instance isolation: real state was written", sep="=",
                   red=True, bold=True)
        for line in findings:
            tr.write_line(f"ISOLATION {line}")
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
