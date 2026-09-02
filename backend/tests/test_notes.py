"""Tests for the Notes app: the vault provider (services/notes_vault) and its
HTTP surface (routes/notes), plus catalog registration.

The router is exercised through a bare FastAPI app (no auth deps — those attach
at catalog-mount time in main.py), with the vault rooted at a tmp dir via the
WORKSHOP_NOTES_VAULT env override.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import notes as notes_routes  # noqa: E402
from services import app_registry, notes_vault  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_NOTES_VAULT", str(tmp_path))
    vault = tmp_path
    (vault / "projects").mkdir()
    (vault / "index.md").write_text(
        "# Index\nlinks to [[projects/alpha]] and [[beta|the beta note]]\n", encoding="utf-8"
    )
    (vault / "projects" / "alpha.md").write_text("# Alpha\nback to [[index]]\n", encoding="utf-8")
    (vault / "beta.md").write_text(
        "# Beta\nmentions searchable-needle and [[projects/alpha#notes]]\n", encoding="utf-8"
    )
    app = FastAPI()
    app.include_router(notes_routes.router)
    return TestClient(app)


# --- read / list / search / backlinks (ported from the original verify harness) ---

def test_health_capabilities(client):
    r = client.get("/notes-app/capabilities")
    assert r.status_code == 200
    assert r.json()["provides"] == [
        "notes-app.read", "notes-app.search", "notes-app.links", "notes-app.write"
    ]


def test_list(client):
    r = client.get("/notes-app/notes")
    assert r.status_code == 200
    assert r.json()["count"] == 3


def test_read_resolves_links(client):
    r = client.get("/notes-app/notes/index")
    assert r.status_code == 200
    resolved = {l["raw"]: l["resolved"] for l in r.json()["links"]}
    assert resolved["projects/alpha"] == "projects/alpha.md"
    assert resolved["beta"] == "beta.md"


def test_read_missing_404(client):
    assert client.get("/notes-app/notes/nope").status_code == 404


def test_search(client):
    r = client.get("/notes-app/search", params={"q": "searchable-needle"})
    assert r.status_code == 200
    assert [x["path"] for x in r.json()["results"]] == ["beta.md"]


def test_search_empty_query_422(client):
    assert client.get("/notes-app/search", params={"q": ""}).status_code == 422


def test_backlinks_both_forms(client):
    # alpha is linked from index (plain) and beta (heading form)
    r = client.get("/notes-app/backlinks/projects/alpha")
    assert r.status_code == 200
    assert {x["path"] for x in r.json()["backlinks"]} == {"index.md", "beta.md"}


# --- create / update ---

def test_create_then_conflict(client):
    r = client.post("/notes-app/notes", json={"path": "new-note", "content": "# New\n"})
    assert r.status_code == 201
    assert client.post("/notes-app/notes", json={"path": "new-note", "content": "x"}).status_code == 409


def test_update_and_missing(client):
    assert client.put("/notes-app/notes/index", json={"content": "# Index\nedited\n"}).status_code == 200
    assert client.put("/notes-app/notes/ghost", json={"content": "x"}).status_code == 404


# --- path safety ---

def test_traversal_rejected(client):
    assert client.get("/notes-app/notes/..%2F..%2Fetc%2Fpasswd").status_code == 400
    assert client.post("/notes-app/notes", json={"path": "../escape.md", "content": "x"}).status_code == 400


# --- delete ---

def test_delete_then_404(client):
    assert client.delete("/notes-app/notes/beta").status_code == 200
    assert client.get("/notes-app/notes/beta").status_code == 404
    assert client.delete("/notes-app/notes/beta").status_code == 404


def test_delete_prunes_empty_dirs(client, tmp_path):
    assert client.delete("/notes-app/notes/projects/alpha").status_code == 200
    assert not (tmp_path / "projects").exists()  # emptied dir pruned


# --- rename with link rewrite ---

def test_rename_rewrites_referrers(client):
    # alpha is referred to by index ([[projects/alpha]]) and beta ([[projects/alpha#notes]])
    r = client.post("/notes-app/notes/projects/alpha/rename", json={"to": "topics/renamed"})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == "topics/renamed.md"
    assert body["notes_rewritten"] == 2
    assert body["links_updated"] == 2
    # index's link now points at the new path
    idx = client.get("/notes-app/notes/index").json()
    resolved = {l["raw"]: l["resolved"] for l in idx["links"]}
    assert resolved["topics/renamed"] == "topics/renamed.md"
    # beta preserved its #notes heading on the rewritten target
    beta = client.get("/notes-app/notes/beta").json()
    assert "[[topics/renamed#notes]]" in beta["content"]
    # backlinks follow the move
    bl = client.get("/notes-app/backlinks/topics/renamed").json()
    assert {x["path"] for x in bl["backlinks"]} == {"index.md", "beta.md"}


def test_rename_conflict_and_missing(client):
    assert client.post("/notes-app/notes/index/rename", json={"to": "beta"}).status_code == 409
    assert client.post("/notes-app/notes/ghost/rename", json={"to": "x"}).status_code == 404


def test_rename_unsafe_target_400(client):
    assert client.post("/notes-app/notes/index/rename", json={"to": "../escape"}).status_code == 400


# --- catalog registration ---

def test_catalog_registration():
    assert "notes-app" in app_registry.BUILTIN_APPS
    spec = app_registry.BUILTIN_APPS["notes-app"]
    assert spec.identity["provides"] == notes_routes.PROVIDES
    # THE ROUTE IS THE ID, and this used to assert `/apps/notes`, which pinned
    # the bug in place. The frontend serves this app at `/apps/notes-app`
    # (registry.tsx builds the path from the same id), no app has the slug
    # `notes`, and `app_surface.surface_for` takes this card's route as the
    # app's home. So the one link the app handed out went to a route nobody
    # serves and the shell bounced the reader to Home with an error. Reported
    # 2026-09-01: "https://.../apps/notes/chats -- redirects to home with an
    # error".
    assert spec.card["route"] == f"/apps/{spec.id}"
    assert spec.mounts and spec.mounts[0].deps  # mounted with an auth dep
