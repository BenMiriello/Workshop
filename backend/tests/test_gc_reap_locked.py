"""Test that a locked, merged worktree is fully reclaimed by the reaper.

A worktree created by allocate_task_worktree is LOCKED (git worktree lock).
Without the unlock-first teardown, the sequence was:
  git worktree remove --force  → refused ("cannot remove a locked working tree")
  shutil.rmtree                → directory gone, but git admin record intact
  git worktree prune           → refused (prune also skips locked trees)
  git branch -D                → refused ("checked out at <gone dir>")
  → branch slug permanently stuck, re-allocation fails with "already exists"

After the fix: worktree_unlock before worktree_remove, prune after the rmtree
fallback.  Both the gc.reap_merged_worktrees path (via discard_branch) and the
worktrees.cleanup_merged path (via _reap_worktree) go through _tear_down_tree,
which does the unlock first.

CANONICAL MERGED DEFINITION: git_service.is_ancestor(project_dir, branch, main)
against the local canonical branch.  Both paths now use this check.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import gc, git_service
import services.worktrees as wt_mod


# THIS MODULE IS FILE-BACKED, AND SAYS SO. Without the redirect these tests
# resolve the OPERATOR's tenant and write their fixtures into the real database:
# measured 2026-08-27, 281 rows under this file's `test-proj` slug, at
# `/tmp/pytest-of-*` paths, which is what `/branches` was reading.
# `conftest._no_test_may_resolve_the_real_tenant` now fails the run rather than
# letting that happen quietly.
@pytest.fixture(autouse=True)
def _file_backed(tmp_path, monkeypatch):
    from tests.conftest import redirect_file_store

    redirect_file_store(monkeypatch, tmp_path)



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test User",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test User",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=_GIT_ENV, capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {list(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True,
                   capture_output=True, env=_GIT_ENV)
    # Need at least one commit so main exists.
    (path / "readme.txt").write_text("initial")
    _git(path, "add", "readme.txt")
    _git(path, "commit", "-m", "init")


def _assert_branch_gone(repo: Path, branch: str) -> None:
    """Assert that the git ref no longer resolves."""
    sha = git_service.rev_parse(repo, branch, verify=True)
    assert sha is None, f"branch ref still resolves to {sha}"


def _assert_worktree_admin_gone(repo: Path, wt_dir: Path) -> None:
    """Assert git worktree list no longer mentions wt_dir."""
    entries = git_service.worktree_list(repo)
    dirs = [e["dir"] for e in entries]
    assert str(wt_dir) not in dirs, (
        f"worktree admin record still present for {wt_dir}; entries: {dirs}"
    )


# ---------------------------------------------------------------------------
# Fixture: a locked worktree with its branch merged into main
# ---------------------------------------------------------------------------

@pytest.fixture()
def locked_merged_env(tmp_path, monkeypatch):
    """Create a locked worktree, merge its branch into main, return context.

    Returns a dict:
        project_slug   str
        branch_slug    str
        branch         str   (full git ref, e.g. wt/test-proj/my-task)
        proj_dir       Path  (main repo)
        wt_dir         Path  (worktree directory)
    """
    project_slug = "test-proj"
    proj_dir = tmp_path / "project"
    proj_dir.mkdir()
    _init_repo(proj_dir)

    # Wire the module-level globals so worktrees/ and gc/ resolve the project.
    branches = tmp_path / "branches"
    branches.mkdir()
    registry = tmp_path / "projects.json"
    registry.write_text(
        json.dumps({"projects": {project_slug: {"dir": str(proj_dir)}}})
    )
    monkeypatch.setattr(wt_mod, "BRANCHES_ROOT", branches)
    monkeypatch.setattr(wt_mod, "PROJECTS_REGISTRY", registry)

    # Allocate (and lock) a worktree.  _record is best-effort; with no DB it
    # returns None, which is the normal "no relational store" path.
    result = wt_mod.allocate_task_worktree(project_slug, "my-task-id")
    branch_slug = result["branch_slug"]
    wt_dir = Path(result["dir"])
    branch = result["branch"]

    # Confirm the worktree IS locked before we proceed.
    entries = git_service.worktree_list(proj_dir)
    locked_entry = next((e for e in entries if e["dir"] == str(wt_dir)), None)
    assert locked_entry is not None, "worktree not found in git worktree list"
    assert locked_entry["locked"], "worktree should be locked after allocate_task_worktree"

    # Add a commit on the branch so there is real work to merge.
    (wt_dir / "work.txt").write_text("work done")
    _git(wt_dir, "add", "work.txt")
    _git(wt_dir, "commit", "-m", "do work on branch")

    # Merge the branch into main so is_ancestor(branch, main) returns True.
    _git(proj_dir, "merge", "--no-ff", branch, "-m", f"Merge {branch}")

    return {
        "project_slug": project_slug,
        "branch_slug": branch_slug,
        "branch": branch,
        "proj_dir": proj_dir,
        "wt_dir": wt_dir,
    }


# ---------------------------------------------------------------------------
# gc.reap_merged_worktrees path
# ---------------------------------------------------------------------------

def test_gc_reap_removes_locked_worktree_directory(locked_merged_env):
    """gc.reap_merged_worktrees(apply=True) deletes the worktree directory."""
    ctx = locked_merged_env
    assert ctx["wt_dir"].exists(), "precondition: worktree dir must exist"

    result = gc.reap_merged_worktrees(apply=True)

    assert not ctx["wt_dir"].exists(), "worktree directory still present after reap"
    assert result["count"] == 1
    assert result["reaped"][0]["branch_slug"] == ctx["branch_slug"]


def test_gc_reap_removes_locked_worktree_branch_ref(locked_merged_env):
    """gc.reap_merged_worktrees(apply=True) deletes the git branch ref."""
    ctx = locked_merged_env

    gc.reap_merged_worktrees(apply=True)

    _assert_branch_gone(ctx["proj_dir"], ctx["branch"])


def test_gc_reap_removes_locked_worktree_admin_record(locked_merged_env):
    """gc.reap_merged_worktrees(apply=True) removes the git admin record."""
    ctx = locked_merged_env

    gc.reap_merged_worktrees(apply=True)

    _assert_worktree_admin_gone(ctx["proj_dir"], ctx["wt_dir"])


def test_gc_reap_branch_slug_reusable_after_reap(locked_merged_env):
    """The branch slug can be allocated again immediately after gc reap."""
    ctx = locked_merged_env

    gc.reap_merged_worktrees(apply=True)

    # Must not raise "worktree dir already exists" or "branch already exists".
    result = wt_mod.allocate_task_worktree(ctx["project_slug"], "my-task-id")
    assert Path(result["dir"]).exists()
    assert result["branch_slug"] == ctx["branch_slug"]


# ---------------------------------------------------------------------------
# worktrees.cleanup_merged path
# ---------------------------------------------------------------------------

def test_cleanup_merged_removes_locked_worktree_directory(locked_merged_env):
    """cleanup_merged() deletes the worktree directory for a locked+merged tree."""
    ctx = locked_merged_env
    assert ctx["wt_dir"].exists(), "precondition: worktree dir must exist"

    result = wt_mod.cleanup_merged(ctx["project_slug"])

    assert not ctx["wt_dir"].exists(), "worktree directory still present after cleanup_merged"
    assert ctx["branch_slug"] in result["removed"]
    assert ctx["branch_slug"] not in result["kept"]


def test_cleanup_merged_removes_locked_worktree_branch_ref(locked_merged_env):
    """cleanup_merged() deletes the git branch ref for a locked+merged tree."""
    ctx = locked_merged_env

    wt_mod.cleanup_merged(ctx["project_slug"])

    _assert_branch_gone(ctx["proj_dir"], ctx["branch"])


def test_cleanup_merged_removes_locked_worktree_admin_record(locked_merged_env):
    """cleanup_merged() removes the git admin record for a locked+merged tree."""
    ctx = locked_merged_env

    wt_mod.cleanup_merged(ctx["project_slug"])

    _assert_worktree_admin_gone(ctx["proj_dir"], ctx["wt_dir"])


def test_cleanup_merged_branch_slug_reusable_after_cleanup(locked_merged_env):
    """The branch slug can be allocated again immediately after cleanup_merged."""
    ctx = locked_merged_env

    wt_mod.cleanup_merged(ctx["project_slug"])

    result = wt_mod.allocate_task_worktree(ctx["project_slug"], "my-task-id")
    assert Path(result["dir"]).exists()
    assert result["branch_slug"] == ctx["branch_slug"]


# ---------------------------------------------------------------------------
# Sanity: dry-run reports merged but does not touch disk
# ---------------------------------------------------------------------------

def test_gc_dry_run_reports_but_does_not_remove(locked_merged_env):
    """gc.reap_merged_worktrees(apply=False) reports without removing."""
    ctx = locked_merged_env

    result = gc.reap_merged_worktrees(apply=False)

    assert result["count"] == 1
    assert result["candidates"][0]["branch_slug"] == ctx["branch_slug"]
    assert ctx["wt_dir"].exists(), "dry-run must not delete the directory"
    # Branch ref should still exist.
    assert git_service.rev_parse(ctx["proj_dir"], ctx["branch"], verify=True) is not None
