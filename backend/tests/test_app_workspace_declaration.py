"""An app declares which directory it is; the platform reads the declaration.

Two candidate directories exist for one app while the registries are unmerged
(app-is-the-entity-2026-08-20.md sec 9): `apps/<slug>` and `projects/<slug>`.
Which one actually holds the app is NOT uniform, measured 2026-08-23:

    birds      apps/ 30150 files   projects/    41   (a scaffold)
    gengui     apps/     0         projects/  1754
    commute    apps/     3         projects/    10   (split across both)

`_resolve_app` used to say "an app's files ARE its linked project's working
tree", which left the largest app unreachable from its own Files tab. An
emptiness heuristic gets `commute` wrong the other way, so the app has to say,
and it says it with a manifest in its own tree.
"""
from __future__ import annotations

import pytest

from services import app_files


@pytest.fixture()
def trees(tmp_path, monkeypatch):
    app_dir = tmp_path / "apps" / "widgets"
    (app_dir / "src").mkdir(parents=True)
    (app_dir / "src" / "main.py").write_text("print('hi')\n")
    proj_dir = tmp_path / "projects" / "widgets"
    proj_dir.mkdir(parents=True)
    (proj_dir / "PROJECT.md").write_text("# scaffold\n")

    from wsplatform import paths
    monkeypatch.setattr(paths, "scope_root", lambda key: tmp_path / "apps" / key.split(":", 1)[1])
    monkeypatch.setattr(app_files, "project_root", lambda slug: tmp_path / "projects" / slug)
    from services import app_links
    monkeypatch.setattr(app_links, "linked_project_slug", lambda slug: slug)
    return app_dir, proj_dir


def test_without_a_manifest_it_is_the_linked_project_tree(trees):
    _app_dir, proj_dir = trees
    assert app_files.resolve("app:widgets").root == proj_dir


def test_a_manifest_in_the_app_tree_makes_that_tree_the_workspace(trees):
    app_dir, _proj_dir = trees
    (app_dir / "workshop.app.json").write_text('{"name": "widgets"}\n')
    assert app_files.resolve("app:widgets").root == app_dir


def test_a_jsonc_manifest_counts_too(trees):
    app_dir, _proj_dir = trees
    (app_dir / "workshop.app.jsonc").write_text('// a comment\n{"name": "widgets"}\n')
    assert app_files.resolve("app:widgets").root == app_dir


def test_an_empty_app_dir_is_not_a_claim(trees):
    # Existence is not a declaration: apps/<slug> is created by other machinery
    # and an empty one must not capture the app away from its real tree.
    app_dir, proj_dir = trees
    for child in list(app_dir.iterdir()):
        if child.is_dir():
            for f in child.iterdir():
                f.unlink()
            child.rmdir()
    assert app_files.resolve("app:widgets").root == proj_dir


def test_a_malformed_manifest_does_not_break_browsing(trees):
    # app_runner raises on a bad manifest where it is deploying. Here that must
    # not take out file browsing: fall back rather than 500.
    app_dir, proj_dir = trees
    (app_dir / "workshop.app.json").write_text("{not json\n")
    assert app_files.resolve("app:widgets").root == proj_dir
