"""An app declares what it opens on and which tabs it has; the platform reads it.

Ticket 7d8d733a. Every app used to get the same eight rows and open on the
first of them, which is a chat list -- so an app with no bundled UI landed on a
generic chat list it never chose, beside tabs (Memory among them) that could
never hold anything for it. Reported at least four times over several chats;
three previous rounds of work improved how those tabs RENDER, which was never
the complaint.

The fix has to be a declaration and never a branch on an app id: CLAUDE.md and
`scripts/check_no_app_names.py` both forbid the second, and a branch would only
move the problem to the next app.
"""
from __future__ import annotations

import json

import pytest

from services import app_surface


@pytest.fixture()
def install(tmp_path, monkeypatch):
    """One app with a tree and a registry record, both hooked to tmp_path.

    Returns a function that (re)declares the app: `tree` is written into the
    app's own `workshop.app.json`, `record` into its project registry entry.
    """
    app_dir = tmp_path / "apps" / "widgets"
    app_dir.mkdir(parents=True)

    projects: dict = {"widgets": {"title": "Widgets", "dir": str(app_dir)}}

    from wsplatform import paths
    from services import app_files, app_links
    # The app declares its own root with the manifest below, so it is readable
    # whether or not the app is linked to a working tree.
    monkeypatch.setattr(paths, "scope_root",
                        lambda key: tmp_path / "apps" / key.split(":", 1)[1])
    monkeypatch.setattr(app_files, "project_root", lambda slug: app_dir)
    monkeypatch.setattr(app_links, "linked_project_slug",
                        lambda slug: "widgets" if slug == "widgets" else None)
    monkeypatch.setattr(app_links, "load",
                        lambda: app_links.AppRoster(installed=("widgets",),
                                                    projects=projects,
                                                    linked={"widgets": "widgets"}))

    def declare(*, tree: dict | None = None, record: dict | None = None) -> None:
        manifest = app_dir / "workshop.app.json"
        if tree is None:
            manifest.unlink(missing_ok=True)
        else:
            manifest.write_text(json.dumps({"name": "widgets", **tree}) + "\n")
        projects["widgets"].pop("surface", None)
        if record is not None:
            projects["widgets"]["surface"] = record

    declare()
    return declare


def test_with_no_declaration_it_takes_every_tab_it_can_have(install):
    s = app_surface.surface_for("widgets")
    assert s["tabs"] == list(app_surface.TAB_IDS)
    assert s["declared"] is False


def test_an_unlinked_app_loses_the_tabs_that_could_only_be_empty(install):
    # Nothing declared and no working tree: Work and Overview both resolve
    # through the link (services/apps.py), so for this app they can never be
    # anything but empty. They are not offered rather than offered and
    # apologised for.
    #
    # FILES IS NOT IN THAT CATEGORY and used to be dropped here too. Every app
    # has a file tree (app_files._app_own_root), so the only thing left that
    # turns on the link is Work and Overview.
    from services import app_links
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(app_links, "linked_project_slug", lambda slug: None)
        s = app_surface.surface_for("nothing-installed-here")
    assert s["tabs"] == ["chats", "memory", "files"]
    assert s["home"] == "/apps/nothing-installed-here/chats"


def test_a_manifest_declares_the_tabs_and_their_order(install):
    install(tree={"surface": {"tabs": ["memory", "chats"]}})
    s = app_surface.surface_for("widgets")
    assert s["tabs"] == ["memory", "chats"]
    assert s["declared"] is True
    # No home declared, so it opens on the first tab it named. Which is the
    # point: the app chose it.
    assert s["home"] == "/apps/widgets/memory"


def test_a_declaration_cannot_conjure_a_tab_the_app_cannot_hold(install):
    # This app owns a directory (so it keeps Files) but has no working tree, so
    # Deploys could only ever be empty for it. Asking for it does not get it:
    # that would put back the empty row this whole rule exists to remove.
    from services import app_links
    install(tree={"surface": {"tabs": ["chats", "deploys", "files"]}})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(app_links, "linked_project_slug", lambda slug: None)
        s = app_surface.surface_for("widgets")
    assert s["tabs"] == ["chats", "files"]


def test_the_registry_record_declares_it_for_a_tree_with_no_manifest(install):
    # A working tree the operator made has no manifest to carry the block and
    # no catalog entry either, so its declaration sits on the record beside the
    # title, icon and dir it already declares there.
    install(record={"home": "memory", "tabs": ["memory", "chats"]})
    s = app_surface.surface_for("widgets")
    assert s["tabs"] == ["memory", "chats"]
    assert s["home"] == "/apps/widgets/memory"


def test_the_manifest_outranks_the_registry_record(install):
    install(tree={"surface": {"tabs": ["chats"]}},
            record={"tabs": ["memory", "chats"]})
    assert app_surface.surface_for("widgets")["tabs"] == ["chats"]


def test_an_app_that_ships_screens_keeps_its_own_route_as_its_home(install):
    s = app_surface.surface_for("widgets", own_route="/apps/widgets")
    assert s["home"] == "/apps/widgets"
    assert s["declared"] is True


def test_a_surface_block_outranks_the_shipped_route(install):
    # The block is the more specific statement: an app may prefer a platform
    # tab to its own landing screen.
    install(tree={"surface": {"home": "memory"}})
    s = app_surface.surface_for("widgets", own_route="/apps/widgets")
    assert s["home"] == "/apps/widgets/memory"


def test_home_may_be_a_tab_id_a_relative_path_or_a_full_one(install):
    for declared, expected in [
        ("memory", "/apps/widgets/memory"),
        ("/memory", "/apps/widgets/memory"),
        ("/apps/widgets/files/src", "/apps/widgets/files/src"),
    ]:
        install(tree={"surface": {"home": declared}})
        assert app_surface.surface_for("widgets")["home"] == expected


def test_a_malformed_declaration_costs_itself_and_nothing_else(install):
    # Same posture as services/manifests.py: an app that cannot say what it
    # opens on gets the platform's answer, which is always a real place.
    install(record={"tabs": "not a list", "home": 7})
    s = app_surface.surface_for("widgets")
    assert s["tabs"] == list(app_surface.TAB_IDS)
    assert s["home"] == "/apps/widgets/work"


def test_an_app_opens_on_work_when_it_has_a_tree(install):
    # THE 2026-08-29 / 08-31 / 09-01 REPORT, as an assertion: an app with a
    # working tree opened on a chat list it never chose. Work is tabs[0] now,
    # and `_resolve_home` opens an app on tabs[0].
    install()
    s = app_surface.surface_for("widgets")
    assert s["tabs"][0] == "work"
    assert s["home"] == "/apps/widgets/work"


def test_a_declaration_of_only_retired_tabs_does_not_strand_the_app(install):
    # `{"tabs": ["tickets"]}` was a good declaration before Work absorbed
    # Tickets. Filtered against the current set it yields [], and an empty list
    # sends `_resolve_home` to the app's BASE path -- the redirect that lands
    # back on itself, so the app would never open at all.
    install(record={"tabs": ["tickets", "runs"]})
    s = app_surface.surface_for("widgets")
    assert s["tabs"] == list(app_surface.TAB_IDS)
    assert s["home"] == "/apps/widgets/work"


def test_a_declaration_keeps_its_live_tabs_when_only_some_retired(install):
    # Partial retirement is a narrowing, not a reset: the app still said Chats.
    install(record={"tabs": ["tickets", "chats"]})
    assert app_surface.surface_for("widgets")["tabs"] == ["chats"]


def test_a_partly_retired_declaration_keeps_its_live_tabs_and_says_so(install, caplog):
    # THE REAL CASE, measured on this installation 2026-09-01. An app declared
    # the tabs of the day; three of the five retired. The survivors are
    # non-empty, so nothing looked broken, and the app had quietly lost Work.
    install(record={"tabs": ["tickets", "runs", "files", "agents", "chats"]})
    import logging
    with caplog.at_level(logging.WARNING, logger="workshop.apps"):
        s = app_surface.surface_for("widgets")
    assert s["tabs"] == ["files", "chats"]
    assert "work" not in s["tabs"]
    # The point of the test: it is SAID.
    assert any("retired tab" in r.getMessage() for r in caplog.records), caplog.text
