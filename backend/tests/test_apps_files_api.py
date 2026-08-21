"""The `/apps/{id}/files*` surface (app-is-the-entity-2026-08-20.md sec 8).

What has to be true:

  - an app linked to a real project tree lists its root, lists a subdirectory,
    reads a text file, and writes a file then reads it back;
  - `..`, an absolute path, and a symlink that resolves outside the app root
    are all refused for both read and write -- this is the one part of the
    feature that is a correctness requirement, not a nicety;
  - an app with NO file root gets an honest empty `200` with a `reason` for
    the tree listing, never a 404 and never another app's tree;
  - a binary file reports `editable: false` rather than failing the request.

Skipped when there is no database, like the other db-backed app tests
(the `app` row itself is Postgres; the tree/file access underneath it is
plain filesystem and is what is actually under test here).
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("WORKSHOP_DATABASE_URL"),
    reason="no WORKSHOP_DATABASE_URL; see infra/postgres/docker-compose.yml",
)

pytest.importorskip("sqlalchemy")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def org():
    from db.engine import owner_session
    from db.ids import uuid7
    from db.models import Organization

    from tests.conftest import DB_ORG_PREFIX, _delete_test_orgs

    tag = uuid.uuid4().hex[:8]
    oid = None
    try:
        with owner_session() as s:
            o = Organization(id=uuid7(), slug=f"{DB_ORG_PREFIX}appfiles-{tag}",
                             name="App files test")
            s.add(o)
            s.flush()
            oid = o.id
        yield oid
    finally:
        _delete_test_orgs([oid])


@pytest.fixture
def client(org, tmp_path, monkeypatch):
    from routes import apps as apps_routes
    from services import installed_apps, tenancy

    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setattr(installed_apps, "REGISTRY_PATH",
                        tmp_path / "installed-apps.json")

    monkeypatch.setattr(tenancy, "configured", lambda: True)
    monkeypatch.setattr(tenancy, "available", lambda: True)
    monkeypatch.setattr(tenancy, "org_and_user", lambda: (org, uuid.uuid4()))

    app = FastAPI()
    app.include_router(apps_routes.router)
    yield TestClient(app)


def _write_projects_registry(tmp_path, projects: dict) -> None:
    import json
    (tmp_path / ".runtime").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".runtime" / "projects.json").write_text(
        json.dumps({"projects": projects}))


class TestLinkedApp:
    @pytest.fixture(autouse=True)
    def _tree(self, tmp_path):
        proj_dir = tmp_path / "projects" / "widgetco"
        (proj_dir / "src").mkdir(parents=True)
        (proj_dir / "README.md").write_text("# Widgetco\n")
        (proj_dir / "src" / "main.py").write_text("print('hi')\n")
        (proj_dir / "node_modules").mkdir()
        (proj_dir / "node_modules" / "junk.js").write_text("noise\n")
        (proj_dir / "binary.dat").write_bytes(b"\x00\x01\x02binary\xffdata")
        _write_projects_registry(tmp_path, {
            "widgetco": {"title": "Widgetco", "dir": str(proj_dir)},
        })
        self.proj_dir = proj_dir

    def _app_id(self, client) -> str:
        r = client.post("/apps", json={"slug": "widgetco", "name": "Widgetco"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_lists_the_root(self, client):
        app_id = self._app_id(client)
        r = client.get(f"/apps/{app_id}/files")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["path"] == ""
        names = {e["name"]: e for e in body["entries"]}
        assert "README.md" in names
        assert names["README.md"]["kind"] == "file"
        assert "src" in names
        assert names["src"]["kind"] == "dir"
        # node_modules is filtered from the listing, and the filtering is
        # visible rather than silent.
        assert "node_modules" not in names
        assert body.get("filtered") == ["node_modules"]
        # dirs sorted before files
        kinds = [e["kind"] for e in body["entries"]]
        assert kinds == sorted(kinds)

    def test_lists_a_subdirectory(self, client):
        app_id = self._app_id(client)
        r = client.get(f"/apps/{app_id}/files", params={"path": "src"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["path"] == "src"
        assert [e["name"] for e in body["entries"]] == ["main.py"]

    def test_reads_a_text_file(self, client):
        app_id = self._app_id(client)
        r = client.get(f"/apps/{app_id}/files/content", params={"path": "README.md"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["content"] == "# Widgetco\n"
        assert body["editable"] is True
        assert body["encoding"] == "utf-8"
        assert body["truncated"] is False

    def test_writes_a_file_then_reads_it_back(self, client):
        app_id = self._app_id(client)
        r = client.put(f"/apps/{app_id}/files/content",
                       json={"path": "src/new_file.py", "content": "x = 1\n"})
        assert r.status_code == 200, r.text
        assert r.json()["path"] == "src/new_file.py"

        r = client.get(f"/apps/{app_id}/files/content", params={"path": "src/new_file.py"})
        assert r.status_code == 200
        assert r.json()["content"] == "x = 1\n"

        # And it shows up in the directory listing.
        r = client.get(f"/apps/{app_id}/files", params={"path": "src"})
        names = {e["name"] for e in r.json()["entries"]}
        assert "new_file.py" in names

    def test_binary_file_reports_not_editable_instead_of_erroring(self, client):
        app_id = self._app_id(client)
        r = client.get(f"/apps/{app_id}/files/content", params={"path": "binary.dat"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["editable"] is False
        assert body["content"] == ""
        assert body["encoding"] == "binary"

    def test_refuses_dotdot_escape_on_read(self, client):
        app_id = self._app_id(client)
        r = client.get(f"/apps/{app_id}/files/content", params={"path": "../outside.txt"})
        assert r.status_code == 400, r.text

    def test_refuses_dotdot_escape_on_write(self, client):
        app_id = self._app_id(client)
        r = client.put(f"/apps/{app_id}/files/content",
                       json={"path": "../../etc/evil.txt", "content": "x"})
        assert r.status_code == 400, r.text

    def test_refuses_absolute_path_on_read(self, client):
        app_id = self._app_id(client)
        r = client.get(f"/apps/{app_id}/files/content", params={"path": "/etc/passwd"})
        assert r.status_code == 400, r.text

    def test_refuses_absolute_path_on_write(self, client):
        app_id = self._app_id(client)
        r = client.put(f"/apps/{app_id}/files/content",
                       json={"path": "/etc/evil.txt", "content": "x"})
        assert r.status_code == 400, r.text

    def test_refuses_absolute_path_on_tree_listing(self, client):
        app_id = self._app_id(client)
        r = client.get(f"/apps/{app_id}/files", params={"path": "/etc"})
        assert r.status_code == 400, r.text

    def test_refuses_symlink_escape_on_read(self, client, tmp_path):
        outside = tmp_path / "outside_secret.txt"
        outside.write_text("do not read me\n")
        link = self.proj_dir / "escape_link"
        link.symlink_to(outside)

        app_id = self._app_id(client)
        r = client.get(f"/apps/{app_id}/files/content", params={"path": "escape_link"})
        assert r.status_code == 400, r.text

    def test_refuses_symlink_escape_on_write(self, client, tmp_path):
        outside_dir = tmp_path / "outside_dir"
        outside_dir.mkdir()
        link = self.proj_dir / "escape_dir_link"
        link.symlink_to(outside_dir)

        app_id = self._app_id(client)
        r = client.put(f"/apps/{app_id}/files/content",
                       json={"path": "escape_dir_link/evil.txt", "content": "x"})
        assert r.status_code == 400, r.text
        assert not (outside_dir / "evil.txt").exists()

    def test_refuses_symlink_escape_on_tree_listing(self, client, tmp_path):
        outside_dir = tmp_path / "outside_dir2"
        outside_dir.mkdir()
        (outside_dir / "secret.txt").write_text("shh\n")
        link = self.proj_dir / "escape_dir_link2"
        link.symlink_to(outside_dir)

        app_id = self._app_id(client)
        r = client.get(f"/apps/{app_id}/files", params={"path": "escape_dir_link2"})
        assert r.status_code == 400, r.text


class TestUnlinkedApp:
    def test_no_project_returns_empty_200_with_reason(self, client):
        r = client.post("/apps", json={"slug": "lonely", "name": "Lonely"})
        app_id = r.json()["id"]
        assert r.json()["project_slug"] is None

        r = client.get(f"/apps/{app_id}/files")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["entries"] == []
        assert body.get("reason")

    def test_no_project_read_content_is_404_not_500(self, client):
        r = client.post("/apps", json={"slug": "lonely2", "name": "Lonely 2"})
        app_id = r.json()["id"]
        r = client.get(f"/apps/{app_id}/files/content", params={"path": "anything.txt"})
        assert r.status_code == 404

    def test_no_project_write_is_404_not_500(self, client):
        r = client.post("/apps", json={"slug": "lonely3", "name": "Lonely 3"})
        app_id = r.json()["id"]
        r = client.put(f"/apps/{app_id}/files/content",
                       json={"path": "anything.txt", "content": "x"})
        assert r.status_code == 404


class TestUnknownApp:
    def test_files_routes_404_for_an_unknown_app(self, client):
        fake = str(uuid.uuid4())
        assert client.get(f"/apps/{fake}/files").status_code == 404
        assert client.get(f"/apps/{fake}/files/content", params={"path": "x"}).status_code == 404
        assert client.put(f"/apps/{fake}/files/content",
                          json={"path": "x", "content": "y"}).status_code == 404
