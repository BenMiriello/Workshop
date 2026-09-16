"""The `/apps` REST surface, over the wire (app-is-the-entity-2026-08-20.md
stage B).

What has to be true:

  - an app row round-trips through the API (create/get/list/patch/delete);
  - a chat linked to an app (`chat_thread.app_id`) shows up under
    `GET /apps/{id}/chats`;
  - an app LINKED to a project (services.app_links) shows real tickets, notes,
    runs and agents once one of each is filed against it;
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
        for path in ("", "/chats", "/tickets", "/notes", "/runs", "/agents"):
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

    def test_a_chat_created_in_an_apps_scope_needs_no_backfill(
            self, client, org, user, tmp_path, monkeypatch):
        """THE DEFECT THIS CLOSES. `GET /apps/{id}/chats` selects on the
        `chat_thread.app_id` COLUMN, and nothing on the write path ever set it:
        the only writer in the tree was `db/app_backfill.py`, reachable solely
        by hand. So a chat opened in a project or app scope was missing from
        its own app's Chats tab until somebody remembered to run a CLI.

        Nothing in this test runs the backfill. The chat is created and
        mirrored exactly as a live one is -- the file store writes it, then
        `chat_branches._sync_record` (what `record_message` drives) turns the
        first message into the row -- and it must be on the tab straight after.
        """
        from services import chat_branches, chat_log, chat_store, ownership

        _write_projects_registry(tmp_path, {
            "gizmo": {"title": "Gizmo", "dir": str(tmp_path / "gizmo")},
        })
        app_id = client.post(
            "/apps", json={"slug": "gizmo", "name": "Gizmo"}).json()["id"]

        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
        ownership.set_current_owner(1)
        try:
            # A PROJECT-SCOPED chat, which is the spelling the defect was
            # reported about: `project:<slug>` is what the project view files
            # a conversation under, and an app owns its project's scope.
            tid = chat_store.create_thread(
                "project:gizmo", title="opened from the project")["id"]
            chat_branches._sync_record(
                org, user, tid,
                {"kind": "message", "role": "user", "content": "hi",
                 "id": "ev-1"},
                chat_log.read_meta(tid))
            chats = client.get(f"/apps/{app_id}/chats").json()["chats"]
        finally:
            ownership.clear_current_owner()

        assert [c["id"] for c in chats] == [tid]
        assert chats[0]["title"] == "opened from the project"

    def test_a_chat_in_a_scope_no_app_owns_stays_unlinked(
            self, client, org, user, tmp_path, monkeypatch):
        """NULL is the right answer, not a failure. An unbound `general` chat
        belongs to no app, and the rule must not hand it to whichever app
        happens to fall through `scope_for_context`'s catch-all."""
        from db import app_repo
        from db.engine import tenant_session
        from services import chat_branches, chat_log, chat_store, ownership

        _write_projects_registry(tmp_path, {
            "gadget": {"title": "Gadget", "dir": str(tmp_path / "gadget")},
        })
        app_id = client.post(
            "/apps", json={"slug": "gadget", "name": "Gadget"}).json()["id"]

        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
        ownership.set_current_owner(1)
        try:
            tid = chat_store.create_thread("general", title="unbound")["id"]
            chat_branches._sync_record(
                org, user, tid,
                {"kind": "message", "role": "user", "content": "hi",
                 "id": "ev-1"},
                chat_log.read_meta(tid))
            assert client.get(f"/apps/{app_id}/chats").json()["chats"] == []
        finally:
            ownership.clear_current_owner()

        with tenant_session(org) as s:
            assert app_repo.app_id_for_context(s, org, "general") is None

    def test_an_app_with_no_chats_gets_an_empty_200(self, client):
        r = client.post("/apps", json={"slug": "quiet", "name": "Quiet"})
        app_id = r.json()["id"]
        r = client.get(f"/apps/{app_id}/chats")
        assert r.status_code == 200
        assert r.json()["chats"] == []


class TestLinkedVsUnlinked:
    """One app linked to a real project (tickets/notes/runs/agents all have
    real data), one installed-only app with no project (all four must be an
    honest empty list)."""

    @pytest.fixture(autouse=True)
    def _registries(self, tmp_path):
        proj_dir = tmp_path / "projects" / "gizmo"
        proj_dir.mkdir(parents=True)
        _write_projects_registry(tmp_path, {
            "gizmo": {"title": "Gizmo", "dir": str(proj_dir)},
        })
        self.proj_dir = proj_dir

    def test_linked_app_shows_real_data_of_every_kind(self, client):
        from services import jobs, tasks

        r = client.post("/apps", json={"slug": "gizmo", "name": "Gizmo"})
        app_id = r.json()["id"]
        assert r.json()["project_slug"] == "gizmo"  # bare project-slug link

        tasks.add(self.proj_dir, title="Ship the gizmo", body="Objective: ship.")
        jobs.start(project_slug="gizmo", trigger="manual")

        agents_dir = self.proj_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "builder.md").write_text(
            "---\nname: Gizmo builder\nrole: maker\npurpose: ship it\n---\n"
            "Body.\n")

        asyncio.run(_put_note(f"app:gizmo"))

        assert client.get(f"/apps/{app_id}/tickets").json()["tickets"], "tickets"
        assert client.get(f"/apps/{app_id}/notes").json()["notes"], "notes"
        assert client.get(f"/apps/{app_id}/runs").json()["runs"], "runs"
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
        assert client.get(f"/apps/{app_id}/agents").json() == {"agents": []}
        # And never GIZMO's rows -- the specific failure mode this must not
        # repeat.
        for coll in ("tickets", "notes", "runs", "agents"):
            assert client.get(f"/apps/{app_id}/{coll}").json()[coll] == []


async def _put_note(scope_key: str) -> None:
    from services import knowledge
    await knowledge.put_note(scope_key, "readme", "Readme", "Some content.")


class TestTheTableTracksTheFileRegistries:
    """`GET /apps` reconciles the table from the two file registries first.

    Before this, `app` rows were written by a person running
    `python -m db.app_backfill --apply` and by nothing else: an app installed or
    a project created after that run simply had no row, `/apps` did not list it,
    and nothing anywhere reported the gap -- a stale roster is shaped exactly
    like a current one.
    """

    def _registries(self, tmp_path, installed=(), projects=None):
        import json as _json
        (tmp_path / ".runtime").mkdir(parents=True, exist_ok=True)
        (tmp_path / "installed-apps.json").write_text(
            _json.dumps({"installed": list(installed)}), encoding="utf-8")
        (tmp_path / ".runtime" / "projects.json").write_text(
            _json.dumps({"projects": projects or {}}), encoding="utf-8")

    def test_a_project_created_after_the_last_backfill_is_listed(
            self, client, tmp_path):
        self._registries(tmp_path, projects={"newly-made": {"title": "Newly Made"}})
        slugs = [a["slug"] for a in client.get("/apps").json()["apps"]]
        assert "newly-made" in slugs

    def test_a_departed_app_is_archived_and_keeps_its_id(self, client, tmp_path):
        """Archived, never deleted: the row is what `chat_thread.app_id` points
        at, and re-adding the app must not mint a second identity for it."""
        self._registries(tmp_path, projects={"here-then-gone": {"title": "Here"}})
        first = [a for a in client.get("/apps").json()["apps"]
                 if a["slug"] == "here-then-gone"][0]

        self._registries(tmp_path, projects={})
        again = [a for a in client.get("/apps?archived=true").json()["apps"]
                 if a["slug"] == "here-then-gone"]
        assert again and again[0]["archived_at"], "should be archived, not gone"
        assert again[0]["id"] == first["id"]

        # ...and coming back restores the SAME row.
        self._registries(tmp_path, projects={"here-then-gone": {"title": "Here"}})
        back = [a for a in client.get("/apps").json()["apps"]
                if a["slug"] == "here-then-gone"][0]
        assert back["id"] == first["id"]
        assert back["archived_at"] is None

    def test_a_renamed_app_is_not_re_derived_back(self, client, tmp_path):
        """"A slug is a label, never an identity" (design doc sec 4.1), so a
        rename is a PATCH. A reconcile that re-derived the slug from the
        registry would undo it on the very next request, and matching by slug
        alone would read the rename as a departure plus an arrival -- archiving
        the row the app's chats point at and minting a new id for the same app.
        """
        self._registries(tmp_path, projects={"old-name": {"title": "Old Name"}})
        before = [a for a in client.get("/apps").json()["apps"]
                  if a["slug"] == "old-name"][0]

        renamed = client.patch(f"/apps/{before['id']}",
                               json={"slug": "new-name", "name": "New Name"})
        assert renamed.status_code == 200, renamed.text

        after = client.get("/apps").json()["apps"]
        rows = [a for a in after if a["id"] == before["id"]]
        assert rows, "the renamed app must survive the reconcile"
        assert rows[0]["slug"] == "new-name"
        assert rows[0]["name"] == "New Name"
        assert rows[0]["archived_at"] is None
        assert not [a for a in after if a["slug"] == "old-name"], \
            "the registry slug must not resurrect as a second row"
