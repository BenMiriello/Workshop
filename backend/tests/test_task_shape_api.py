"""The ticket HTTP surface speaks `title` + `body`.

A ticket is title + description, the way Linear, Jira and GitHub all model one
(docs/platform-redesign/ticket-model-and-external-trackers-2026-08-07.md). These
tests pin the API contract that makes that real: the pair round-trips, each half
patches independently, the legacy one-blob `text` still works for the CLI and
older clients, and asking for both at once is an error rather than a silent
precedence rule.

The router is exercised through a bare FastAPI app (auth attaches at
catalog-mount time in main.py), with the scope pointed at a tmp store.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import tasks_all  # noqa: E402
from services import tasks  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    # One scope, rooted at a tmp store. Dedup's embedder is not available in the
    # test env and is not what these tests are about, so the create path is
    # pinned to the plain store add.
    monkeypatch.setattr(tasks_all, "_root_for_scope",
                        lambda scope: tmp_path if scope == "workshop" else None)

    def _add(root, *, text, labels=None, priority=0, source=None,
             scoped_by=None, **kw):
        task = tasks.add(root, text=text, labels=labels or [], priority=priority,
                         source=source, scoped_by=scoped_by)
        return {"decision": "distinct", "task": task, "matched_id": None,
                "cosine": None, "message": "created"}

    monkeypatch.setattr(tasks_all.task_dedup, "dedup_add", _add)

    app = FastAPI()
    app.include_router(tasks_all.router, prefix="/api")
    return TestClient(app)


def _create(client, **payload) -> dict:
    r = client.post("/api/tasks", json={"scope": "workshop", **payload})
    assert r.status_code == 200, r.text
    return r.json()["task"]


def test_create_with_a_title_and_body_stores_the_pair(client):
    t = _create(client, title="Bind a scope to a tracker",
                body="Objective: one binding per scope.\nOutput: a settings form.")
    assert t["title"] == "Bind a scope to a tracker"
    assert t["body"].startswith("Objective: one binding per scope.")
    # `text` is the projection every legacy reader still sees.
    assert t["text"] == t["title"] + "\n" + t["body"]


def test_create_with_one_blob_still_splits(client):
    t = _create(client, text="Fix the alerter\nIt stopped firing on Fridays.")
    assert t["title"] == "Fix the alerter"
    assert t["body"] == "It stopped firing on Fridays."


def test_create_requires_a_title_or_text(client):
    r = client.post("/api/tasks", json={"scope": "workshop", "body": "orphan body"})
    assert r.status_code == 400
    assert "required" in r.json()["detail"]


def test_title_patches_without_resending_the_body(client):
    t = _create(client, title="Original title", body="A body worth keeping.")
    r = client.patch(f"/api/tasks/workshop/{t['id']}", json={"title": "A better title"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["title"] == "A better title"
    assert out["body"] == "A body worth keeping."


def test_body_patches_without_resending_the_title(client):
    t = _create(client, title="Keep me", body="old body")
    out = client.patch(f"/api/tasks/workshop/{t['id']}", json={"body": "new body"}).json()
    assert out["title"] == "Keep me"
    assert out["body"] == "new body"


def test_patching_text_still_works_and_splits(client):
    t = _create(client, title="Before", body="before body")
    out = client.patch(f"/api/tasks/workshop/{t['id']}",
                       json={"text": "After\nafter body"}).json()
    assert (out["title"], out["body"]) == ("After", "after body")


def test_sending_text_and_title_together_is_an_error(client):
    t = _create(client, title="Pick one", body="")
    r = client.patch(f"/api/tasks/workshop/{t['id']}",
                     json={"text": "one blob", "title": "and a title"})
    assert r.status_code == 400
    assert "not both" in r.json()["detail"]


def test_labels_round_trip(client):
    t = _create(client, title="Labelled", labels=["infra", "sync", "infra"])
    assert t["labels"] == ["infra", "sync"]   # deduped, order preserved
    out = client.patch(f"/api/tasks/workshop/{t['id']}", json={"labels": []}).json()
    assert out["labels"] == []


def test_draft_is_an_accepted_status(client):
    t = _create(client, title="Not ready yet")
    out = client.patch(f"/api/tasks/workshop/{t['id']}", json={"status": "draft"}).json()
    assert out["status"] == "draft"


def test_exec_projection_carries_the_private_fields(client):
    t = _create(client, title="Has exec")
    assert set(tasks.EXEC_FIELDS) <= set(t["exec"].keys())
    # And nothing under exec is in the syncable set — the boundary, asserted.
    assert not set(tasks.EXEC_FIELDS) & set(tasks.SYNCABLE_FIELDS)


def test_an_empty_patch_is_rejected(client):
    t = _create(client, title="Untouched")
    r = client.patch(f"/api/tasks/workshop/{t['id']}", json={})
    assert r.status_code == 400
