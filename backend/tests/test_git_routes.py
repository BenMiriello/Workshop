"""`routes/git.py` -- the git resource answers, and its declared models do not
drop fields.

Two things are worth a live request rather than a direct call to the handler.

1. FastAPI FILTERS every return value through `response_model` and silently
   drops any key the model does not name. A handler that returns the service's
   dict verbatim therefore looks correct in isolation and loses fields over the
   wire; that is the exact defect ADR 0023 clause 6 exists for. So these go
   through the app.
2. The design rule in section 13.2 is that every router declares a prefix and a
   tag ("a bare `APIRouter()` is a bug"), and that the resource is addressable
   on its own rather than as a side effect of another resource. That is an
   assertion about the router object, and it is cheap to keep honest.

Auth is NOT exercised here: the router carries none of its own, exactly like the
other platform-control routers, and `main.py` mounts it with the operator
dependency alongside them.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import git as git_routes  # noqa: E402
from services import git_service  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(git_routes.router)
    return TestClient(app)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True,
                   capture_output=True)
    (path / "README.md").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True,
                   capture_output=True)
    assert git_service.commit(path, "initial")["ok"]
    return path


class TestTheRouterIsAResource:
    def test_it_declares_a_prefix_and_a_tag(self):
        assert git_routes.router.prefix == "/git"
        assert git_routes.router.tags == ["git"]

    def test_every_route_declares_a_response_model(self):
        for route in git_routes.router.routes:
            assert route.response_model is not None, f"{route.path} has no model"


class TestReads:
    def test_status_of_a_clean_then_dirty_tree(self, client, repo):
        r = client.get("/git/status", params={"repo": str(repo)})
        assert r.status_code == 200
        assert r.json() == {"repo": str(repo), "branch": "main", "clean": True,
                            "entries": []}

        (repo / "new.txt").write_text("new\n", encoding="utf-8")
        body = client.get("/git/status", params={"repo": str(repo)}).json()
        assert body["clean"] is False
        assert body["entries"] == [{"status": "??", "path": "new.txt"}]

    def test_log_carries_every_declared_field(self, client, repo):
        body = client.get("/git/log", params={"repo": str(repo)}).json()
        assert body["repo"] == str(repo)
        entry = body["commits"][0]
        assert entry["subject"] == "initial"
        assert entry["author"] == git_service.IDENTITY_NAME
        assert len(entry["sha"]) == 40
        assert entry["date"]

    def test_diff_omits_the_patch_unless_it_was_asked_for(self, client, repo):
        subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feature"],
                       check=True, capture_output=True)
        (repo / "f.txt").write_text("f\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                       capture_output=True)
        git_service.commit(repo, "feature commit")

        params = {"repo": str(repo), "a": "main", "b": "feature", "three_dot": True}
        body = client.get("/git/diff", params=params).json()
        assert body["files"] == [{"status": "A", "path": "f.txt"}]
        assert "1 file changed" in body["summary"]
        assert "patch" not in body

        body = client.get("/git/diff", params={**params, "unified": True}).json()
        assert "+f" in body["patch"]

    def test_ahead_behind_and_is_ancestor(self, client, repo):
        first = git_service.rev_parse(repo, "HEAD")
        (repo / "b.txt").write_text("b\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                       capture_output=True)
        second = git_service.commit(repo, "second")["commit"]

        ab = client.get("/git/ahead-behind",
                        params={"repo": str(repo), "upstream": first}).json()
        assert ab == {"ahead": 1, "behind": 0}

        anc = client.get("/git/is-ancestor",
                         params={"repo": str(repo), "ancestor": first,
                                 "descendant": second}).json()
        assert anc["is_ancestor"] is True

    def test_rev_parse_reports_a_missing_ref_as_null(self, client, repo):
        body = client.get("/git/rev-parse",
                          params={"repo": str(repo), "rev": "nope"}).json()
        assert body == {"repo": str(repo), "rev": "nope", "sha": None}


class TestWrites:
    def test_branches_create_list_delete(self, client, repo):
        assert client.post("/git/branches", json={"repo": str(repo), "name": "topic"}
                           ).json() == {"repo": str(repo), "branch": "topic"}
        listed = client.get("/git/branches", params={"repo": str(repo)}).json()
        assert sorted(listed["branches"]) == ["main", "topic"]
        assert client.request("DELETE", "/git/branches",
                              params={"repo": str(repo), "name": "topic"}
                              ).json() == {"ok": True}

    def test_creating_an_existing_branch_is_a_400_with_gits_words(self, client, repo):
        client.post("/git/branches", json={"repo": str(repo), "name": "topic"})
        r = client.post("/git/branches", json={"repo": str(repo), "name": "topic"})
        assert r.status_code == 400
        assert "topic" in r.json()["detail"]

    def test_worktree_add_list_and_remove(self, client, repo, tmp_path):
        target = tmp_path / "wt" / "one"
        added = client.post("/git/worktrees", json={
            "repo": str(repo), "path": str(target), "branch": "wt/one",
        })
        assert added.status_code == 200
        assert added.json()["branch"] == "wt/one"

        listed = client.get("/git/worktrees", params={"repo": str(repo)}).json()
        record = next(w for w in listed["worktrees"] if w["branch"] == "wt/one")
        assert record["locked"] is False
        assert record["head"]

        assert client.request("DELETE", "/git/worktrees",
                              params={"repo": str(repo), "path": str(target)}
                              ).json() == {"ok": True}
        assert client.post("/git/worktrees/prune", json={"repo": str(repo)}
                           ).json() == {"ok": True}

    def test_a_conflicting_merge_answers_200_with_the_conflict(self, client, repo):
        subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feature"],
                       check=True, capture_output=True)
        (repo / "README.md").write_text("feature\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                       capture_output=True)
        git_service.commit(repo, "feature edits")
        subprocess.run(["git", "-C", str(repo), "checkout", "main"], check=True,
                       capture_output=True)
        (repo / "README.md").write_text("main\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                       capture_output=True)
        git_service.commit(repo, "main edits")

        r = client.post("/git/merge", json={"repo": str(repo), "ref": "feature"})

        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["conflict"] is True
        assert body["conflicts"] == ["README.md"]
        # The failure is reported, not left behind: the tree is clean again.
        assert client.get("/git/status", params={"repo": str(repo)}).json()["clean"]

    def test_snapshot_then_clean_is_the_quiesce_sequence(self, client, repo):
        (repo / "README.md").write_text("half-applied\n", encoding="utf-8")
        (repo / "scratch.txt").write_text("junk\n", encoding="utf-8")
        head = git_service.rev_parse(repo, "HEAD")

        snap = client.post("/git/snapshot", json={
            "repo": str(repo), "ref": "refs/wip/session-1",
        }).json()
        assert snap["ok"] is True
        assert snap["ref"] == "refs/wip/session-1"
        assert snap["clean"] is False
        assert git_service.rev_parse(repo, "HEAD") == head

        assert client.post("/git/checkout", json={"repo": str(repo), "ref": "HEAD",
                                                  "force": True}).json()["ok"]
        cleaned = client.post("/git/clean", json={"repo": str(repo)}).json()
        assert cleaned["removed"] == ["scratch.txt"]
        assert client.get("/git/status", params={"repo": str(repo)}).json()["clean"]
        # The snapshot still holds the work that was cleared off the tree.
        assert "half-applied" in git_service.diff(repo, "HEAD", snap["sha"])

    def test_commit_through_the_route(self, client, repo):
        (repo / "c.txt").write_text("c\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                       capture_output=True)
        body = client.post("/git/commit", json={"repo": str(repo),
                                                "message": "via the route"}).json()
        assert body["ok"] is True
        assert len(body["commit"]) == 40
        assert git_service.log(repo)[0]["subject"] == "via the route"


class TestTheRepoArgumentIsChecked:
    def test_a_missing_directory_is_404(self, client, tmp_path):
        r = client.get("/git/status", params={"repo": str(tmp_path / "nope")})
        assert r.status_code == 404

    def test_a_directory_that_is_not_a_repo_is_400(self, client, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        r = client.get("/git/status", params={"repo": str(plain)})
        assert r.status_code == 400
        assert "not a git repository" in r.json()["detail"]

    def test_repo_is_required(self, client):
        assert client.get("/git/status").status_code == 422
