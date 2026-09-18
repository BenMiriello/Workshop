"""A retry builds on the previous attempt's branch, not a fresh one.

The defect this closes: a supervised sweep commits real work to a task
worktree, the run does not converge (rc=1/2, an acceptance-criteria failure,
or any other non-terminal failure release_or_block handles), and the ticket
goes back to `todo`. The next attempt used to call `alloc_task_worktree` again
with the same title; the old worktree was still on disk, so
`branch_slug_for_work` found the name taken and minted a DETERMINISTIC BUT
DIFFERENT sibling slug instead of reusing it. Measured 2026-09-18: 28
`wt/development/*` branches held ~55 commits, none reachable from HEAD, with
one ticket (`60fa5411`) carrying three independent implementations of the same
fix across three abandoned branches.

The fix is two pure readers in `services/tasks.py` (`retry_worktree_branch_slug`,
`retry_prompt_context`) plus a writer in `scripts/project_ralph.sh`: a
`worktree` evidence note records the branch the moment a task's tree is
allocated, and a `retry_reason` evidence note (written from `release_or_block`)
records why the attempt did not land. Both are file-backed ticket evidence --
no database required -- so this test drives them exactly as `project_ralph.sh`
does: real git, a real allocated worktree, a real commit on it, then the two
readers a retry claim consults before it asks `worktrees` for a tree and
before it hands `ralph.sh` a prompt.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import tasks, worktrees  # noqa: E402

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.test",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.test",
}


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(cwd), *args], check=True,
                         capture_output=True, text=True,
                         env={**os.environ, **_GIT_ENV})
    return res.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True,
                   capture_output=True, env={**os.environ, **_GIT_ENV})
    (path / "README.md").write_text("base\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "init")


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A real git project registered as `proj`, with its own branches root.

    Same shape as `test_worktree_naming.py`'s fixture: no database, so this
    exercises exactly the file-backed path every project worktree and every
    file-backed ticket store already runs on.
    """
    import services.worktrees as wt_mod

    proj_dir = tmp_path / "projects" / "proj"
    _init_repo(proj_dir)

    branches = tmp_path / "branches"
    branches.mkdir()
    monkeypatch.setattr(wt_mod, "BRANCHES_ROOT", branches)

    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"projects": {"proj": {"dir": str(proj_dir)}}}))
    monkeypatch.setattr(wt_mod, "PROJECTS_REGISTRY", registry)
    return proj_dir


def _record_worktree_note(task: dict, branch_slug: str) -> dict:
    """What `project_ralph.sh` writes right after allocating a task's tree."""
    task = dict(task)
    ev = list(task.get("evidence") or [])
    ev.append({"kind": "worktree", "ref": branch_slug, "ts": "2026-09-18T00:00:00Z"})
    task["evidence"] = ev
    return task


def _record_retry_reason(task: dict, reason: str) -> dict:
    """What `release_or_block` writes on the retry leg, before `set-status todo`."""
    task = dict(task)
    ev = list(task.get("evidence") or [])
    ev.append({"kind": "retry_reason", "ref": reason, "ts": "2026-09-18T00:01:00Z"})
    task["evidence"] = ev
    return task


# ---------------------------------------------------------------------------
# The pure readers
# ---------------------------------------------------------------------------

def test_first_attempt_has_no_retry_branch_or_context():
    """A brand-new ticket carries neither evidence kind: both readers are quiet."""
    task = {"id": "t1", "evidence": []}
    assert tasks.retry_worktree_branch_slug(task) == ""
    assert tasks.retry_prompt_context(task) == ""


def test_retry_branch_slug_is_the_most_recent_worktree_note():
    task = {"id": "t1", "evidence": [
        {"kind": "worktree", "ref": "old-slug"},
        {"kind": "receipt", "ref": "job-1"},
        {"kind": "worktree", "ref": "current-slug"},
    ]}
    assert tasks.retry_worktree_branch_slug(task) == "current-slug"


def test_retry_prompt_context_carries_the_reviewer_reason():
    task = {"id": "t1", "evidence": [
        {"kind": "worktree", "ref": "fix-thing"},
        {"kind": "retry_reason", "ref": "typecheck failed: 3 errors in App.tsx"},
    ]}
    ctx = tasks.retry_prompt_context(task)
    assert "typecheck failed: 3 errors in App.tsx" in ctx
    assert "RETRY" in ctx


def test_retry_prompt_context_uses_the_latest_reason_only():
    """A ticket retried twice must not see stale reasons from its first failure."""
    task = {"id": "t1", "evidence": [
        {"kind": "retry_reason", "ref": "stale: missing tests"},
        {"kind": "retry_reason", "ref": "current: lint errors in worktrees.py"},
    ]}
    ctx = tasks.retry_prompt_context(task)
    assert "current: lint errors in worktrees.py" in ctx
    assert "stale: missing tests" not in ctx


# ---------------------------------------------------------------------------
# End to end: the worktree a retry gets IS the one the first attempt built
# ---------------------------------------------------------------------------

def test_retry_reattaches_the_prior_attempts_worktree_and_keeps_its_commit(project):
    """The headline claim: a retry's tree is the first attempt's tree.

    Simulates one full cycle: attempt 1 allocates a worktree and commits real
    work to it; the run does not converge, so `project_ralph.sh` records the
    branch and a reviewer reason on the ticket and releases it to `todo`.
    Attempt 2's claim reads the ticket back (exactly what `$NEXT` is in the
    sweep) and must resolve to the SAME directory and branch, with attempt 1's
    commit still on it -- not a fresh `alloc_task_worktree` call, which would
    mint a disambiguated sibling because the old slug is still taken.
    """
    ticket_id = "9c1e2f3a4b5d4e6f8a9b0c1d2e3f4a5b"
    task = {"id": ticket_id, "evidence": []}

    # --- attempt 1: allocate, do real work, commit it -----------------------
    alloc = worktrees.allocate_task_worktree(
        "proj", ticket_id, title="Fix the flaky import")
    attempt_1_dir = Path(alloc["dir"])
    branch_slug = alloc["branch_slug"]

    (attempt_1_dir / "fix.py").write_text("real work from attempt 1\n")
    _git(attempt_1_dir, "add", "fix.py")
    _git(attempt_1_dir, "commit", "-m", "attempt 1: partial fix")
    attempt_1_head = _git(attempt_1_dir, "rev-parse", "HEAD")

    # --- the run does not converge: project_ralph.sh's writers fire ---------
    task = _record_worktree_note(task, branch_slug)
    task = _record_retry_reason(task, "reviewer: acceptance criterion failed, tests still red")

    # --- attempt 2's claim: reads the ticket, resolves the retry branch -----
    retry_slug = tasks.retry_worktree_branch_slug(task)
    assert retry_slug == branch_slug

    retry_dir = worktrees.branch_dir("proj", retry_slug)
    assert retry_dir == attempt_1_dir, (
        "a retry must resolve to the exact directory the first attempt used, "
        "not a fresh worktree"
    )
    assert retry_dir.is_dir()
    assert (retry_dir / "fix.py").read_text() == "real work from attempt 1\n"
    assert _git(retry_dir, "rev-parse", "HEAD") == attempt_1_head

    # --- and the reviewer's reason travels into the next attempt's prompt ---
    prompt_suffix = tasks.retry_prompt_context(task)
    assert "acceptance criterion failed, tests still red" in prompt_suffix

    worker_prompt = "Fix the flaky import" + prompt_suffix
    assert "acceptance criterion failed, tests still red" in worker_prompt


def test_without_the_fix_a_second_allocation_would_have_forked_a_sibling_branch(project):
    """Documents the bug this closes, so a regression here fails loudly.

    Calling `allocate_task_worktree` again for the SAME ticket (the old
    behaviour) does not raise and does not reuse attempt 1's tree -- it mints
    a disambiguated sibling, because the slug is still taken by attempt 1's
    worktree on disk. That is the mechanism that produced three branches for
    one ticket. The fix never takes this path on a retry; this test exists to
    pin why the reattach path above is necessary rather than optional.
    """
    ticket_id = "1a2b3c4d5e6f4a5b6c7d8e9f0a1b2c3d"
    alloc_1 = worktrees.allocate_task_worktree(
        "proj", ticket_id, title="Same fix, retried")
    alloc_2 = worktrees.allocate_task_worktree(
        "proj", ticket_id, title="Same fix, retried")

    assert alloc_1["branch_slug"] != alloc_2["branch_slug"]
    assert alloc_1["dir"] != alloc_2["dir"]
