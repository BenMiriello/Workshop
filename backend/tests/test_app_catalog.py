"""The app catalog: read from a git repo, install, turn on and off, defaults once.

Pinned: catalog.json parses tolerantly; the catalog is fetched with git from
the URL the installation declares (a local repo here, so no network); install
clones and registers with the catalog's provenance, and an app already in the
projects folder is adopted rather than cloned over; a default is applied once,
so an app turned off or removed stays off.
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from routes import projects as projects_routes
from services import app_catalog, app_catalog_apply, app_runner
from services import apps as apps_svc
from services.app_catalog import Entry

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def _repo(path, files: dict[str, str]) -> str:
    path.mkdir(parents=True)
    for name, text in files.items():
        (path / name).write_text(text)
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=path, env=GIT_ENV, check=True)
    return path.as_uri()


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A runtime dir, a projects folder and registry, an app repo, and a
    catalog repo listing it as a default. Runner start/stop are recorded."""
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / "runtime"))
    monkeypatch.setenv("WORKSHOP_PROJECTS_ROOT_NEW", str(tmp_path / "projects"))
    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(tmp_path / "projects.json"))
    monkeypatch.setattr(apps_svc, "reconcile_from_registries", lambda: {"ok": True})
    app_repo = _repo(tmp_path / "src" / "jotter", {"workshop.app.json": "{}"})
    catalog = {"apps": [
        {"slug": "jotter", "name": "Jotter", "repo": app_repo, "default": True},
        {"slug": "extra", "name": "Extra", "repo": app_repo + "-missing"},
    ]}
    url = _repo(tmp_path / "src" / "catalog", {"catalog.json": json.dumps(catalog)})
    monkeypatch.setenv("WORKSHOP_APP_CATALOG_URL", url)
    monkeypatch.setattr(app_catalog, "_last_fetch", {})
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(app_runner, "start_app", lambda s: calls.append(("start", s)) or {})
    monkeypatch.setattr(app_runner, "stop_app", lambda s: calls.append(("stop", s)) or {})
    monkeypatch.setattr(app_runner, "wants_running", lambda s: ("start", s) in calls[-1:])
    monkeypatch.setattr(app_runner, "app_status", lambda s: {"phase": "stopped"})
    return {"tmp": tmp_path, "url": url, "calls": calls}


def test_parse_keeps_valid_entries_once():
    raw = {"apps": [
        {"slug": "One", "name": "One", "repo": "https://x/one.git", "default": True},
        {"slug": "one", "repo": "https://x/again.git"},
        {"slug": "bad slug", "repo": "https://x/b.git"},
        {"slug": "norepo"},
        "junk",
        {"slug": "two", "repo": "git@x:two.git", "default": "yes"},
    ]}
    assert app_catalog.parse_catalog(raw) == [
        Entry("one", "One", "https://x/one.git", "", True),
        Entry("two", "two", "git@x:two.git", "", False),
    ]
    assert app_catalog.parse_catalog([{"slug": "a", "repo": "r"}])[0].slug == "a"
    assert app_catalog.parse_catalog({"apps": "x"}) == []


def test_no_catalog_set_lists_nothing(monkeypatch):
    monkeypatch.delenv("WORKSHOP_APP_CATALOG_URL", raising=False)
    assert app_catalog.entries() == ([], None)


def test_the_catalog_is_fetched_from_its_repo(world):
    items, error = app_catalog.entries()
    assert error is None
    assert [e.slug for e in items] == ["jotter", "extra"]


def test_applied_set_is_remembered(world):
    assert app_catalog.applied() == set()
    app_catalog.mark_applied("b")
    app_catalog.mark_applied("a")
    app_catalog.mark_applied("a")
    assert app_catalog.applied() == {"a", "b"}


def test_pending_defaults_skip_applied_and_non_default():
    items = [Entry("a", "A", "r", default=True), Entry("b", "B", "r", default=True),
             Entry("c", "C", "r")]
    assert app_catalog_apply.pending_defaults(items, {"b"}) == [items[0]]


def test_defaults_install_and_start_once(world):
    result = app_catalog_apply.apply_defaults()
    assert result["started"] == ["jotter"] and result["failed"] == {}
    target = world["tmp"] / "projects" / "jotter"
    assert (target / "workshop.app.json").exists()
    rec = projects_routes._load_registry()["projects"]["jotter"]
    assert rec["dir"] == str(target) and rec["title"] == "Jotter"
    assert rec["from_catalog"]["catalog"] == world["url"]
    assert world["calls"] == [("start", "jotter")]

    app_catalog_apply.set_on("jotter", False)
    assert app_catalog_apply.apply_defaults()["started"] == []
    assert world["calls"] == [("start", "jotter"), ("stop", "jotter")]


def test_an_app_already_there_is_adopted_not_cloned(world):
    target = world["tmp"] / "projects" / "jotter"
    target.mkdir(parents=True)
    (target / "mine.txt").write_text("kept")
    app_catalog_apply.install(app_catalog_apply.find("jotter"))
    assert (target / "mine.txt").read_text() == "kept"
    assert not (target / ".git").exists()
    rec = projects_routes._load_registry()["projects"]["jotter"]
    assert rec["from_catalog"]["repo"].endswith("jotter")


def test_a_registered_app_gains_provenance_only(world):
    projects_routes._save_registry({"projects": {"jotter": {"title": "Mine", "dir": "/x"}}})
    app_catalog_apply.install(app_catalog_apply.find("jotter"))
    rec = projects_routes._load_registry()["projects"]["jotter"]
    assert rec["title"] == "Mine" and rec["dir"] == "/x" and "from_catalog" in rec


def test_a_failed_clone_leaves_nothing_and_stays_unapplied(world):
    with pytest.raises(app_catalog_apply.CatalogError):
        app_catalog_apply.set_on("extra", True)
    assert not (world["tmp"] / "projects" / "extra").exists()
    assert "extra" not in app_catalog.applied()


def test_listing_reports_installed_and_on(world):
    app_catalog_apply.set_on("jotter", True)
    rows = {r["slug"]: r for r in app_catalog_apply.listing()["apps"]}
    assert rows["jotter"]["installed"] and rows["jotter"]["on"]
    assert not rows["extra"]["installed"] and not rows["extra"]["on"]
