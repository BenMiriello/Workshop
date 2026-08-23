"""The worktree record: a branch stops being only a directory.

Step B2 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`.

The claim under test is the one the design turns on: the record and the disk
cannot silently disagree. So these drive REAL git -- a real project, a real
worktree, a real merge -- rather than a mocked allocator, because a fixture that
reports "allocated" without a directory behind it would pass while proving the
opposite of the point.

The last test in the file is the one that keeps the rest honest: with no
relational store, allocation still works. Record-keeping is additive, and an
installation without Postgres must not lose the ability to make a branch.

Needs the OWNER credential, not just the application one: the fixture mints a
throwaway organization, and creating a tenant is deliberately outside what the
application role may do (`db/engine.py`). Skipped without it, like the rest of
the db suite -- except the no-database test, which needs no database by
definition and runs everywhere.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("sqlalchemy")

_HAS_DB = bool(os.environ.get("WORKSHOP_DATABASE_ADMIN_URL"))
needs_db = pytest.mark.skipif(
    not _HAS_DB,
    reason="no WORKSHOP_DATABASE_ADMIN_URL; the fixture mints a throwaway org, "
           "which the application role deliberately cannot do",
)


# ---------------------------------------------------------------------------
# git fixtures
# ---------------------------------------------------------------------------

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
    """A real git project with a registry entry, addressable as `proj`.

    The per-tree lock file lives under `.runtime/` of the workshop root, and
    `work_driver` resolves that once at import. It is left pointing at the real
    root on purpose: the lock is keyed by the md5 of the tree path, and every
    tree here is under a unique `tmp_path`, so no run can collide with another.
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


def _commit_in(worktree_dir: Path, name: str = "file.txt") -> str:
    (worktree_dir / name).write_text("work\n")
    _git(worktree_dir, "add", name)
    _git(worktree_dir, "commit", "-m", f"add {name}")
    return _git(worktree_dir, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# tenant fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def tenant(monkeypatch):
    """A throwaway org/user, with the record service pinned to it."""
    from db.engine import owner_session
    from db.ids import uuid7
    from db.models import Membership, Organization, UserAccount
    from services import tenancy

    from tests.conftest import (DB_ORG_PREFIX, _delete_test_orgs,
                                _delete_test_users)

    tag = uuid.uuid4().hex[:8]
    org_ids: list = []
    user_ids: list = []
    try:
        with owner_session() as s:
            user = UserAccount(id=uuid7(), email=f"worktree-{tag}@example.test",
                               name="Worktree test")
            s.add(user)
            s.flush()
            user_ids.append(user.id)
            org = Organization(id=uuid7(),
                               slug=f"{DB_ORG_PREFIX}worktree-{tag}",
                               name="Worktree test")
            s.add(org)
            s.flush()
            s.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
            org_ids.append(org.id)

        org_id = org_ids[0]
        monkeypatch.setattr(tenancy, "configured", lambda: True)
        monkeypatch.setattr(tenancy, "available", lambda: True)
        monkeypatch.setattr(tenancy, "org_and_user", lambda: (org_id, user.id))
        yield {"org_id": org_id, "user_id": user.id}
    finally:
        _delete_test_orgs(org_ids)
        _delete_test_users(user_ids)


# ---------------------------------------------------------------------------
# allocation writes a record
# ---------------------------------------------------------------------------

@needs_db
def test_allocating_a_worktree_writes_a_record(project, tenant):
    """The whole point: the tree is remembered, with the facts that were only
    ever derivable from `git worktree list` while the directory survived."""
    from services import worktree_records, worktrees

    result = worktrees.allocate_task_worktree("proj", "T-104",
                                              allocated_by="sweep")

    assert result["worktree_id"], "allocation did not write a record"
    record = worktree_records.get_record(result["worktree_id"])

    assert record["project_slug"] == "proj"
    assert record["scope"] == "project:proj"
    assert record["branch_slug"] == "t-104"
    assert record["branch"] == "wt/proj/t-104"
    assert record["path"] == result["dir"]
    assert Path(record["path"]).is_dir(), "the record names a tree that is not there"
    # The base as a name AND as the commit it resolved to at creation. Unbound
    # project, so the cut point is the local canonical branch.
    assert record["base_ref"] == "main"
    assert record["base_sha"] == _git(project, "rev-parse", "main")
    assert len(record["base_sha"]) == 40
    # Who, and for which ticket. Neither was answerable before this row.
    assert record["allocated_by"] == "sweep"
    assert record["ticket_id"] == "T-104"
    assert record["status"] == "allocated"
    assert record["removed_at"] is None
    assert record["merged_sha"] is None


@needs_db
def test_the_record_is_findable_by_its_branch(project, tenant):
    from services import worktrees

    worktrees.allocate_task_worktree("proj", "find-me")
    record = worktrees.worktree_record("proj", "find-me")

    assert record is not None
    assert record["branch_slug"] == "find-me"


@needs_db
def test_a_branch_with_no_ticket_is_first_class(project, tenant):
    """Design sec 10.4: "writing with no ticket is common and not
    second-class". `create_branch` is that path, and the record carries a NULL
    ticket rather than the slug pressed into service as one."""
    from services import worktree_records, worktrees

    created = worktrees.create_branch("proj", "no-ticket")

    record = worktree_records.get_record(created["worktree_id"])
    assert record["ticket_id"] is None
    assert record["status"] == "allocated"
    assert record["branch"] == "wt/proj/no-ticket"


@needs_db
def test_provisioning_moves_the_record_and_names_what_it_cloned(project, tenant,
                                                                monkeypatch):
    """An allocated tree is a source checkout; a provisioned one can be built.
    The record distinguishes them, and the timeline names the directories."""
    import services.worktrees as wt_mod
    from services import worktree_records

    (project / "package-lock.json").write_text("{}\n")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "dep.js").write_text("//\n")
    _git(project, "add", "package-lock.json")
    _git(project, "commit", "-m", "lockfile")
    # No app manifest in this fixture, so the platform defaults apply.
    monkeypatch.setattr(wt_mod, "_read_app_manifest", lambda d: {})

    result = wt_mod.provision_worktree("project:proj", "T-9")

    record = worktree_records.get_record(result["worktree_id"])
    assert record["status"] == "provisioned"
    assert record["scope"] == "project:proj"

    kinds = [e["kind"] for e in worktree_records.timeline(result["worktree_id"])]
    assert kinds == ["allocated", "provisioned"]
    provisioned = [e for e in worktree_records.timeline(result["worktree_id"])
                   if e["kind"] == "provisioned"][0]
    assert provisioned["paths"] == ["node_modules"]


# ---------------------------------------------------------------------------
# the lifecycle
# ---------------------------------------------------------------------------

@needs_db
def test_merging_moves_the_record_to_merged(project, tenant):
    """The lifecycle move, with the commit that did it, recorded by the same
    call that performed the merge."""
    from services import worktree_records, worktrees

    allocated = worktrees.allocate_task_worktree("proj", "merges-cleanly")
    _commit_in(Path(allocated["dir"]))

    result = worktrees.merge_branch("proj", "merges-cleanly")
    assert result["ok"], result

    record = worktree_records.get_record(allocated["worktree_id"])
    assert record["status"] == "merged"
    assert record["merged_sha"] == result["commit"]
    # A terminal record says WHEN the tree left the disk; the database makes
    # that a constraint rather than a convention.
    assert record["removed_at"] is not None


@needs_db
def test_reaping_takes_the_tree_and_closes_the_record_together(project, tenant):
    """"Closes", not "removes": what happened to a branch is exactly the
    question the record exists to answer, so the row survives the directory."""
    from services import worktree_records, worktrees

    allocated = worktrees.allocate_task_worktree("proj", "gets-reaped")
    tree = Path(allocated["dir"])
    _commit_in(tree)

    worktrees.merge_branch("proj", "gets-reaped")

    assert not tree.exists(), "the merge did not reap the tree"
    record = worktree_records.get_record(allocated["worktree_id"])
    assert record["status"] == "merged"
    # And the branch is free to be allocated again, with its own record.
    again = worktrees.allocate_task_worktree("proj", "gets-reaped")
    assert again["worktree_id"] != allocated["worktree_id"]
    assert worktrees.worktree_record("proj", "gets-reaped")["id"] == \
        again["worktree_id"]


@needs_db
def test_discarding_moves_the_record_to_discarded_with_its_reason(project,
                                                                  tenant):
    from services import worktree_records, worktrees

    allocated = worktrees.allocate_task_worktree("proj", "thrown-away")
    tree = Path(allocated["dir"])

    worktrees.discard_branch("proj", "thrown-away", reason="superseded")

    assert not tree.exists()
    record = worktree_records.get_record(allocated["worktree_id"])
    assert record["status"] == "discarded"
    assert record["removed_at"] is not None
    assert record["merged_sha"] is None

    discarded = worktree_records.timeline(allocated["worktree_id"])[-1]
    assert discarded["kind"] == "discarded"
    assert discarded["detail"] == "superseded"


@needs_db
def test_a_terminal_record_leaves_the_live_list(project, tenant):
    from services import worktree_records, worktrees

    kept = worktrees.allocate_task_worktree("proj", "still-here")
    gone = worktrees.allocate_task_worktree("proj", "not-for-long")
    worktrees.discard_branch("proj", "not-for-long")

    live = {r["id"] for r in worktree_records.list_records(live_only=True)}
    assert kept["worktree_id"] in live
    assert gone["worktree_id"] not in live
    # The history is still readable; it is just not "live".
    everything = {r["id"] for r in worktree_records.list_records()}
    assert gone["worktree_id"] in everything


# ---------------------------------------------------------------------------
# the timeline (sec 9)
# ---------------------------------------------------------------------------

@needs_db
def test_the_timeline_keeps_its_structured_fields(project, tenant):
    """Sec 9: "The record goes on the worktree timeline as structured events,
    not as prose in a transcript." So the fields come back as fields."""
    from services import worktree_records, worktrees

    allocated = worktrees.allocate_task_worktree("proj", "gets-preempted")
    wt_id = allocated["worktree_id"]

    event = worktree_records.add_event(
        wt_id, kind="preempted", actor="chat:42",
        detail="operator asked for the fix now",
        sha="0" * 40, paths=["backend/services/worktrees.py"])

    assert event["kind"] == "preempted"
    assert event["actor"] == "chat:42"
    assert event["sha"] == "0" * 40
    assert event["detail"] == "operator asked for the fix now"
    assert event["paths"] == ["backend/services/worktrees.py"]
    assert event["at"]

    # And it is on the timeline, after the allocation, in that order.
    timeline = worktree_records.timeline(wt_id)
    assert [e["kind"] for e in timeline] == ["allocated", "preempted"]
    assert timeline[-1] == event


@needs_db
def test_a_snapshot_event_carries_its_sha(project, tenant):
    """The `snapshot <sha>` of sec 9: a yield produces a dangling ref, and the
    sha is what makes it resumable, so it is a column and not a sentence."""
    from services import git_service, worktree_records, worktrees

    allocated = worktrees.allocate_task_worktree("proj", "yields")
    tree = Path(allocated["dir"])
    (tree / "dirty.txt").write_text("half done\n")
    _git(tree, "add", "dirty.txt")
    snap = git_service.snapshot(tree, message="wip")

    event = worktree_records.add_event(allocated["worktree_id"],
                                       kind="snapshot", sha=snap["sha"])
    assert event["sha"] == snap["sha"]
    assert len(event["sha"]) == 40


@needs_db
def test_an_invented_event_kind_is_refused(project, tenant):
    """The vocabulary is closed. A timeline whose kinds are free text is prose
    with punctuation, which is what sec 9 refuses."""
    from services import worktree_records, worktrees

    allocated = worktrees.allocate_task_worktree("proj", "closed-vocab")
    with pytest.raises(ValueError):
        worktree_records.add_event(allocated["worktree_id"], kind="vibed")


@needs_db
def test_two_appends_both_survive(project, tenant):
    """The reason the timeline is a TABLE and not a JSON column on the record:
    a read-modify-write of an array loses one of two concurrent appends, and
    losing who took a tree from whom is not an acceptable failure in the design
    whose subject is two participants wanting the same tree."""
    from services import worktree_records, worktrees

    allocated = worktrees.allocate_task_worktree("proj", "two-writers")
    wt_id = allocated["worktree_id"]
    worktree_records.add_event(wt_id, kind="acquired", actor="agent:1")
    worktree_records.add_event(wt_id, kind="steered", actor="chat:2",
                               detail="also fix the typo")

    kinds = [e["kind"] for e in worktree_records.timeline(wt_id)]
    assert kinds == ["allocated", "acquired", "steered"]


# ---------------------------------------------------------------------------
# no database
# ---------------------------------------------------------------------------

def test_allocation_still_works_with_no_database(project, monkeypatch):
    """THE TEST THAT KEEPS THIS ADDITIVE.

    The sweep, the CLI and every agent path predate the record. Turning "no
    Postgres" into "no worktree" would break the very thing the record exists to
    describe, so an unconfigured store answers with no id and a working tree.
    """
    from services import tenancy, worktrees

    monkeypatch.setattr(tenancy, "configured", lambda: False)
    monkeypatch.setattr(tenancy, "available", lambda: False)

    result = worktrees.allocate_task_worktree("proj", "no-db-here")

    assert result["worktree_id"] is None
    assert Path(result["dir"]).is_dir()
    assert result["branch"] == "wt/proj/no-db-here"
    assert worktrees.worktree_record("proj", "no-db-here") is None

    # And the rest of the lifecycle is equally unbothered.
    _commit_in(Path(result["dir"]))
    merged = worktrees.merge_branch("proj", "no-db-here")
    assert merged["ok"], merged
    assert not Path(result["dir"]).exists()


def test_a_broken_store_does_not_fail_an_allocation(project, monkeypatch):
    """The same guarantee one level harder: the store is CONFIGURED and then
    raises. A bug in bookkeeping must not be able to fail an allocation."""
    from services import tenancy, worktree_records, worktrees

    monkeypatch.setattr(tenancy, "configured", lambda: True)
    monkeypatch.setattr(tenancy, "available", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("the record store fell over")

    monkeypatch.setattr(worktree_records, "record_allocation", _boom)

    result = worktrees.allocate_task_worktree("proj", "store-is-broken")
    assert result["worktree_id"] is None
    assert Path(result["dir"]).is_dir()
