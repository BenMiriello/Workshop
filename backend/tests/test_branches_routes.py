"""`routes/branches.py` -- the worktree record answers over HTTP.

Two things are worth a live request rather than a direct call to the service.

1. FastAPI FILTERS every return value through `response_model` and silently
   drops any key the model does not name. A handler that returns the service's
   dict verbatim therefore looks correct in isolation and loses fields over the
   wire; that is the exact defect ADR 0023 clause 6 exists for. So these go
   through the app, and the timeline assertion checks that the STRUCTURED fields
   of section 9's events survive the trip -- an event whose `sha` and `actor`
   are dropped by the model is prose again.
2. The design rule in section 13.2 is that every router declares a prefix and a
   tag ("a bare `APIRouter()` is a bug"), that the resource is addressable on
   its own, and that it is mounted. An unwired route is not done.

Auth is NOT exercised here: the router carries none of its own, exactly like the
other platform-control routers, and `main.py` mounts it with the operator
dependency alongside them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import branches as branch_routes  # noqa: E402
from services import worktree_records as records  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(branch_routes.router)
    return TestClient(app)


class TestTheRouterIsAResource:
    def test_it_declares_a_prefix_and_a_tag(self):
        assert branch_routes.router.prefix == "/branches"
        assert branch_routes.router.tags == ["branches"]

    def test_every_route_declares_a_response_model(self):
        for route in branch_routes.router.routes:
            assert getattr(route, "response_model", None) is not None, (
                f"{route.path} has no response_model, so the generated client "
                f"types it as unknown")

    def test_it_is_mounted_in_the_app(self):
        """An unwired router is not done. `main` imports the world, so this is
        the one test in the file that pays for the whole app import."""
        from main import app

        paths = {r.path for r in app.routes}
        assert "/branches" in paths
        assert "/branches/{worktree_id}/timeline" in paths
        assert "/branches/{worktree_id}/events" in paths


class TestItReportsAMissingStoreRatherThanAnEmptyList:
    """An empty `/branches` because the database is down must not read the same
    as an empty `/branches` because nothing is allocated."""

    def test_the_collection_is_a_503(self, client, monkeypatch):
        def _down(*a, **k):
            raise records.WorktreesUnavailable("no relational store")

        monkeypatch.setattr(records, "list_records", _down)
        assert client.get("/branches").status_code == 503

    def test_an_unknown_record_is_a_404(self, client, monkeypatch):
        def _missing(*a, **k):
            raise records.WorktreeNotFound("nope")

        monkeypatch.setattr(records, "get_record", _missing)
        assert client.get("/branches/nope").status_code == 404


class TestTheModelsDoNotDropFields:
    _ROW = {
        "id": "11111111-1111-1111-1111-111111111111",
        "app_id": None,
        "project_slug": "proj",
        "scope": "project:proj",
        "branch_slug": "t-1",
        "branch": "wt/proj/t-1",
        "path": "/branches/proj/t-1",
        "base_ref": "origin/main",
        "base_sha": "a" * 40,
        "ticket_id": "T-1",
        "allocated_by": "sweep",
        "status": "allocated",
        "merged_sha": None,
        "removed_at": None,
        "created_at": "2026-08-23T00:00:00+00:00",
        "updated_at": "2026-08-23T00:00:00+00:00",
    }

    def test_the_record_survives_the_wire(self, client, monkeypatch):
        monkeypatch.setattr(records, "get_record", lambda *a, **k: dict(self._ROW))
        body = client.get(f"/branches/{self._ROW['id']}").json()
        assert body == self._ROW

    def test_a_timeline_event_keeps_its_structured_fields(self, client,
                                                          monkeypatch):
        event = {
            "id": "22222222-2222-2222-2222-222222222222",
            "worktree_id": self._ROW["id"],
            "kind": "preempted",
            "actor": "chat:42",
            "sha": "b" * 40,
            "detail": "operator asked for the fix now",
            "paths": ["backend/services/worktrees.py"],
            "at": "2026-08-23T00:00:01+00:00",
        }
        monkeypatch.setattr(records, "timeline", lambda *a, **k: [event])
        body = client.get(f"/branches/{self._ROW['id']}/timeline").json()
        assert body == {"worktree_id": self._ROW["id"], "events": [event]}

    def test_an_invented_kind_is_a_400(self, client, monkeypatch):
        def _refuse(*a, **k):
            raise ValueError("unknown worktree event kind 'vibed'")

        monkeypatch.setattr(records, "add_event", _refuse)
        r = client.post(f"/branches/{self._ROW['id']}/events",
                        json={"kind": "vibed"})
        assert r.status_code == 400
