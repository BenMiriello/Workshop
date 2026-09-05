"""The runtime app loader's server half: an app appears and disappears without
anything being rebuilt.

WHAT THIS PINS, and it is one property in three places. Installing an app used
to require rebuilding the platform's frontend bundle, because the shell
discovered apps with `import.meta.glob`, which Vite resolves at BUILD time. The
shell now reads `GET /base-apps/contributions`, so the whole question is whether
that endpoint answers from live state. `test_an_app_appears_and_disappears`
below is that question: one process, no restart, no build, roster edited between
two requests.

It names no app: every fixture app here is a fictional id written by the test.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import base_apps as base_apps_routes  # noqa: E402
from services import app_ui, installed_apps  # noqa: E402

SLUG = "sample-widget"


def _write_app(root: Path, slug: str = SLUG, *, ui: dict | None = None,
               entry: str = "console.log(1)", css: str | None = ".x{color:red}",
               extra_css: dict[str, str] | None = None) -> Path:
    """A minimal installed app on disk: a manifest and a built bundle."""
    app_dir = root / "apps" / slug
    build = app_dir / "dist" / "ui"
    build.mkdir(parents=True, exist_ok=True)
    (build / "app.js").write_text(entry, encoding="utf-8")
    if css is not None:
        (build / "app.css").write_text(css, encoding="utf-8")
    for name, body in (extra_css or {}).items():
        (build / name).write_text(body, encoding="utf-8")
    block = ui if ui is not None else {
        "contract": 1,
        "kit": "*",
        "dir": "dist/ui",
        "entry": "app.js",
        "styles": ["app.css"],
        "icon": "sliders",
        "link": {"label": "Sample", "to": f"/apps/{slug}"},
        "pages": [{
            "path": f"/apps/{slug}",
            "mount": "app.route",
            "export": "Home",
            "crumb": {"label": "Sample", "parent": "/apps"},
        }],
    }
    (app_dir / "workshop.app.json").write_text(
        json.dumps({"name": slug, "ui": block}), encoding="utf-8")
    return app_dir


@pytest.fixture()
def instance(tmp_path, monkeypatch):
    """An installation with its own root, its own installed-apps roster and its
    own build stamp -- which is where the kit version an app declares a range
    against comes from."""
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    registry = tmp_path / "installed-apps.json"
    monkeypatch.setattr(installed_apps, "REGISTRY_PATH", registry)
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "build-info.json").write_text(json.dumps({
        "source_id": "test", "kit": "0.7.9", "react": "18.3.1", "router": "6.30.4",
    }), encoding="utf-8")
    return tmp_path


@pytest.fixture()
def client(instance) -> TestClient:
    app = FastAPI()
    app.include_router(base_apps_routes.router, prefix="/base-apps")
    return TestClient(app)


class TestAFreshInstanceIsBlank:
    def test_nothing_installed_is_an_empty_list_not_an_error(self, client):
        r = client.get("/base-apps/contributions")
        assert r.status_code == 200
        assert r.json()["apps"] == []

    def test_the_shell_says_what_runtime_it_publishes(self, client):
        r = client.get("/base-apps/runtime")
        assert r.status_code == 200
        body = r.json()
        assert body["contract"] == app_ui.CONTRACT
        # Read from the BUILD STAMP, so the answer describes the bundle being
        # served rather than whatever is in node_modules right now.
        assert body["kit"] == "0.7.9"
        assert body["react"] == "18.3.1"
        assert body["router"] == "6.30.4"


class TestTheLoaderSeam:
    def test_an_app_appears_and_disappears(self, client, instance):
        """THE WHOLE POINT, as one assertion. No rebuild, no restart, no
        process boundary: the same client answers differently either side of a
        roster edit, which is what "installed at runtime" has to mean."""
        _write_app(instance)

        assert client.get("/base-apps/contributions").json()["apps"] == []

        installed_apps.install(SLUG)
        listed = client.get("/base-apps/contributions").json()["apps"]
        assert [a["id"] for a in listed] == [SLUG]
        entry_url = listed[0]["urls"]["entry"]
        assert entry_url and entry_url.startswith(f"/base-apps/ui/{SLUG}/app.js?v=")
        assert listed[0]["error"] is None

        # And the bytes are actually served, which is the other half of "it
        # appeared": a declaration whose bundle 404s is not an installed app.
        asset = client.get(entry_url.replace("/base-apps", "/base-apps", 1))
        assert asset.status_code == 200
        assert asset.text == "console.log(1)"
        assert asset.headers["content-type"].startswith("text/javascript")

        installed_apps.remove(SLUG)
        assert client.get("/base-apps/contributions").json()["apps"] == []
        # A removed app stops being reachable immediately, files or no files.
        assert client.get(f"/base-apps/ui/{SLUG}/app.js").status_code == 404

    def test_the_url_changes_when_the_app_is_rebuilt(self, client, instance):
        """The hash is of the file's own bytes, computed per read, so an app
        that rebuilds is picked up with no manifest edit and no shell rebuild."""
        app_dir = _write_app(instance)
        installed_apps.install(SLUG)
        first = client.get("/base-apps/contributions").json()["apps"][0]["urls"]["entry"]
        (app_dir / "dist" / "ui" / "app.js").write_text("console.log(2)", encoding="utf-8")
        second = client.get("/base-apps/contributions").json()["apps"][0]["urls"]["entry"]
        assert first != second

    def test_the_declaration_is_readable_without_running_app_code(self, client, instance):
        """Declare-then-activate: the launcher, the crumb, the icon and the
        switcher link are all present before any bundle is fetched."""
        _write_app(instance)
        installed_apps.install(SLUG)
        app = client.get("/base-apps/contributions").json()["apps"][0]
        assert app["icon"] == "sliders"
        assert app["link"] == {"label": "Sample", "to": f"/apps/{SLUG}"}
        assert app["pages"][0]["crumb"] == {"label": "Sample", "parent": "/apps"}
        assert app["pages"][0]["mount"] == "app.route"


class TestItRefusesBeforeItFetches:
    def test_a_contract_mismatch_is_named_not_rendered(self, client, instance):
        _write_app(instance, ui={"contract": 99, "kit": "*", "pages": []})
        installed_apps.install(SLUG)
        app = client.get("/base-apps/contributions").json()["apps"][0]
        assert "contract 99" in (app["error"] or "")
        assert app["urls"]["entry"] is None

    def test_a_kit_range_the_shell_cannot_satisfy_is_named(self, client, instance):
        _write_app(instance, ui={"contract": 1, "kit": "^99.0", "pages": []})
        installed_apps.install(SLUG)
        app = client.get("/base-apps/contributions").json()["apps"][0]
        assert "^99.0" in (app["error"] or "")

    def test_an_undeclared_stylesheet_is_an_error_not_a_bare_page(self, client, instance):
        """Trap B from the platform's side. An app built with cssCodeSplit on
        emits chunk stylesheets the shell will never link, and the page renders
        unstyled on the BUILT origin only. Say so instead."""
        _write_app(instance, extra_css={"Chunk.css": ".y{}"})
        installed_apps.install(SLUG)
        app = client.get("/base-apps/contributions").json()["apps"][0]
        assert "Chunk.css" in (app["error"] or "")

    def test_an_app_with_no_build_says_so(self, client, instance):
        app_dir = _write_app(instance)
        for f in (app_dir / "dist" / "ui").iterdir():
            f.unlink()
        (app_dir / "dist" / "ui").rmdir()
        installed_apps.install(SLUG)
        app = client.get("/base-apps/contributions").json()["apps"][0]
        assert "no built UI" in (app["error"] or "") or "not built" in (app["error"] or "")

    def test_a_headless_app_contributes_no_screens_and_no_error(self, client, instance):
        _write_app(instance, ui={"contract": 1, "kit": "*", "headless": True, "pages": []})
        installed_apps.install(SLUG)
        app = client.get("/base-apps/contributions").json()["apps"][0]
        assert app["headless"] is True
        assert app["pages"] == []
        assert app["error"] is None


class TestTheAssetRouteIsContained:
    def test_a_traversal_out_of_the_build_dir_is_refused(self, client, instance):
        _write_app(instance)
        installed_apps.install(SLUG)
        (instance / "apps" / SLUG / "secret.txt").write_text("no", encoding="utf-8")
        r = client.get(f"/base-apps/ui/{SLUG}/../../secret.txt")
        assert r.status_code == 404

    def test_only_javascript_and_css_are_served(self, client, instance):
        app_dir = _write_app(instance)
        (app_dir / "dist" / "ui" / "notes.txt").write_text("hi", encoding="utf-8")
        installed_apps.install(SLUG)
        assert client.get(f"/base-apps/ui/{SLUG}/notes.txt").status_code == 404
        assert client.get(f"/base-apps/ui/{SLUG}/app.css").status_code == 200

    def test_an_app_that_is_not_installed_serves_nothing(self, client, instance):
        _write_app(instance)
        assert client.get(f"/base-apps/ui/{SLUG}/app.js").status_code == 404


class TestTheKitRangeRule:
    """`^0.7` excludes 0.8.0, which is why adding a kit export is a fleet-wide
    event and why the shell publishes host wiring outside the kit."""

    @pytest.mark.parametrize("version,spec,ok", [
        ("0.7.9", "^0.7", True),
        ("0.7.9", "^0.7.2", True),
        ("0.8.0", "^0.7", False),
        ("0.7.1", "^0.7.2", False),
        ("1.2.0", "^1.0", True),
        ("2.0.0", "^1.0", False),
        ("0.7.9", "~0.7.2", True),
        ("0.8.1", "~0.7.2", False),
        ("0.7.9", ">=0.7", True),
        ("0.6.0", ">=0.7", False),
        ("0.7.9", "0.7", True),
        ("0.7.9", "*", True),
        ("0.7.9", "", True),
        # A range this does not parse must not take an installed app off the
        # screen: rendering it is the better of the two failures.
        ("0.7.9", "workshop-latest", True),
    ])
    def test_ranges(self, version, spec, ok):
        assert app_ui.kit_satisfies(version, spec) is ok
