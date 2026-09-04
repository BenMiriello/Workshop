"""Red-team verification of the auth + ownership redesign (docs/auth-redesign/).

Every assertion here is a guarantee the operator demanded: no anonymous access,
no cross-user data, no network/loopback trust, no fail-open, no is_operator data
bypass, and WORKSHOP_OPERATOR_EMAIL consulted at no request-time path. These run
against the real FastAPI app via TestClient (which connects from loopback — so
every 401/403 below also proves loopback grants nothing)."""
from __future__ import annotations

import os
import pathlib

os.environ["WORKSHOP_COOKIE_SECURE"] = "0"  # test client speaks http
os.environ["WORKSHOP_REGISTRATION_OPEN"] = "1"

import pytest
from fastapi.testclient import TestClient

from services import auth_store, ownership
from main import app
from tests.conftest import requires_app

CSRF = {"X-Workshop-Client": "1"}
BACKEND = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    auth_store.init_db()
    # Operator = data owner of the single-dataset domains (set as DATA here, the
    # way the bootstrap migration does — never via email at request time).
    op = auth_store.create_user("operator@example.com", "password1")
    member = auth_store.create_user("member@example.com", "password2")
    auth_store.set_operator(op["id"])
    for d in ("finance", "commute", "location", "health"):
        auth_store.set_domain_owner(d, op["id"])
    return {"op": op, "member": member, "tmp": tmp_path}


def _client_for(email: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return c


def _is_mounted(path: str) -> bool:
    """Is this path actually served by this deployment?

    Several of these routes belong to installable apps, so on a fresh clone with
    no apps installed they are not mounted at all. A 404 there is not an
    ownership failure, it is an absent feature, and asserting `401 or 403`
    against it made the whole suite red on any machine but the one it was
    written on.
    """
    return any(getattr(r, "path", None) == path for r in app.routes)


# 1 + 2 — no credential (from loopback) reaches any personal/platform endpoint.
@pytest.mark.parametrize("path", [
    "/finance/cache", "/budget/plan", "/location/latest", "/health/latest",
    "/commute/schedule", "/chat/threads", "/agents/", "/push/status",
])
def test_unauthenticated_is_denied(env, path):
    if not _is_mounted(path):
        pytest.skip(f"{path} is not mounted on this deployment (app not installed)")
    anon = TestClient(app)
    assert anon.get(path).status_code in (401, 403)


# 4 — garbage / absent credentials never fail open.
def test_garbage_credentials_denied(env):
    requires_app("location")
    anon = TestClient(app)
    assert anon.get("/location/latest", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    anon.cookies.set("workshop_session", "forged-cookie-value")
    assert anon.get("/location/latest").status_code == 401


# 3 — a second member never sees the operator's data and cannot widen scope.
def test_member_cannot_read_operator_domains(env):
    requires_app("commute")
    requires_app("finance")
    member = _client_for("member@example.com", "password2")
    # commute is still the operator's single-dataset domain -> 403.
    assert member.get("/commute/schedule").status_code == 403
    # finance/budget are PER-USER now: the member reaches them (200) but sees
    # ONLY their own empty partition — never the operator's linked banks.
    items = member.get("/finance/items")
    assert items.status_code == 200
    assert items.json()["items"] == []          # zero banks; not the operator's 4
    # Forged widening params cannot reveal the operator's finance.
    assert member.get("/finance/items?owner_id=" + str(env["op"]["id"])).json()["items"] == []


def test_per_user_stores_are_isolated(env):
    requires_app("location")
    op = _client_for("operator@example.com", "password1")
    member = _client_for("member@example.com", "password2")
    # Operator records a location ping into THEIR partition. ADR 0023: a POST
    # that creates answers 201, and a single-resource response is the
    # resource itself, unwrapped (no {"latest": ...} / {"metrics": ...}).
    r = op.post("/location/update", json={"lat": 37.44, "lng": -122.14}, headers=CSRF)
    assert r.status_code == 201, r.text
    assert op.get("/location/latest").json()["lat"] == 37.44
    # The member sees only their own (empty) partition — never the operator's.
    assert member.get("/location/latest").json() is None
    assert member.get("/health/latest").json() == {}
    # Budget is per-user: the operator edits THEIR plan; the member's plan is a
    # separate default and never reflects the operator's edit.
    op_plan = op.get("/budget/plan").json()
    op_plan["_test_marker"] = "op-only"
    assert op.put("/budget/plan", json=op_plan, headers=CSRF).status_code == 200
    assert op.get("/budget/plan").json().get("_test_marker") == "op-only"
    assert member.get("/budget/plan").json().get("_test_marker") is None


# 7 — platform routers are admin-only; a non-admin member is refused.
def test_platform_requires_operator(env):
    member = _client_for("member@example.com", "password2")
    assert member.get("/agents/").status_code == 403
    op = _client_for("operator@example.com", "password1")
    assert op.get("/agents/").status_code != 403  # operator passes the gate


# 5 — a service token only works inside its scope / ownership.
def test_service_token_scoping(env):
    requires_app("commute")
    # An operator-owned commute token authenticates the cron to the commute domain.
    tok = auth_store.create_service_token(env["op"]["id"], "automation:commute", "cron")
    c = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    assert c.get("/commute/schedule", headers=h).status_code == 200
    # A token owned by a NON-owner (the member) is refused on the operator domain.
    mtok = auth_store.create_service_token(env["member"]["id"], "automation:commute", "x")
    assert c.get("/commute/schedule", headers={"Authorization": f"Bearer {mtok}"}).status_code == 403


# 6 — WORKSHOP_OPERATOR_EMAIL is consulted at NO request-time path. It may appear
# only in the one-time migration script. Grep the request-serving code.
def test_operator_email_not_consulted_at_request_time():
    offenders = []
    for p in BACKEND.rglob("*.py"):
        rel = p.relative_to(BACKEND).as_posix()
        if rel.startswith(("scripts/", "tests/", ".venv/")):
            continue
        if "WORKSHOP_OPERATOR_EMAIL" in p.read_text():
            offenders.append(rel)
    assert offenders == [], f"operator email referenced in request-time code: {offenders}"


# 7 LEFT THIS FILE ON 2026-09-04. It asserted that one app's reads are scoped to
# the server-pinned owner and that a client-supplied viewer id cannot widen
# them. The ownership MODEL is this file's subject and stays; the app that
# resolves a viewer is the one that can regress it, so the assertion moved to
# that app's own tests and this file stopped being the last platform module
# importing an app.
