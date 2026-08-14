"""Auto-checkpoint commits for build-agent turns.

Part of the doc 05 history/undo substrate: after a builder turn, commit the
working tree in the agent's directory so the branch holds a recoverable timeline
even if the agent later breaks something or leaves work uncommitted. Best-effort:
never raises into the turn path; silently skips non-repos and clean trees. Uses a
neutral committer and --no-verify so project commit hooks (lefthook/commitlint,
e.g. gengui) cannot block a checkpoint.
"""
from __future__ import annotations

import fcntl
import logging
import os
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("workshop.checkpoints")

_ENV = {
    "GIT_AUTHOR_NAME": "Workshop Checkpoints",
    "GIT_AUTHOR_EMAIL": "checkpoints@workshop.local",
    "GIT_COMMITTER_NAME": "Workshop Checkpoints",
    "GIT_COMMITTER_EMAIL": "checkpoints@workshop.local",
}


def _git(args: list[str], cwd: Path, *, env: dict | None = None, timeout: int = 60):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )


def _is_repo(d: Path) -> bool:
    r = _git(["rev-parse", "--is-inside-work-tree"], d, timeout=15)
    return r.returncode == 0 and r.stdout.strip() == "true"


@contextmanager
def _commit_lock(d: Path):
    """One checkpoint at a time per tree.

    Several turns run in this one checkout at once and each ends by staging and
    committing. Two of them doing that concurrently race git's index.lock, and
    the loser's work silently does not get committed -- the exact outcome this
    whole module exists to prevent.
    """
    lock = d / ".git" / "workshop-checkpoint.lock"
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock, "w")
    except Exception:
        yield  # cannot lock (not a normal repo layout); proceed rather than skip
        return
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()
        except Exception:
            pass


def checkpoint(cwd: str, label: str, paths: list[str] | None = None) -> str | None:
    """Commit this turn's pending changes in `cwd` as a checkpoint.

    `paths` are the files THIS turn wrote, repo-relative. Only they are staged.

    STAGE THIS TURN'S FILES, NOT THE TREE. This used to run `git add -A`, which
    on a shared checkout is not "save my work", it is "commit whatever every
    other agent happens to have open right now". Measured 2026-08-13: one
    checkpoint commit carried this turn's script change together with another
    agent's half-finished database models and two migrations, under the message
    "checkpoint: a turn". Three costs, all real: another agent's in-progress
    work is committed as though it were finished, provenance is destroyed, and
    the platform does on every turn exactly what CLAUDE.md forbids agents from
    doing ("Do NOT `git add -A` / commit the whole tree").

    With no paths (an older caller, or a turn that recorded none) it still falls
    back to `add -A`, because the one thing worse than a messy checkpoint is a
    lost one. Returns the short sha, or None if skipped or on any failure.
    Never raises.
    """
    try:
        d = Path(cwd)
        if not d.exists() or not _is_repo(d):
            return None
        env = {**os.environ, **_ENV}
        scope = ["--", *paths] if paths else []
        with _commit_lock(d):
            # Scoped to the same paths as the add, or "nothing to do" would be
            # answered by somebody else's dirty file and we would commit an
            # empty change (or worse, decide there was nothing of ours to save).
            status = _git(["status", "--porcelain", *scope], d, timeout=30)
            if status.returncode != 0 or not status.stdout.strip():
                return None  # nothing of ours to checkpoint
            add = _git(["add", *(paths if paths else ["-A"])], d, env=env)
            if add.returncode != 0:
                log.info("checkpoint add failed in %s: %s", cwd, (add.stderr or "")[:200])
                return None
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            commit = _git(
                # Only what we staged. Without this, a concurrent turn's already
                # -staged files ride along in our commit.
                ["commit", "-q", "--no-verify", "-m", f"checkpoint: {label} {ts}", *scope],
                d, env=env,
            )
            if commit.returncode != 0:
                log.info("checkpoint commit skipped in %s: %s", cwd, (commit.stderr or commit.stdout)[:200])
                return None
            sha = _git(["rev-parse", "--short", "HEAD"], d, timeout=15)
            return sha.stdout.strip() or None
    except Exception as e:  # pragma: no cover — best-effort, never block a turn
        log.info("checkpoint failed in %s: %s", cwd, e)
        return None
