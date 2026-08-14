"""A checkpoint commits the turn's own work, and never the platform repo.

Two defects, both observed on 2026-08-13 in commit cf3eabd, which carried one
turn's script change together with another agent's half-finished database models
and two migrations under the message "checkpoint: a turn":

1. The skip-the-platform-repo guard asked whether cwd WAS the monorepo root.
   `git add -A` is repo-wide from any subdirectory, so a turn running in
   backend/ or shared/ or apps/ or branches/ passed that check and then
   auto-committed the whole monorepo.

2. Even where checkpointing is wanted (project repos), `git add -A` stages every
   dirty file in the tree, so concurrent agents commit each other's unfinished
   work under a message naming only one of them.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from services import app_runner, checkpoints


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(["init", "-q", "-b", "main"], tmp_path)
    (tmp_path / "seed.txt").write_text("seed\n")
    _git(["add", "-A"], tmp_path)
    _git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed"], tmp_path)
    return tmp_path


def _committed_files(repo):
    r = _git(["show", "--name-only", "--format=", "HEAD"], repo)
    return sorted(f for f in r.stdout.split() if f)


def test_stages_only_the_paths_this_turn_wrote(repo):
    """The whole point: another agent's dirty file must not ride along."""
    (repo / "mine.txt").write_text("my work\n")
    (repo / "theirs.txt").write_text("another agent, mid-edit\n")

    sha = checkpoints.checkpoint(str(repo), "my turn", ["mine.txt"])

    assert sha, "checkpoint should have committed my file"
    assert _committed_files(repo) == ["mine.txt"]
    # And theirs is still uncommitted, exactly where they left it.
    assert "theirs.txt" in _git(["status", "--porcelain"], repo).stdout


def test_no_paths_falls_back_to_staging_everything(repo):
    """A messy checkpoint is recoverable; a missing one is not. When the turn
    recorded no paths, the old behaviour must survive."""
    (repo / "a.txt").write_text("a\n")
    (repo / "b.txt").write_text("b\n")

    assert checkpoints.checkpoint(str(repo), "turn", None)
    assert _committed_files(repo) == ["a.txt", "b.txt"]


def test_skips_when_only_other_peoples_files_are_dirty(repo):
    """"Is there anything to commit" has to be asked about OUR paths.

    Asked about the whole tree, a turn that wrote nothing would still see a dirty
    tree, stage nothing, and then either commit empty or sweep in whatever was
    there.
    """
    (repo / "theirs.txt").write_text("not mine\n")

    assert checkpoints.checkpoint(str(repo), "turn", ["mine.txt"]) is None
    assert "theirs.txt" in _git(["status", "--porcelain"], repo).stdout


def test_a_deleted_file_still_checkpoints(repo):
    """`git add <path>` records a deletion; a turn that removes a file must not
    silently drop that from its own checkpoint."""
    assert checkpoints.checkpoint(str(repo), "turn", ["seed.txt"]) is None  # clean
    (repo / "seed.txt").unlink()
    assert checkpoints.checkpoint(str(repo), "turn", ["seed.txt"])
    assert _committed_files(repo) == ["seed.txt"]
    assert not (repo / "seed.txt").exists()


def test_platform_repo_is_never_auto_committed_from_a_subdirectory():
    """The guard is containment, not identity.

    Every one of these resolves to the platform repo, and `git add -A` from any
    of them is repo-wide. `projects/*` carry their own .git, so they are their
    own tree and remain checkpointable.
    """
    root = app_runner.WORKSHOP_ROOT
    for sub in ("", "backend", "shared", "apps"):
        p = root / sub if sub else root
        if not p.exists():
            continue
        assert app_runner.is_in_self_repo(p) is True, f"{p} must be protected"
    assert app_runner.is_in_self_repo("/tmp") is False


def test_undecidable_location_is_treated_as_protected(monkeypatch):
    """Failing closed here costs a skipped checkpoint. Failing open costs
    another agent's work committed half-written."""
    def boom(*a, **k):
        raise OSError("git unavailable")

    monkeypatch.setattr(subprocess, "run", boom)
    assert app_runner.is_in_self_repo("/anywhere") is True


def test_written_paths_reads_the_turns_own_journal(tmp_path, monkeypatch):
    """Paths come from the journal, so no state has to be threaded through the
    drive loop and a re-attached turn still knows what it wrote."""
    from services import build_streaming as bs

    jdir = tmp_path / "journal"
    jdir.mkdir()
    monkeypatch.setattr(bs, "_journal_dir", lambda tid: jdir)

    cwd = tmp_path / "tree"
    (cwd / "sub").mkdir(parents=True)
    rows = [
        {"message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": str(cwd / "a.py")}}]}},
        {"message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": str(cwd / "sub" / "b.ts")}}]}},
        # Reads are not writes.
        {"message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": str(cwd / "c.md")}}]}},
        # Outside the tree: not ours to commit.
        {"message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": "/tmp/elsewhere.txt"}}]}},
        # Duplicates collapse.
        {"message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": str(cwd / "a.py")}}]}},
    ]
    (jdir / "stream.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    assert bs._written_paths("t1", str(cwd)) == ["a.py", "sub/b.ts"]


def test_written_paths_is_empty_when_there_is_no_journal(tmp_path, monkeypatch):
    """Which the caller turns into the add -A fallback rather than an empty
    commit scope that would silently save nothing."""
    from services import build_streaming as bs

    monkeypatch.setattr(bs, "_journal_dir", lambda tid: tmp_path / "gone")
    assert bs._written_paths("t1", str(tmp_path)) == []
