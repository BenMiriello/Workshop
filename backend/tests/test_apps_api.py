"""The `/apps` REST surface, over the wire (app-is-the-entity-2026-08-20.md
stage B).

What has to be true:

  - an app row round-trips through the API (create/get/list/patch/delete);
  - a chat linked to an app (`chat_thread.app_id`) shows up under
    `GET /apps/{id}/chats`;
  - an app LINKED to a project (services.app_links) shows real tickets, notes,
    runs, deploys and agents once one of each is filed against it;
  - an app with NO linked project gets an honest empty `200` for every one of
    those, never a 404 and never another app's rows -- the specific failure
    mode this design has been burned by before;
  - `GET /apps/{unknown-id}/...` 404s (the app does not exist), which is a
    different answer from "the app exists and has nothing".

Skipped when there is no database, like the other db-backed tests.
"""
from __future__ import annotations

import asyncio
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
            o = Organization(id=uuid7(), slug=f"{DB_ORG_PREFIX}apps-{tag}",
                             name="Apps API test")
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

    # Every file registry this test touches (`.runtime/projects.json`,
    # `.runtime/installed-apps.json`, an app's knowledge dir) lives under a
    # throwaway tree, never the real installation's.
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setattr(installed_apps, "REGISTRY_PATH",
                        tmp_path / "installed-apps.json")

    # Bypass the ownership contextvar -> tenancy resolution chain (a legacy
    # int owner id through `.runtime/auth.db`): this test's caller is always
    # the throwaway `org`, which is the one fact `services.apps._require_db`
    # actually needs.
    monkeypatch.setattr(tenancy, "configured", lambda: True)
    monkeypatch.setattr(tenancy, "available", lambda: True)
    monkeypatch.setattr(tenancy, "org_and_user", lambda: (org, uuid.uuid4()))

    app = FastAPI()
    app.include_router(apps_routes.router)
    yield TestClient(app)


@pytest.fixture
def user(org):
    """A throwaway user_account, membered into `org`. User accounts are not
    cascaded from an org delete (test_db_chat.py's fixture comment explains
    why), so this cleans up its own row."""
    from db.engine import owner_session, tenant_session
    from db.ids import uuid7
    from db.models import Membership, UserAccount

    from tests.conftest import _delete_test_users

    tag = uuid.uuid4().hex[:8]
    uid = None
    try:
        with owner_session() as s:
            u = UserAccount(id=uuid7(), email=f"apps-test-{tag}@example.test")
            s.add(u)
            s.flush()
            uid = u.id
        with tenant_session(org) as s:
            s.add(Membership(org_id=org, user_id=uid, role="owner"))
        yield uid
    finally:
        _delete_test_users([uid])


def _write_projects_registry(tmp_path, projects: dict) -> None:
    import json
    (tmp_path / ".runtime").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".runtime" / "projects.json").write_text(
        json.dumps({"projects": projects}))


class TestTheEntity:
    def test_create_get_list_patch_delete_round_trip(self, client):
        r = client.post("/apps", json={"slug": "widget", "name": "Widget"})
        assert r.status_code == 201, r.text
        app = r.json()
        assert app["slug"] == "widget"
        assert app["kind"] == "app"
        app_id = app["id"]

        r = client.get(f"/apps/{app_id}")
        assert r.status_code == 200
        assert r.json()["name"] == "Widget"

        r = client.get("/apps")
        assert r.status_code == 200
        assert any(a["id"] == app_id for a in r.json()["apps"])

        r = client.patch(f"/apps/{app_id}", json={"name": "Widget Pro"})
        assert r.status_code == 200
        assert r.json()["name"] == "Widget Pro"

        r = client.patch(f"/apps/{app_id}", json={"archived": True})
        assert r.status_code == 200
        assert r.json()["archived_at"] is not None

        r = client.get("/apps", params={"archived": "true"})
        assert any(a["id"] == app_id for a in r.json()["apps"])
        r = client.get("/apps", params={"archived": "false"})
        assert not any(a["id"] == app_id for a in r.json()["apps"])

        r = client.delete(f"/apps/{app_id}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True
        assert client.get(f"/apps/{app_id}").status_code == 404

    def test_creating_a_duplicate_slug_conflicts(self, client):
        client.post("/apps", json={"slug": "dup", "name": "One"})
        r = client.post("/apps", json={"slug": "dup", "name": "Two"})
        assert r.status_code == 409

    def test_unknown_app_404s_everywhere(self, client):
        fake = str(uuid.uuid4())
        for path in ("", "/chats", "/tickets", "/notes", "/runs", "/deploys",
                     "/agents"):
            r = client.get(f"/apps/{fake}{path}")
            assert r.status_code == 404, f"{path}: {r.status_code} {r.text}"


class TestChats:
    def test_a_thread_linked_to_this_app_shows_up(self, client, org, user):
        from db import app_repo, chat_repo
        from db.engine import tenant_session

        r = client.post("/apps", json={"slug": "chatty", "name": "Chatty"})
        app_id = r.json()["id"]

        with tenant_session(org) as s:
            t = chat_repo.create_thread(s, org, user, context_key="chatty",
                                        scope="app:chatty", title="hello")
            app_repo.set_thread_app_id(s, org, t["id"], app_id)

        r = client.get(f"/apps/{app_id}/chats")
        assert r.status_code == 200
        chats = r.json()["chats"]
        assert len(chats) == 1
        assert chats[0]["title"] == "hello"

    def test_an_app_with_no_chats_gets_an_empty_200(self, client):
        r = client.post("/apps", json={"slug": "quiet", "name": "Quiet"})
        app_id = r.json()["id"]
        r = client.get(f"/apps/{app_id}/chats")
        assert r.status_code == 200
        assert r.json()["chats"] == []


class TestLinkedVsUnlinked:
    """One app linked to a real project (tickets/notes/runs/deploys/agents all
    have real data), one installed-only app with no project (all five must be
    an honest empty list)."""

    @pytest.fixture(autouse=True)
    def _registries(self, tmp_path):
        proj_dir = tmp_path / "projects" / "gizmo"
        proj_dir.mkdir(parents=True)
        _write_projects_registry(tmp_path, {
            "gizmo": {"title": "Gizmo", "dir": str(proj_dir)},
        })
        self.proj_dir = proj_dir

    def test_linked_app_shows_real_data_of_every_kind(self, client):
        from services import deploys, jobs, tasks

        r = client.post("/apps", json={"slug": "gizmo", "name": "Gizmo"})
        app_id = r.json()["id"]
        assert r.json()["project_slug"] == "gizmo"  # bare project-slug link

        tasks.add(self.proj_dir, title="Ship the gizmo", body="Objective: ship.")
        jobs.start(project_slug="gizmo", trigger="manual")
        deploys.create(project_slug="gizmo", name="prod",
                       target={"kind": "static"}, source={"kind": "git", "branch": "main"})

        agents_dir = self.proj_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "builder.md").write_text(
            "---\nname: Gizmo builder\nrole: maker\npurpose: ship it\n---\n"
            "Body.\n")

        asyncio.run(_put_note(f"app:gizmo"))

        assert client.get(f"/apps/{app_id}/tickets").json()["tickets"], "tickets"
        assert client.get(f"/apps/{app_id}/notes").json()["notes"], "notes"
        assert client.get(f"/apps/{app_id}/runs").json()["runs"], "runs"
        assert client.get(f"/apps/{app_id}/deploys").json()["deploys"], "deploys"
        assert client.get(f"/apps/{app_id}/agents").json()["agents"], "agents"

    def test_installed_only_app_is_honestly_empty_everywhere(self, client, monkeypatch):
        from services import installed_apps
        (installed_apps.REGISTRY_PATH.parent).mkdir(parents=True, exist_ok=True)
        installed_apps.REGISTRY_PATH.write_text('{"installed": ["lonely"]}')

        r = client.post("/apps", json={"slug": "lonely", "name": "Lonely"})
        app_id = r.json()["id"]
        assert r.json()["project_slug"] is None

        assert client.get(f"/apps/{app_id}/tickets").json() == {"tickets": []}
        assert client.get(f"/apps/{app_id}/notes").json() == {"notes": []}
        assert client.get(f"/apps/{app_id}/runs").json() == {"runs": []}
        assert client.get(f"/apps/{app_id}/deploys").json() == {"deploys": []}
        assert client.get(f"/apps/{app_id}/agents").json() == {"agents": []}
        # And never GIZMO's rows -- the specific failure mode this must not
        # repeat.
        for coll in ("tickets", "notes", "runs", "deploys", "agents"):
            assert client.get(f"/apps/{app_id}/{coll}").json()[coll] == []


async def _put_note(scope_key: str) -> None:
    from services import knowledge
    await knowledge.put_note(scope_key, "readme", "Readme", "Some content.")
