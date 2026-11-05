"""An app's served screens live inside the app and it opens on the first one.

docs/platform-redesign/app-screens-2026-09-23.md. A screen is a service the
app's manifest marks `expose`; the app lists them first in its sidebar and
opens on the first, and a process-mode app that should be running is started
again when the backend comes up.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import app_links, app_runner, app_surface


@pytest.fixture()
def app_tree(tmp_path, monkeypatch):
    """One linked app whose manifest the test writes."""
    app_dir = tmp_path / "widgets"
    app_dir.mkdir()
    monkeypatch.setattr(app_links, "linked_project_slug",
                        lambda slug: "widgets" if slug == "widgets" else None)
    monkeypatch.setattr(app_runner, "_project_dir", lambda slug: app_dir)
    monkeypatch.setattr(app_runner, "is_self_project", lambda slug: False)
    monkeypatch.setattr(app_surface, "declared_surface", lambda slug: None)

    def write(services: list[dict] | None) -> None:
        manifest = app_dir / "workshop.app.json"
        if services is None:
            manifest.unlink(missing_ok=True)
        else:
            manifest.write_text(json.dumps({"name": "widgets", "services": services}))

    return write


def _svc(name: str, **kw) -> dict:
    return {"name": name, "start": "true", **kw}


class TestScreens:
    def test_every_exposed_service_is_a_screen_in_manifest_order(self, app_tree):
        app_tree([_svc("api"), _svc("store", expose=True, label="Store"),
                  _svc("admin", expose=True)])
        assert app_surface.app_screens("widgets") == [
            {"service": "store", "label": "Store"},
            {"service": "admin", "label": "admin"},
        ]

    def test_a_disabled_service_is_not_a_screen(self, app_tree):
        app_tree([_svc("web", expose=True), _svc("old", expose=True, enabled=False)])
        assert [s["service"] for s in app_surface.app_screens("widgets")] == ["web"]

    def test_no_manifest_means_no_screen_even_if_a_server_is_detectable(self, app_tree):
        app_tree(None)
        assert app_surface.app_screens("widgets") == []

    def test_the_platforms_own_tree_has_no_screen(self, app_tree, monkeypatch):
        app_tree([_svc("web", expose=True)])
        monkeypatch.setattr(app_runner, "is_self_project", lambda slug: True)
        assert app_surface.app_screens("widgets") == []

    def test_an_unlinked_app_has_no_screen(self, app_tree):
        assert app_surface.app_screens("nothing-linked") == []


class TestWhereAnAppOpens:
    def test_it_opens_on_its_first_screen(self, app_tree):
        app_tree([_svc("api"), _svc("web", expose=True)])
        s = app_surface.surface_for("widgets")
        assert s["home"] == "/apps/widgets/ui/web"
        assert s["screens"] == [{"service": "web", "label": "web"}]

    def test_a_bundled_screen_is_more_specific(self, app_tree):
        app_tree([_svc("web", expose=True)])
        s = app_surface.surface_for("widgets", own_route="/apps/widgets")
        assert s["home"] == "/apps/widgets"

    def test_a_declared_home_is_more_specific(self, app_tree, monkeypatch):
        app_tree([_svc("web", expose=True)])
        monkeypatch.setattr(app_surface, "declared_surface", lambda slug: {"home": "chats"})
        assert app_surface.surface_for("widgets")["home"] == "/apps/widgets/chats"

    def test_without_a_screen_it_opens_on_its_first_tab(self, app_tree):
        app_tree(None)
        s = app_surface.surface_for("widgets")
        assert s["screens"] == []
        assert s["home"] == f"/apps/widgets/{s['tabs'][0]}"


class TestRestoreOnStartup:
    @pytest.fixture()
    def state(self, monkeypatch):
        st: dict = {}
        started: list[str] = []
        monkeypatch.setattr(app_runner, "_STATE", st)
        monkeypatch.setattr(app_runner, "_alive", lambda pid: pid == 1)
        monkeypatch.setattr(app_runner, "start_app", lambda slug: started.append(slug))
        monkeypatch.setattr(app_runner, "_adopt_untracked", lambda slug, app: None)
        return st, started

    def test_a_dead_process_app_that_should_run_is_started(self, state):
        st, started = state
        st["a"] = {"desired": "running", "backend": "subprocess",
                   "services": {"web": {"pid": 999}}}
        assert app_runner.restore_desired_running() == ["a"]
        assert started == ["a"]

    def test_stopped_container_and_alive_apps_are_left_alone(self, state):
        st, started = state
        st["stopped"] = {"desired": "stopped", "backend": "subprocess",
                         "services": {"web": {"pid": 999}}}
        st["boxed"] = {"desired": "running", "backend": "container", "services": {}}
        st["alive"] = {"desired": "running", "backend": "subprocess",
                       "services": {"api": {"pid": 999, "status": "failed"},
                                    "web": {"pid": 1, "status": "running"}}}
        assert app_runner.restore_desired_running() == []
        assert started == []


class TestARestartDuringABringUpStandsDown:
    """At boot the restore's start_app is still building when the monitor sees
    the service `failed`. A restart then spawned a second copy onto the port the
    bring-up was about to use, and the service ended parked as crashlooping
    while an orphan the runner no longer tracked served the port."""

    @pytest.fixture()
    def spawned(self, monkeypatch):
        calls: list[str] = []
        manifest = app_runner.AppManifest(name="a", services=[app_runner.ServiceSpec(
            name="web", start="serve", port=8702)])
        monkeypatch.setattr(app_runner, "resolve_manifest", lambda slug: manifest)
        monkeypatch.setattr(app_runner, "_project_dir", lambda slug: Path("/nowhere"))
        monkeypatch.setattr(app_runner, "_spawn_service",
                            lambda *a, **k: calls.append("spawn") or 4242)
        monkeypatch.setattr(app_runner, "app_status", lambda slug: {"slug": slug})
        monkeypatch.setattr(app_runner, "_persist_state", lambda: None)
        monkeypatch.setattr(app_runner, "_reflect_primary_surface", lambda *a: None)
        monkeypatch.setattr(app_runner.port_registry, "allocate", lambda *a, **k: 8702)
        monkeypatch.setattr(app_runner.port_registry, "set_pid", lambda *a: None)
        monkeypatch.setattr(app_runner, "_STATE", {"a": {
            "slug": "a", "phase": "up", "services": {"web": {"status": "failed"}}}})
        monkeypatch.setattr(app_runner, "_LOCKS", {})
        monkeypatch.setattr(app_runner, "_evict_squatter",
                            lambda slug, svc, port: calls.append(f"evict:{port}"))
        return calls

    def test_it_does_not_spawn_while_the_bring_up_holds_the_lock(self, spawned):
        with app_runner._slug_lock("a"):
            app_runner.restart_service("a", "web")
        assert spawned == []

    def test_it_does_not_spawn_before_the_bring_up_takes_the_lock(self, spawned):
        app_runner._STATE["a"]["phase"] = "starting"
        app_runner.restart_service("a", "web")
        assert spawned == []

    def test_with_no_bring_up_it_clears_its_pinned_port_and_spawns(self, spawned):
        app_runner.restart_service("a", "web")
        assert spawned == ["evict:8702", "spawn"]

    def test_a_bring_up_that_raises_does_not_stay_starting(self, monkeypatch):
        monkeypatch.setattr(app_runner, "_persist_state", lambda: None)
        monkeypatch.setattr(app_runner, "_STATE", {"a": {"phase": "starting"}})

        def boom(slug, manifest):
            raise RuntimeError("bring-up died")

        with pytest.raises(RuntimeError):
            app_runner._run_bring_up(boom, "a", None)
        assert app_runner._STATE["a"]["phase"] == "failed"
