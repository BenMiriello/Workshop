"""Auto-checkpoint commits for build-agent turns.

Part of the doc 05 history/undo substrate: after a builder turn, commit the
working tree in the agent's directory so the branch holds a recoverable timeline
even if the agent later breaks something or leaves work uncommitted. Best-effort:
never raises into the turn path; silently skips non-repos and clean trees. Uses a
neutral committer and --no-verify so project commit hooks (lefthook/commitlint,
e.g. gengui) cannot block a checkpoint.
"""
from __future__ import annotations

import logging
import os
import subprocess
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


def checkpoint(cwd: str, label: str) -> str | None:
    """Commit any pending changes in `cwd` as a checkpoint.

    Returns the short commit sha on success, or None if skipped (not a git repo,
    clean tree) or on any failure. Never raises.
    """
    try:
        d = Path(cwd)
        if not d.exists() or not _is_repo(d):
            return None
        status = _git(["status", "--porcelain"], d, timeout=30)
        if status.returncode != 0 or not status.stdout.strip():
            return None  # nothing to checkpoint
        env = {**os.environ, **_ENV}
        add = _git(["add", "-A"], d, env=env)
        if add.returncode != 0:
            log.info("checkpoint add failed in %s: %s", cwd, (add.stderr or "")[:200])
            return None
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        commit = _git(
            ["commit", "-q", "--no-verify", "-m", f"checkpoint: {label} {ts}"],
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
