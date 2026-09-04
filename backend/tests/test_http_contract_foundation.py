"""ADR 0023 (`docs/decisions/0023-the-http-contract.md`) foundation: the
registered error handler, the `Page[T]` envelope, and the one exemplar
conversion that is still platform code (`routes/location.py`).

The second exemplar was an app. Its three assertions left with it on 2026-09-04
and are `apps/health/tests/test_health_contract.py` now: they tested that app's
routes, not this ADR, and a fresh clone had no reason to ship them.

Setup mirrors `test_ownership.py`: real `main.app` via TestClient, auth state
in `tmp_path` so nothing here touches the real deployment's data.
"""
from __future__ import annotations

import os
import pathlib

os.environ["WORKSHOP_COOKIE_SECURE"] = "0"
os.environ["WORKSHOP_REGISTRATION_OPEN"] = "1"

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from main import app
from services import auth_store, ownership
from tests.conftest import requires_app
from wsplatform.http import DEFAULT_PAGE, MAX_PAGE, Page, PageMeta, error_body, validation_error_body

CSRF = {"X-Workshop-Client": "1"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    auth_store.init_db()
    op = auth_store.create_user("operator@example.com", "password1")
    auth_store.set_operator(op["id"])
    auth_store.set_domain_owner("location", op["id"])
    return {"op": op, "tmp": tmp_path}


def _client_for(email: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return c


# ---------------------------------------------------------------- error model


def test_http_exception_upgrades_a_plain_string_detail(env):
    """`raise HTTPException(404, "no ticket 0f8c")` -- a real, unmodified raise
    site (routes/agents.py and 347 others do exactly this) -- must come back
    as clause 3's `{"code", "message", "hint"}`, deriving `code` from the
    status since the raiser didn't give one."""
    anon = TestClient(app)
    r = anon.get("/api/this-route-truly-does-not-exist-anywhere")
    assert r.status_code == 404
    body = r.json()
    assert body == {"detail": {"code": "not_found", "message": "Not Found", "hint": None}}


def test_http_exception_status_code_still_means_what_it_meant(env):
    """The upgrade changes the BODY, never the status. An unauthenticated
    request to a gated route is still 401, exactly as before this ADR."""
    anon = TestClient(app)
    r = anon.get("/api/location/latest")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "unauthorized"


def test_validation_error_carries_the_same_shape(env):
    """A 422 (FastAPI's own `RequestValidationError`, not a hand-written
    raise site) must carry the same `{"code", "message", "hint"}` object as
    every other failure, not FastAPI's stock bare list of `{loc, msg, type}`."""
    op = _client_for("operator@example.com", "password1")
    # lat is required and bounded to [-90, 90]; 999 fails validation before
    # the handler body ever runs.
    r = op.post("/api/location/update", json={"lat": 999, "lng": 0}, headers=CSRF)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert set(detail) == {"code", "message", "hint"}
    assert detail["code"] == "validation_error"
    assert "lat" in detail["message"]


def test_error_body_passes_through_an_opted_in_code():
    """A raise site that hands `HTTPException(404, {"code": "ticket_not_found",
    "message": "..."})` -- opting in to a stable code, as clause 3 invites --
    is not double-wrapped or overwritten."""
    out = error_body(404, {"code": "ticket_not_found", "message": "no ticket 0f8c"})
    assert out == {"code": "ticket_not_found", "message": "no ticket 0f8c", "hint": None}


def test_validation_error_body_counts_the_rest_without_dropping_them():
    errors = [
        {"loc": ("body", "lat"), "msg": "field required", "type": "missing"},
        {"loc": ("body", "lng"), "msg": "field required", "type": "missing"},
    ]
    out = validation_error_body(errors)
    assert out["code"] == "validation_error"
    assert "lat" in out["message"]
    assert out["hint"] == "1 more validation error(s)"


# ---------------------------------------------------------------- Page[T]


def test_page_serializes_the_adr_0023_envelope():
    class Item(BaseModel):
        name: str

    page = Page[Item](items=[Item(name="a"), Item(name="b")], page=PageMeta(limit=50, total=2))
    dumped = page.model_dump(mode="json")
    assert dumped == {
        "items": [{"name": "a"}, {"name": "b"}],
        "page": {"limit": 50, "next_cursor": None, "total": 2, "offset": None},
    }


def test_page_items_is_never_absent_for_an_empty_collection():
    class Item(BaseModel):
        name: str

    page = Page[Item](items=[], page=PageMeta(limit=DEFAULT_PAGE, total=0))
    dumped = page.model_dump(mode="json")
    assert dumped["items"] == []
    assert dumped["page"]["total"] == 0


def test_default_and_max_page_constants():
    assert DEFAULT_PAGE == 50
    assert MAX_PAGE == 200


# ---------------------------------------------------------------- location.py


def test_location_update_creates_201_and_returns_the_resource_unwrapped(env):
    requires_app("location")
    op = _client_for("operator@example.com", "password1")
    r = op.post("/api/location/update", json={"lat": 37.44, "lng": -122.14}, headers=CSRF)
    assert r.status_code == 201, r.text
    body = r.json()
    # Unwrapped: the resource itself, not {"ok": true, "record": {...}}.
    assert body["lat"] == 37.44
    assert body["lng"] == -122.14
    assert "ok" not in body
    assert "record" not in body


def test_location_latest_is_the_resource_unwrapped(env):
    requires_app("location")
    op = _client_for("operator@example.com", "password1")
    op.post("/api/location/update", json={"lat": 1.0, "lng": 2.0}, headers=CSRF)
    r = op.get("/api/location/latest")
    assert r.status_code == 200
    body = r.json()
    assert body["lat"] == 1.0
    assert "latest" not in body  # not {"latest": {...}}


def test_location_latest_is_null_not_wrapped_when_absent(env):
    requires_app("location")
    op = _client_for("operator@example.com", "password1")
    r = op.get("/api/location/latest")
    assert r.status_code == 200
    assert r.json() is None


def test_location_history_is_the_adr_0023_envelope(env):
    requires_app("location")
    op = _client_for("operator@example.com", "password1")
    for i in range(3):
        op.post("/api/location/update", json={"lat": float(i), "lng": 0.0}, headers=CSRF)
    r = op.get("/api/location/history?limit=2&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "page"}
    assert len(body["items"]) == 2
    assert body["page"]["limit"] == 2
    assert body["page"]["total"] == 3
    # Offset survives (ADR 0023 clause 2: "offset survives only where it
    # already is") -- carried inside `page`, not as a fourth top-level key.
    assert body["page"]["offset"] == 0


def test_location_history_delete_is_204_with_no_body(env):
    requires_app("location")
    op = _client_for("operator@example.com", "password1")
    op.post("/api/location/update", json={"lat": 1.0, "lng": 1.0}, headers=CSRF)
    r = op.delete("/api/location/history", headers=CSRF)
    assert r.status_code == 204
    assert r.content == b""
