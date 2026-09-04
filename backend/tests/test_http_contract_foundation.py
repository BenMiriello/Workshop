"""ADR 0023 (`docs/decisions/0023-the-http-contract.md`) foundation: the
registered error handler and the `Page[T]` envelope.

BOTH exemplar conversions were an app's routes and both left with their app on
2026-09-04, into that app's own `apps/<slug>/tests/`. They tested those apps'
routes, not this ADR, and a fresh clone has no reason to ship them. What stays here is
the part that is true of every route on every installation: the handler shape,
the envelope, and the two constants.

The probes below therefore use PLATFORM routes. They used to use an app's,
which meant this file named an app twelve times and skipped entirely on an
instance that had not installed it -- so the ADR's own foundation went unasserted
on exactly the clone it most needed to hold for.

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
from wsplatform.http import DEFAULT_PAGE, MAX_PAGE, Page, PageMeta, error_body, validation_error_body

CSRF = {"X-Workshop-Client": "1"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    auth_store.init_db()
    op = auth_store.create_user("operator@example.com", "password1")
    auth_store.set_operator(op["id"])
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
    r = anon.get("/api/chat/threads")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "unauthorized"


def test_validation_error_carries_the_same_shape(env):
    """A 422 (FastAPI's own `RequestValidationError`, not a hand-written
    raise site) must carry the same `{"code", "message", "hint"}` object as
    every other failure, not FastAPI's stock bare list of `{loc, msg, type}`."""
    op = _client_for("operator@example.com", "password1")
    # `context_key` is typed `str`; a list fails validation before the handler
    # body ever runs.
    r = op.post("/api/chat/threads", json={"context_key": ["not", "a", "string"]},
                headers=CSRF)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert set(detail) == {"code", "message", "hint"}
    assert detail["code"] == "validation_error"
    assert "context_key" in detail["message"]


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
