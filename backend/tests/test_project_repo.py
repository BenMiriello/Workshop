"""An app's GitHub repository: adoption of the one an older app already has,
the managed switch, pushing without ever forcing, and app creation that cannot
leave a folder behind with no app when GitHub fails."""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from routes import projects as P
from services import github, github_identity, project_repo

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]


def _git(cwd, *args):
    return subprocess.run([*GIT, "-C", str(cwd), *args], capture_output=True, text=True, check=True)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """A registered app `widgets` whose `origin` reads as a github.com repo and
    pushes to a local bare repository."""
    bare = tmp_path / "remote.git"
    subprocess.run([*GIT, "init", "-q", "--bare", str(bare)], check=True)
    d = tmp_path / "projects" / "widgets"
    d.mkdir(parents=True)
    _git(d, "init", "-q", "-b", "main")
    (d / "a.txt").write_text("a\n")
    _git(d, "add", ".")
    _git(d, "commit", "-q", "-m", "one")
    _git(d, "remote", "add", "origin", "https://github.com/octo/widgets.git")
    _git(d, "config", "remote.origin.pushurl", str(bare))
    _git(d, "push", "-q", "origin", "main")
    _git(d, "update-ref", "refs/remotes/origin/main", "main")

    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"projects": {"widgets": {
        "title": "Widgets", "dir": str(d), "purpose": "", "state": "sketch",
        "created": "2026-01-01T00:00:00+00:00", "last_touched": "2026-01-01T00:00:00+00:00",
    }}}))
    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(registry))
    monkeypatch.setenv("WORKSHOP_RUNTIME_DIR", str(tmp_path / "runtime"))
    from services import apps as apps_svc
    monkeypatch.setattr(apps_svc, "reconcile_from_registries", lambda: {"ok": False, "reason": "stubbed"})
    monkeypatch.setattr(project_repo, "_connected_login", lambda: "octo")
    monkeypatch.setattr(project_repo, "_status_path", lambda: tmp_path / "repo-push.json")
    return d, bare, registry


def _row(registry):
    return json.loads(registry.read_text())["projects"]["widgets"]


def test_an_older_apps_repository_on_the_connected_account_is_adopted_as_managed(app):
    d, _, registry = app
    out = project_repo.describe("widgets", fetch=False)
    assert out["connected"] and out["managed"] and out["created_by"] == "workshop"
    assert (out["owner"], out["name"]) == ("octo", "widgets")
    assert _row(registry)["repo"]["managed"] is True


def test_a_repository_on_another_account_is_adopted_as_linked(app, monkeypatch):
    monkeypatch.setattr(project_repo, "_connected_login", lambda: "someone-else")
    out = project_repo.describe("widgets", fetch=False)
    assert out["connected"] and not out["managed"] and out["created_by"] == "link"


def test_push_sends_new_commits_and_reports_ok(app):
    d, bare, _ = app
    (d / "b.txt").write_text("b\n")
    _git(d, "add", ".")
    _git(d, "commit", "-q", "-m", "two")
    assert project_repo.describe("widgets", fetch=False)["ahead"] == 1

    entry = project_repo.push("widgets")

    assert entry["state"] == "ok"
    remote_head = subprocess.run(["git", "-C", str(bare), "rev-parse", "main"],
                                 capture_output=True, text=True).stdout.strip()
    assert remote_head == entry["sha"]
    assert project_repo.describe("widgets", fetch=False)["push"]["state"] == "ok"


def test_a_remote_with_commits_this_app_lacks_is_diverged_and_never_forced(app, tmp_path):
    d, bare, _ = app
    other = tmp_path / "other"
    subprocess.run([*GIT, "clone", "-q", str(bare), str(other)], check=True)
    (other / "theirs.txt").write_text("x\n")
    _git(other, "add", ".")
    _git(other, "commit", "-q", "-m", "theirs")
    _git(other, "push", "-q", "origin", "main")
    theirs = _git(other, "rev-parse", "HEAD").stdout.strip()
    (d / "mine.txt").write_text("y\n")
    _git(d, "add", ".")
    _git(d, "commit", "-q", "-m", "mine")

    entry = project_repo.push("widgets")

    assert entry["state"] == "diverged"
    assert subprocess.run(["git", "-C", str(bare), "rev-parse", "main"],
                          capture_output=True, text=True).stdout.strip() == theirs


def test_turning_management_off_is_kept(app):
    project_repo.describe("widgets", fetch=False)
    out = project_repo.set_managed("widgets", False)
    assert out["managed"] is False
    assert project_repo.describe("widgets", fetch=False)["managed"] is False


def test_the_app_is_found_from_a_chat_worktree(app, tmp_path):
    d, _, _ = app
    wt = tmp_path / "wt"
    _git(d, "worktree", "add", "-q", "-b", "chat-x", str(wt))
    assert project_repo._slug_for(str(wt)) == "widgets"
    assert project_repo._slug_for(str(d)) == "widgets"


# ---- creating an app ----------------------------------------------------------

@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"projects": {}}))
    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(registry))
    monkeypatch.setattr(P, "_projects_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(P, "_assert_project_ignored", lambda slug: None)
    from services import apps as apps_svc
    monkeypatch.setattr(apps_svc, "reconcile_from_registries", lambda: {"ok": False, "reason": "stubbed"})
    monkeypatch.setattr(github, "reject_blocked", lambda *a: None)
    monkeypatch.setattr(github, "unique_repo_name", lambda base: base)
    monkeypatch.setattr(github_identity, "connected", lambda owner_id=None: True)
    monkeypatch.setattr(github_identity, "defaults", lambda owner_id=None: {
        "auto_create_repo": True, "default_visibility": "private", "default_branch_name": "trunk"})
    return tmp_path, registry


def _create(slug="gadgets", **kw):
    return asyncio.run(P.create_project(P.CreateProjectIn(slug=slug, title="Gadgets", **kw)))


def test_a_failed_repository_keeps_the_app_and_says_why(fresh, monkeypatch):
    tmp_path, registry = fresh

    def boom(dir, name, visibility="private"):
        raise ValueError("gh repo create failed: network")
    monkeypatch.setattr(github, "create_repo", boom)

    out = _create()

    row = json.loads(registry.read_text())["projects"]["gadgets"]
    assert "network" in (out.repo_error or "") and "network" in row["repo_error"]
    assert out.repo is None and (tmp_path / "projects" / "gadgets").is_dir()


def test_a_new_app_follows_the_settings_defaults_and_starts_managed(fresh, monkeypatch):
    tmp_path, registry = fresh
    seen = {}

    def made(dir, name, visibility="private"):
        seen.update(name=name, visibility=visibility)
        return {"owner": "octo", "name": name, "remote_url": f"https://github.com/octo/{name}.git",
                "default_branch": "trunk"}
    monkeypatch.setattr(github, "create_repo", made)

    out = _create()

    assert seen["visibility"] == "private"
    assert out.repo.managed is True and out.repo.created_by == "workshop"
    head = subprocess.run(["git", "-C", str(tmp_path / "projects" / "gadgets"),
                           "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
    assert head.stdout.strip() == "trunk"


def test_no_repository_when_github_is_not_connected(fresh, monkeypatch):
    monkeypatch.setattr(github_identity, "connected", lambda owner_id=None: False)
    monkeypatch.setattr(github, "create_repo", lambda *a, **k: pytest.fail("must not create"))
    out = _create()
    assert out.repo is None and not out.repo_error


def test_a_retry_binds_the_origin_a_failed_create_left_instead_of_making_another(fresh, monkeypatch):
    tmp_path, registry = fresh
    monkeypatch.setattr(github, "create_repo", lambda *a, **k: (_ for _ in ()).throw(ValueError("push failed")))
    _create()
    d = tmp_path / "projects" / "gadgets"
    _git(d, "remote", "add", "origin", "https://github.com/octo/gadgets.git")
    monkeypatch.setattr(github, "create_repo", lambda *a, **k: pytest.fail("must bind, not create"))
    monkeypatch.setattr(github, "bind_repo", lambda dir, url: {
        "owner": "octo", "name": "gadgets", "remote_url": url, "default_branch": "trunk"})

    raw = P._provision_repo("gadgets", scratch=True)

    assert raw["repo"]["name"] == "gadgets" and "repo_error" not in raw
