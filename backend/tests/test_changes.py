"""The Change record: a branch that is ahead of its base becomes reviewable.

Step C1 of `docs/platform-redesign/changes-and-review-2026-08-22.md`. The claim
under test is the one the design turns on: a change is EVIDENCE, not an
assertion. So the tests drive real git -- a real project, a real worktree, real
commits -- rather than a mocked branch state, because a fixture that reports
"ahead by 1" without a commit behind it would pass while proving nothing, which
is precisely the failure mode the record exists to remove.

Needs the OWNER credential, not just the application one: the fixture mints a
throwaway organization, and creating a tenant is deliberately outside what the
application role may do (`db/engine.py`). Skipped without it, like the rest of
the db suite.
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

pytestmark = pytest.mark.skipif(
    not os.environ.get("WORKSHOP_DATABASE_ADMIN_URL"),
    reason="no WORKSHOP_DATABASE_ADMIN_URL; the fixture mints a throwaway org, "
           "which the application role deliberately cannot do",
)

pytest.importorskip("sqlalchemy")


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
    """A real git project with a registry entry, addressable as `proj`."""
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


def _branch_with_commits(project_dir: Path, slug: str, n: int = 1) -> Path:
    """A worktree-pinned branch carrying `n` real commits ahead of main."""
    from services import worktrees

    record = worktrees.create_branch("proj", slug)
    wt_dir = Path(record["dir"])
    for i in range(n):
        (wt_dir / f"file{i}.txt").write_text(f"change {i}\n")
        _git(wt_dir, "add", f"file{i}.txt")
        _git(wt_dir, "commit", "-m", f"add file{i}")
    return wt_dir


# ---------------------------------------------------------------------------
# tenant fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def tenant(monkeypatch):
    """A throwaway org/user, with `services.changes` pinned to it.

    `tenancy.org_and_user()` resolves the acting tenant from the request-scoped
    owner contextvar, which no test has. Pinning the three functions the
    service actually calls keeps `_require_db`'s fail-closed path exercised
    while pointing it at an organization that can be deleted afterwards.
    """
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
            user = UserAccount(id=uuid7(), email=f"changes-{tag}@example.test",
                               name="Changes test")
            s.add(user)
            s.flush()
            user_ids.append(user.id)
            for n in ("a", "b"):
                org = Organization(id=uuid7(),
                                   slug=f"{DB_ORG_PREFIX}changes-{tag}-{n}",
                                   name=f"Changes test {n}")
                s.add(org)
                s.flush()
                s.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
                org_ids.append(org.id)

        org_a, org_b = org_ids
        monkeypatch.setattr(tenancy, "configured", lambda: True)
        monkeypatch.setattr(tenancy, "available", lambda: True)
        monkeypatch.setattr(tenancy, "org_and_user", lambda: (org_a, user.id))
        yield {"org_a": org_a, "org_b": org_b, "user_id": user.id}
    finally:
        _delete_test_orgs(org_ids)
        _delete_test_users(user_ids)


# ---------------------------------------------------------------------------
# the opening condition
# ---------------------------------------------------------------------------

def test_a_branch_ahead_of_base_produces_a_change(project, tenant):
    from services import changes

    _branch_with_commits(project, "adds-a-file")
    change = changes.open_change("proj", "adds-a-file",
                                 title="Add a file", summary="claims a file")

    assert change["project_slug"] == "proj"
    assert change["branch_slug"] == "adds-a-file"
    assert change["base_ref"] == "main"
    assert change["status"] == "draft"
    assert change["title"] == "Add a file"
    # The sha is the branch's actual head, full length, not an abbreviation.
    head = _git(project, "rev-parse", "wt/proj/adds-a-file")
    assert change["head_sha"] == head
    assert len(change["head_sha"]) == 40
    # The slots exist and are empty, not absent.
    assert change["gates"] == []
    assert change["remote"] is None
    assert change["merged_sha"] is None


def test_a_branch_with_nothing_ahead_produces_no_change(project, tenant):
    """The opening condition, stated as a refusal.

    A change with an empty diff would be a record asserting work that does not
    exist -- the same unbacked claim `in_review` used to be.
    """
    from services import changes, worktrees

    worktrees.create_branch("proj", "empty-branch")
    with pytest.raises(changes.NothingToReview):
        changes.open_change("proj", "empty-branch")

    assert changes.change_for_branch("proj", "empty-branch") is None
    assert changes.list_changes() == []


def test_an_unknown_branch_is_not_a_change(project, tenant):
    from services import changes

    with pytest.raises(changes.BranchUnknown):
        changes.open_change("proj", "never-created")


def test_reopening_returns_the_same_change_at_the_new_head(project, tenant):
    """Idempotent: the sweep reaches this on every commit.

    The authored fields survive, because a reviewer may have edited them and a
    re-open that reverted them would lose that silently.
    """
    from services import changes

    wt_dir = _branch_with_commits(project, "moves-again")
    first = changes.open_change("proj", "moves-again", title="First title",
                                ticket_ids=["t-1"])

    (wt_dir / "second.txt").write_text("more\n")
    _git(wt_dir, "add", "second.txt")
    _git(wt_dir, "commit", "-m", "second")

    again = changes.open_change("proj", "moves-again", title="Ignored")
    assert again["id"] == first["id"]
    assert again["title"] == "First title"
    assert again["ticket_ids"] == ["t-1"]
    assert again["head_sha"] != first["head_sha"]
    assert again["head_sha"] == _git(project, "rev-parse", "wt/proj/moves-again")
    assert len(changes.list_changes()) == 1


# ---------------------------------------------------------------------------
# the payload
# ---------------------------------------------------------------------------

def test_the_diff_payload_comes_back(project, tenant):
    """`worktrees.diff_branch` IS the review payload (design doc sec 7); this
    proves the change hands back that shape rather than a second differ's."""
    from services import changes

    _branch_with_commits(project, "two-files", n=2)
    change = changes.open_change("proj", "two-files")

    payload = changes.diff_for(change["id"])
    assert payload["summary"]
    assert payload["summary"] != "no changes vs main"
    paths = sorted(f["path"] for f in payload["files"])
    assert paths == ["file0.txt", "file1.txt"]
    for f in payload["files"]:
        assert f["status"] == "A"
        assert "+change" in f["diff"]


def test_a_change_carries_several_tickets_and_they_round_trip(project, tenant):
    """A change may satisfy several tickets and merges as one unit (design doc
    sec 3). More than one, because a one-element list would pass against a
    scalar column."""
    from services import changes

    _branch_with_commits(project, "three-tickets")
    ids = ["a3f1c72b9e40", "b8d2e5a14c73", "d7b3e0c5f981"]
    change = changes.open_change("proj", "three-tickets", ticket_ids=ids)
    assert change["ticket_ids"] == ids

    fetched = changes.get_change(change["id"])
    assert fetched["ticket_ids"] == ids

    # And each one is findable by the ticket it carries -- the query the
    # status derivation (step C2) runs.
    for ticket_id in ids:
        found = changes.list_changes(ticket_id=ticket_id)
        assert [c["id"] for c in found] == [change["id"]]
    assert changes.list_changes(ticket_id="not-a-ticket") == []


def test_gates_and_remote_are_typed_slots_that_round_trip(project, tenant):
    from services import changes

    _branch_with_commits(project, "has-gates")
    change = changes.open_change("proj", "has-gates")

    patched = changes.update_change(
        change["id"],
        gates=[{"name": "pytest", "status": "pass", "detail": "3155 passed",
                "at": "2026-08-22T12:00:00+00:00"},
               {"name": "typecheck", "status": "fail", "detail": "1 error"}],
        remote={"kind": "github", "url": "https://example.test/pr/7",
                "number": 7})
    assert [g["name"] for g in patched["gates"]] == ["pytest", "typecheck"]
    assert patched["gates"][0]["status"] == "pass"
    assert patched["gates"][1]["detail"] == "1 error"
    assert patched["gates"][1]["at"] is None
    assert patched["remote"] == {"kind": "github",
                                 "url": "https://example.test/pr/7",
                                 "number": 7}

    with pytest.raises(ValueError):
        changes.update_change(change["id"],
                              gates=[{"name": "x", "status": "green"}])
    with pytest.raises(ValueError):
        changes.update_change(change["id"], gates=[{"status": "pass"}])


def test_the_merge_outcome_is_not_patchable(project, tenant):
    """Merging is step C5. Until something actually merges, a change cannot be
    told it is merged -- the row has to name the commit that did it."""
    from services import changes

    _branch_with_commits(project, "not-merged")
    change = changes.open_change("proj", "not-merged")
    for patch in ({"status": "merged"}, {"merged_sha": "0" * 40},
                  {"merged_by": "operator"}, {"merge_basis": "because"}):
        with pytest.raises(ValueError):
            changes.update_change(change["id"], **patch)


def test_refresh_follows_the_branch(project, tenant):
    from services import changes

    wt_dir = _branch_with_commits(project, "keeps-moving")
    change = changes.open_change("proj", "keeps-moving")

    (wt_dir / "later.txt").write_text("later\n")
    _git(wt_dir, "add", "later.txt")
    _git(wt_dir, "commit", "-m", "later")

    refreshed = changes.refresh_change(change["id"])
    assert refreshed["id"] == change["id"]
    assert refreshed["head_sha"] == _git(project, "rev-parse",
                                         "wt/proj/keeps-moving")


# ---------------------------------------------------------------------------
# tenancy
# ---------------------------------------------------------------------------

def test_a_change_in_one_org_is_invisible_to_another(project, tenant):
    """RLS, the same property `test_db_apps.py` pins for the app table."""
    from db.change_repo import get_change, list_changes
    from db.engine import tenant_session
    from services import changes

    _branch_with_commits(project, "org-scoped")
    change = changes.open_change("proj", "org-scoped")

    with tenant_session(tenant["org_b"], tenant["user_id"]) as s:
        assert get_change(s, tenant["org_b"], change["id"]) is None
        assert list_changes(s, tenant["org_b"]) == []


def test_no_database_is_reported_not_answered_empty(project, monkeypatch):
    """"Nothing is open for review" and "the store is down" must never read the
    same. `services/apps.py` makes the same call for the same reason."""
    from services import changes, tenancy

    monkeypatch.setattr(tenancy, "configured", lambda: False)
    with pytest.raises(changes.ChangesUnavailable):
        changes.list_changes()


# ---------------------------------------------------------------------------
# one branch, one open change
# ---------------------------------------------------------------------------

def test_the_routes_answer_with_what_they_declare(project, tenant):
    """The response models must MATCH what the handlers return.

    `check_response_model.py` sees that a model is named and
    `check_opaque_fields.py` sees that its fields are typed; neither can see
    the value. FastAPI validates on the way out and 500s on a mismatch, which
    is how `/chat/threads/{id}/events` took the chat surface down for seven
    minutes (scripts/check_routes_answer.py's own docstring). So the routes are
    driven here, in process, against a real change.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import changes as change_routes

    app = FastAPI()
    app.include_router(change_routes.router)
    client = TestClient(app)

    _branch_with_commits(project, "over-http", n=2)
    opened = client.post("/changes", json={
        "project_slug": "proj", "branch_slug": "over-http",
        "title": "Over HTTP", "summary": "two files",
        "ticket_ids": ["t-1", "t-2"]})
    assert opened.status_code == 200, opened.text
    change_id = opened.json()["id"]
    assert opened.json()["ticket_ids"] == ["t-1", "t-2"]

    assert client.get("/changes").json()["changes"][0]["id"] == change_id
    assert client.get(f"/changes/{change_id}").json()["head_sha"]

    diff = client.get(f"/changes/{change_id}/diff").json()
    assert sorted(f["path"] for f in diff["files"]) == ["file0.txt", "file1.txt"]

    patched = client.patch(f"/changes/{change_id}", json={
        "status": "ready",
        "gates": [{"name": "pytest", "status": "pass", "detail": "ok",
                   "at": "2026-08-22T12:00:00+00:00"}],
        "remote": {"kind": "github", "url": "https://example.test/pr/1",
                   "number": 1}})
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "ready"
    assert patched.json()["gates"][0]["name"] == "pytest"
    assert patched.json()["remote"]["number"] == 1

    assert client.post(f"/changes/{change_id}/refresh").status_code == 200
    assert client.delete(f"/changes/{change_id}").json() == {
        "ok": True, "id": change_id}
    assert client.get(f"/changes/{change_id}").status_code == 404


def test_the_routes_refuse_a_branch_with_nothing_ahead(project, tenant):
    """409, not 500 and not a silently-created empty change: the request was
    well formed and the answer is about the state of the repository."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import changes as change_routes
    from services import worktrees

    app = FastAPI()
    app.include_router(change_routes.router)
    client = TestClient(app)

    worktrees.create_branch("proj", "nothing-here")
    res = client.post("/changes", json={"project_slug": "proj",
                                        "branch_slug": "nothing-here"})
    assert res.status_code == 409, res.text
    assert client.post("/changes", json={"project_slug": "proj",
                                         "branch_slug": "no-such"}
                       ).status_code == 404


def test_a_deleted_change_frees_the_branch_to_open_a_new_one(project, tenant):
    """Soft delete keeps the row and releases the partial unique index, which
    is what "one branch produces one change" means in practice: one OPEN one."""
    from services import changes

    _branch_with_commits(project, "reusable")
    first = changes.open_change("proj", "reusable")
    assert changes.delete_change(first["id"]) is True
    assert changes.change_for_branch("proj", "reusable") is None

    second = changes.open_change("proj", "reusable")
    assert second["id"] != first["id"]


# --- C7: pre-approval, and who merged it ------------------------------------

def test_pre_approval_is_off_until_somebody_sets_it(project, tenant):
    """A change nobody armed never merges itself. The default is the whole
    safety property of step C7, so it is asserted on the stored row rather
    than trusted to a Python constant."""
    from services import changes

    _branch_with_commits(project, "not-armed")
    change = changes.open_change("proj", "not-armed")
    assert change["auto_merge"] is False
    assert change["merged_by"] is None and change["merge_basis"] is None

    armed = changes.update_change(change["id"], auto_merge=True)
    assert armed["auto_merge"] is True
    # and it can be taken back: pre-approval is a stored decision, not a
    # one-way switch.
    assert changes.update_change(change["id"], auto_merge=False)["auto_merge"] \
        is False


def test_a_merged_change_records_which_kind_of_decision_merged_it(project, tenant):
    """An auto-merge that reads the same as a human acceptance is a lie in the
    record. The actor and the basis land in the same transaction as the sha."""
    from services import changes

    _branch_with_commits(project, "auto-merged")
    change = changes.open_change("proj", "auto-merged")
    merged = changes.mark_merged(
        change["id"], "c0ffee0", merged_by="change_auto_merge",
        basis="every non-advisory gate passed")

    assert merged["status"] == "merged"
    assert merged["merged_by"] == "change_auto_merge"
    assert merged["merge_basis"] == "every non-advisory gate passed"


def test_a_merge_with_no_actor_named_is_the_operator(project, tenant):
    """Every caller that predates C7 meant a person, and says so now."""
    from services import changes

    _branch_with_commits(project, "hand-merged")
    change = changes.open_change("proj", "hand-merged")
    assert changes.mark_merged(change["id"], "c0ffee1")["merged_by"] == "operator"


def test_an_unknown_merge_actor_is_refused(project, tenant):
    """A closed vocabulary, refused in the service as well as in the schema:
    a merge whose attribution nothing can interpret is worse than none."""
    from services import changes

    _branch_with_commits(project, "who-merged-it")
    change = changes.open_change("proj", "who-merged-it")
    with pytest.raises(ValueError):
        changes.mark_merged(change["id"], "c0ffee2", merged_by="somebody")
