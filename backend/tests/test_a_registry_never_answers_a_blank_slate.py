"""An unreadable registry must not read as an empty one.

MEASURED, TWICE. Both of this instance's file registries answered "nothing
here" for two different reasons and gave the caller the same value for both:

  * `.runtime/installed-apps.json` -- "Missing/corrupt file => [] (blank
    slate)", and "the read never raises". So the registry naming eight apps
    that the catalog could not build was shaped exactly like a fresh clone with
    none installed, and every app 404'd for two days with nothing logged.
  * `.runtime/projects.json` -- read behind `except (OSError, ValueError):
    return {}`, so a corrupt file made every app look unlinked: no working
    tree, no tickets, no runs, which is what an instance with no projects
    honestly looks like.

The rule this file pins is one sentence: an ABSENT registry is the blank slate
and must stay silent; a PRESENT one that cannot be understood is a fault and
must say so. A fresh clone has neither file, so nothing here makes it noisy --
which is the constraint `scripts/check_fresh_clone.sh` measures independently.

It names no app: every roster in it is written by the test.
"""
from __future__ import annotations

import json

import pytest


# --- the installed-apps registry ---------------------------------------------

def test_an_absent_installed_registry_is_a_blank_slate(monkeypatch, tmp_path):
    """The fresh-clone case, and the only one that may answer []."""
    from services import installed_apps

    monkeypatch.setattr(installed_apps, "REGISTRY_PATH", tmp_path / "nope.json")
    assert installed_apps.installed_app_ids() == []


def test_an_empty_installed_roster_is_still_a_blank_slate(monkeypatch, tmp_path):
    """`{"installed": []}` is somebody SAYING no apps, which is legitimate and
    must not be confused with the malformed case below."""
    from services import installed_apps

    p = tmp_path / "installed-apps.json"
    p.write_text(json.dumps({"installed": []}), encoding="utf-8")
    monkeypatch.setattr(installed_apps, "REGISTRY_PATH", p)
    assert installed_apps.installed_app_ids() == []


@pytest.mark.parametrize("body", [
    "{ this is not json",
    json.dumps({"apps": ["one"]}),      # right file, wrong key
    json.dumps({"installed": "one"}),   # right key, wrong type
    json.dumps(["one", "two"]),         # not an object at all
])
def test_a_present_but_unreadable_installed_registry_raises(
        monkeypatch, tmp_path, body):
    from services import installed_apps

    p = tmp_path / "installed-apps.json"
    p.write_text(body, encoding="utf-8")
    monkeypatch.setattr(installed_apps, "REGISTRY_PATH", p)
    with pytest.raises(installed_apps.RegistryUnreadable) as e:
        installed_apps.installed_app_ids()
    assert str(p) in str(e.value)


# --- the projects registry ----------------------------------------------------

def test_an_absent_projects_registry_is_a_blank_slate(monkeypatch, tmp_path):
    from services import app_links

    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(tmp_path / "nope.json"))
    assert app_links.load().projects == {}


def test_a_present_but_unreadable_projects_registry_raises(monkeypatch, tmp_path):
    from services import app_links

    p = tmp_path / "projects.json"
    p.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(p))
    with pytest.raises(app_links.RegistryUnreadable):
        app_links.load()


# --- installed, but resolving to nothing --------------------------------------

def test_an_installed_app_the_catalog_cannot_build_is_named(monkeypatch):
    """The state that cannot be an intentional configuration.

    An empty catalog is legitimate and an empty roster is legitimate; "the
    roster names apps and the catalog resolves none of them" is neither, and
    `installed_specs()` used to express it as an empty list -- the same value a
    correct fresh clone produces.
    """
    from services import app_registry, installed_apps

    monkeypatch.setattr(installed_apps, "installed_app_ids",
                        lambda: ["ghost-a", "ghost-b"])
    assert sorted(app_registry.unresolved_installed_ids()) == ["ghost-a", "ghost-b"]


def test_a_resolvable_roster_names_nothing_unresolved(monkeypatch):
    from services import app_registry, installed_apps

    known = sorted(app_registry.BUILTIN_APPS)
    if not known:
        pytest.skip("this instance ships no built-in apps to resolve against")
    monkeypatch.setattr(installed_apps, "installed_app_ids", lambda: known[:1])
    assert app_registry.unresolved_installed_ids() == []


def test_the_drop_is_logged_not_silent(monkeypatch, caplog):
    """`installed_specs` still drops the id -- there is no spec to return -- but
    a running instance now says so, rather than only a test nobody ran."""
    from services import app_registry, installed_apps

    monkeypatch.setattr(installed_apps, "installed_app_ids", lambda: ["ghost-a"])
    with caplog.at_level("WARNING"):
        assert app_registry.installed_specs() == []
    assert any("ghost-a" in r.getMessage() for r in caplog.records)
