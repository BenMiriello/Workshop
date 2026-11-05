"""An app may open over any page: the `overlay` block of its manifest.

Pinned: the block parses tolerantly (no service, no block); the path is
rooted; a shortcut is kept only when the shell can bind it without firing
while the operator types; only RUNNING apps are listed, framed through the
runner's proxy on this origin.
"""
from __future__ import annotations

import pytest

from services import app_runner, overlay_apps


def test_a_full_block_parses():
    raw = {"overlay": {"service": "app", "path": "list", "title": " Board ",
                       "shortcut": "Mod + Alt + V"}}
    assert overlay_apps.parse_overlay(raw) == {
        "service": "app", "path": "/list", "title": "Board", "shortcut": "Mod+Alt+V"}


def test_defaults_fill_path_title_and_shortcut():
    assert overlay_apps.parse_overlay({"overlay": {"service": "web"}}) == {
        "service": "web", "path": "/", "title": None, "shortcut": None}


@pytest.mark.parametrize("raw", [
    None, {}, {"overlay": "app"}, {"overlay": {}}, {"overlay": {"path": "/"}},
    {"overlay": {"service": "a b"}},
])
def test_a_malformed_block_declares_nothing(raw):
    assert overlay_apps.parse_overlay(raw) is None


@pytest.mark.parametrize("chord", ["Mod+Alt+V", "Ctrl+Shift+K", "Cmd+1", "F7", "Shift+F2",
                                   "Alt+Space", "Mod+/"])
def test_bindable_shortcuts_are_kept(chord):
    assert overlay_apps.parse_shortcut(chord) == chord


@pytest.mark.parametrize("chord", ["V", "Shift+V", "Mod+", "Mod++", "Hyper+V", "Mod+Mod+V",
                                   "Mod+Alt+VV", "", 7, None])
def test_unbindable_shortcuts_are_dropped(chord):
    assert overlay_apps.parse_shortcut(chord) is None


def test_an_unreadable_shortcut_keeps_the_block():
    decl = overlay_apps.parse_overlay({"overlay": {"service": "app", "shortcut": "V"}})
    assert decl is not None and decl["shortcut"] is None


def test_only_running_apps_are_listed(monkeypatch):
    manifests = {
        "notes-x": {"name": "Notes X", "overlay": {"service": "app", "path": "/pick"}},
        "stopped": {"overlay": {"service": "app"}},
        "plain": {"name": "Plain"},
        "titled": {"overlay": {"service": "web", "title": "Alpha", "shortcut": "F8"},
                   "events": {"dictated": {"service": "web", "path": "/hooks/dictated"}}},
    }
    targets = {"notes-x": ("running", 1), "stopped": ("stopped", None),
               "plain": ("running", 2), "titled": ("running", 3)}
    monkeypatch.setattr(app_runner, "_STATE", {k: {} for k in manifests})
    monkeypatch.setattr(app_runner, "is_self_project", lambda slug: False)
    monkeypatch.setattr(app_runner, "_project_dir", lambda slug: slug)
    monkeypatch.setattr(app_runner, "_read_manifest_file", lambda d: manifests[d])
    monkeypatch.setattr(app_runner, "service_target", lambda slug, svc: targets[slug])

    assert overlay_apps.running_overlays() == [
        {"slug": "titled", "title": "Alpha", "shortcut": "F8",
         "url": "/api/base-apps/run/titled/proxy/web/", "events": ["dictated"]},
        {"slug": "notes-x", "title": "Notes X", "shortcut": None,
         "url": "/api/base-apps/run/notes-x/proxy/app/pick", "events": []},
    ]


def test_an_unreadable_manifest_skips_only_its_app(monkeypatch):
    def read(d):
        if d == "bad":
            raise app_runner.AppError("malformed")
        return {"overlay": {"service": "app"}}

    monkeypatch.setattr(app_runner, "_STATE", {"bad": {}, "good": {}})
    monkeypatch.setattr(app_runner, "is_self_project", lambda slug: False)
    monkeypatch.setattr(app_runner, "_project_dir", lambda slug: slug)
    monkeypatch.setattr(app_runner, "_read_manifest_file", read)
    monkeypatch.setattr(app_runner, "service_target", lambda slug, svc: ("running", 1))
    assert [o["slug"] for o in overlay_apps.running_overlays()] == ["good"]
