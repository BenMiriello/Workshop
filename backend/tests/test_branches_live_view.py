"""What `/branches?live=true` answers, and what reaping a tree costs.

THE DEFECT THIS FILE PINS, measured on this installation on 2026-08-27. The
live listing read the `worktree` RECORD TABLE. The table only ever has a row for
a tree allocated through the record path, so eleven abandoned worktrees on disk
-- every one of them dirty, four days cold, locked -- produced a page that said
"Nothing is stuck", while the one row it did return pointed at a `/tmp` path
that had not existed for hours. A view whose subject is "what is stuck" cannot
take its list of things from a register most things were never entered in.

So the live view starts from the DISK and joins the record onto it, and these
tests drive REAL git for the same reason `test_worktree_records.py` does: a
fixture that reports a worktree without a directory behind it would pass while
proving the opposite of the point.

NO DATABASE IS NEEDED and that is deliberate. `worktree_records.list_records` is
stubbed here, because what is under test is the JOIN -- which side wins on which
field, and what happens when one side is missing -- and none of that is a
question about Postgres. `test_worktree_records.py` covers the record store
itself.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import git_service, worktree_records
import services.worktrees as wt

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


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A real git project registered as `proj`, with its own branches root."""
    proj_dir = tmp_path / "projects" / "proj"
    proj_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(proj_dir)], check=True,
                   capture_output=True, env={**os.environ, **_GIT_ENV})
    (proj_dir / "README.md").write_text("base\n")
    _git(proj_dir, "add", "README.md")
    _git(proj_dir, "commit", "-m", "init")

    branches = tmp_path / "branches"
    branches.mkdir()
    monkeypatch.setattr(wt, "BRANCHES_ROOT", branches)

    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"projects": {"proj": {"dir": str(proj_dir)}}}))
    monkeypatch.setattr(wt, "PROJECTS_REGISTRY", registry)
    return proj_dir


@pytest.fixture()
def no_records(monkeypatch):
    """This installation keeps no branch records at all."""
    def _down(*a, **k):
        raise worktree_records.WorktreesUnavailable("no relational store")

    for name in ("list_records", "record_for_branch"):
        monkeypatch.setattr(worktree_records, name, _down)


def _rows(**kw) -> dict:
    return {r["branch_slug"]: r for r in wt.live_branches(**kw)["worktrees"]}


# ---------------------------------------------------------------------------
# the join
# ---------------------------------------------------------------------------

def test_a_tree_nothing_recorded_is_a_ROW_and_not_an_absence(project, no_records):
    """The eleven-tree case. This is the whole bug in one assertion."""
    wt.create_branch("proj", "unremembered")

    out = wt.live_branches()
    assert out["records_available"] is False
    rows = {r["branch_slug"]: r for r in out["worktrees"]}
    assert "unremembered" in rows

    row = rows["unremembered"]
    assert row["presence"] == "tree"
    assert row["recorded"] is False
    # NOT "allocated". Nothing allocated it through the record path, and a
    # plausible-looking status is how "no record" got conflated with "a record"
    # in the first place.
    assert row["status"] is None
    assert row["branch"] == "wt/proj/unremembered"
    assert Path(row["path"]).is_dir()
    # The base a git-only tree will be compared against is the project's
    # canonical branch, resolved now, because there is no `base_ref` recorded.
    assert row["base_ref"] == "main"
    assert row["head_sha"]
    assert row["tip_at"]


def test_the_record_supplies_the_intent_and_the_disk_supplies_the_place(
        project, monkeypatch):
    wt.create_branch("proj", "joined")
    record = {
        "id": "11111111-1111-1111-1111-111111111111", "app_id": None,
        "project_slug": "proj", "scope": "project:proj",
        "branch_slug": "joined", "branch": "wt/proj/joined",
        "path": "/somewhere/that/moved", "base_ref": "origin/main",
        "base_sha": None, "ticket_id": "T-9", "allocated_by": "sweep",
        "status": "provisioned", "merged_sha": None, "removed_at": None,
        "created_at": "2026-08-23T00:00:00+00:00",
        "updated_at": "2026-08-23T00:00:00+00:00",
    }
    monkeypatch.setattr(worktree_records, "list_records", lambda **k: [record])

    row = _rows()["joined"]
    assert row["recorded"] is True
    assert row["ticket_id"] == "T-9"          # only the record can say this
    assert row["allocated_by"] == "sweep"
    assert row["status"] == "provisioned"
    # ...and only the disk can say where it actually is.
    assert row["path"] == str(wt.BRANCHES_ROOT / "proj" / "joined")
    assert row["presence"] == "tree"


def test_a_record_whose_directory_is_gone_is_a_DISTINCT_state(project, monkeypatch):
    """One of these existed in the live database: a fixture pointing at /tmp."""
    record = {
        "id": "22222222-2222-2222-2222-222222222222", "app_id": None,
        "project_slug": "proj", "scope": "project:proj",
        "branch_slug": "vanished", "branch": "wt/proj/vanished",
        "path": "/tmp/pytest-of-nobody/branches/proj/vanished",
        "base_ref": "main", "base_sha": None, "ticket_id": None,
        "allocated_by": None, "status": "allocated", "merged_sha": None,
        "removed_at": None, "created_at": "2026-08-23T00:00:00+00:00",
        "updated_at": "2026-08-23T00:00:00+00:00",
    }
    monkeypatch.setattr(worktree_records, "list_records", lambda **k: [record])

    row = _rows()["vanished"]
    assert row["presence"] == "gone"
    assert row["recorded"] is True


def test_a_record_for_a_project_the_registry_never_heard_of_still_appears(
        project, monkeypatch):
    """281 of these were in the live table, all under a slug from a fixture.

    A row nothing on this box can resolve is exactly the row worth showing: it
    is bookkeeping with nothing behind it, and hiding it is how it accumulated.
    """
    record = {
        "id": "33333333-3333-3333-3333-333333333333", "app_id": None,
        "project_slug": "not-a-project", "scope": "project:not-a-project",
        "branch_slug": "ghost", "branch": "wt/not-a-project/ghost",
        "path": "/tmp/gone", "base_ref": "master", "base_sha": None,
        "ticket_id": None, "allocated_by": None, "status": "allocated",
        "merged_sha": None, "removed_at": None,
        "created_at": "2026-08-23T00:00:00+00:00",
        "updated_at": "2026-08-23T00:00:00+00:00",
    }
    monkeypatch.setattr(worktree_records, "list_records", lambda **k: [record])
    assert _rows()["ghost"]["presence"] == "gone"


def test_a_directory_git_lost_is_visible_as_an_ORPHAN(project, no_records):
    """What a teardown that fell through to `rmtree` leaves behind.

    It has no branch, no worktree metadata and no record, so it is invisible to
    git AND to the table, and one of them was sitting in the operator's branches
    directory holding a whole checkout of disk.
    """
    stray = wt.BRANCHES_ROOT / "proj" / "left-behind"
    stray.mkdir(parents=True)
    (stray / "somebody-was-working-here.txt").write_text("hi\n")

    row = _rows()["left-behind"]
    assert row["presence"] == "orphan"
    assert row["recorded"] is False
    assert row["head_sha"] is None


def test_a_worktree_git_knows_about_is_not_also_reported_as_an_orphan(
        project, no_records):
    wt.create_branch("proj", "real-one")
    rows = _rows()
    assert rows["real-one"]["presence"] == "tree"
    assert len([r for r in rows.values() if r["presence"] == "orphan"]) == 0


def test_the_project_filter_covers_both_sides_of_the_join(project, monkeypatch):
    wt.create_branch("proj", "mine")
    record = {
        "id": "44444444-4444-4444-4444-444444444444", "app_id": None,
        "project_slug": "other", "scope": "project:other",
        "branch_slug": "theirs", "branch": "wt/other/theirs",
        "path": "/tmp/other", "base_ref": "main", "base_sha": None,
        "ticket_id": None, "allocated_by": None, "status": "allocated",
        "merged_sha": None, "removed_at": None, "created_at": None,
        "updated_at": None,
    }
    monkeypatch.setattr(worktree_records, "list_records", lambda **k: [record])

    assert set(_rows(project_slug="proj")) == {"mine"}
    assert set(_rows(project_slug="other")) == {"theirs"}


# ---------------------------------------------------------------------------
# reaping: the verb the page can now offer
# ---------------------------------------------------------------------------

def test_reaping_a_DIRTY_tree_saves_the_work_before_it_removes_it(
        project, no_records):
    wt.create_branch("proj", "dirty")
    tree = wt.BRANCHES_ROOT / "proj" / "dirty"
    (tree / "tracked.txt").write_text("edited\n")
    _git(tree, "add", "tracked.txt")
    (tree / "never-added.txt").write_text("the work that is usually at stake\n")

    out = wt.discard_branch("proj", "dirty")
    assert out["ok"] is True
    assert out["snapshot_ref"] == "refs/wip/dirty"
    assert out["snapshot_sha"]
    assert out["dirty_entries"] == 2
    assert not tree.exists()

    # THE UNTRACKED FILE IS IN IT. That is most of what an abandoned tree is
    # holding, and a tracked-only snapshot would report success having saved
    # nothing.
    listed = _git(project, "ls-tree", "-r", "--name-only", out["snapshot_sha"])
    assert "never-added.txt" in listed
    assert "tracked.txt" in listed


def test_reaping_a_CLEAN_branch_that_is_ahead_keeps_its_commits_reachable(
        project, no_records):
    """Teardown runs `git branch -D`, so the commits go with the ref."""
    wt.create_branch("proj", "ahead")
    tree = wt.BRANCHES_ROOT / "proj" / "ahead"
    (tree / "work.txt").write_text("landed\n")
    _git(tree, "add", "work.txt")
    _git(tree, "commit", "-m", "work")
    tip = _git(tree, "rev-parse", "HEAD")

    out = wt.discard_branch("proj", "ahead")
    assert out["ok"] is True
    assert out["dirty_entries"] == 0
    assert out["snapshot_ref"] == "refs/wip/ahead"
    assert git_service.rev_parse(project, "refs/wip/ahead", verify=True) == tip


def test_reaping_a_branch_the_trunk_already_has_leaves_no_ref_behind(
        project, no_records):
    """Otherwise every routine merge-then-reap would litter `refs/wip`."""
    wt.create_branch("proj", "nothing-new")
    out = wt.discard_branch("proj", "nothing-new")
    assert out["ok"] is True
    assert out["snapshot_ref"] is None
    assert git_service.rev_parse(project, "refs/wip/nothing-new",
                                 verify=True) is None


def test_reaping_a_directory_with_no_git_REFUSES_until_it_is_forced(
        project, no_records):
    """Nothing can speak for an orphan, so removing it is a plain deletion."""
    stray = wt.BRANCHES_ROOT / "proj" / "orphan"
    stray.mkdir(parents=True)
    (stray / "unknown.txt").write_text("nobody knows\n")

    refused = wt.discard_branch("proj", "orphan")
    assert refused["ok"] is False
    assert refused["removed"] is False
    assert "force" in refused["detail"]
    assert stray.exists()

    forced = wt.discard_branch("proj", "orphan", force=True)
    assert forced["ok"] is True
    assert not stray.exists()


def test_a_refused_reap_leaves_the_tree_exactly_as_it_was(project, no_records,
                                                          monkeypatch):
    """A dirty tree the next reader can see beats work nobody can get back."""
    wt.create_branch("proj", "unsnapshottable")
    tree = wt.BRANCHES_ROOT / "proj" / "unsnapshottable"
    (tree / "at-stake.txt").write_text("do not lose me\n")

    monkeypatch.setattr(git_service, "snapshot",
                        lambda *a, **k: {"ok": False, "detail": "disk full"})
    out = wt.discard_branch("proj", "unsnapshottable")
    assert out["ok"] is False
    assert out["removed"] is False
    assert "disk full" in out["detail"]
    assert (tree / "at-stake.txt").read_text() == "do not lose me\n"
