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


# 1 + 2 — no credential (from loopback) reaches any personal/platform endpoint.
@pytest.mark.parametrize("path", [
    "/finance/cache", "/budget/plan", "/location/latest", "/health/latest",
    "/commute/schedule", "/chat/threads", "/agents/", "/push/status",
])
def test_unauthenticated_is_denied(env, path):
    anon = TestClient(app)
    assert anon.get(path).status_code in (401, 403)


# 4 — garbage / absent credentials never fail open.
def test_garbage_credentials_denied(env):
    anon = TestClient(app)
    assert anon.get("/location/latest", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    anon.cookies.set("workshop_session", "forged-cookie-value")
    assert anon.get("/location/latest").status_code == 401


# 3 — a second member never sees the operator's data and cannot widen scope.
def test_member_cannot_read_operator_domains(env):
    member = _client_for("member@example.com", "password2")
    # finance/budget/commute are the operator's single-dataset domains -> 403.
    assert member.get("/finance/cache").status_code == 403
    assert member.get("/budget/plan").status_code == 403
    assert member.get("/commute/schedule").status_code == 403
    # Forged widening params do not help.
    assert member.get("/finance/cache?owner_id=" + str(env["op"]["id"])).status_code == 403


def test_per_user_stores_are_isolated(env):
    op = _client_for("operator@example.com", "password1")
    member = _client_for("member@example.com", "password2")
    # Operator records a location ping into THEIR partition.
    r = op.post("/location/update", json={"lat": 37.44, "lng": -122.14}, headers=CSRF)
    assert r.status_code == 200, r.text
    assert op.get("/location/latest").json()["latest"]["lat"] == 37.44
    # The member sees only their own (empty) partition — never the operator's.
    assert member.get("/location/latest").json()["latest"] is None
    # And cannot reach the operator's health/budget either.
    assert member.get("/health/latest").json() == {"metrics": {}}


# 7 — platform routers are admin-only; a non-admin member is refused.
def test_platform_requires_operator(env):
    member = _client_for("member@example.com", "password2")
    assert member.get("/agents/").status_code == 403
    op = _client_for("operator@example.com", "password1")
    assert op.get("/agents/").status_code != 403  # operator passes the gate


# 5 — a service token only works inside its scope / ownership.
def test_service_token_scoping(env):
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


# 7 — birds visibility is scoped to the server-pinned owner on the main backend
# (the "birds viewer bridge"), and a client-supplied viewer_id cannot widen it.
# Without this, /birds/* reads default to see-all. DB-free: exercises the
# resolution directly.
def test_birds_viewer_is_pinned_to_owner_not_see_all():
    from services import birds_analyze

    birds_analyze.set_request_viewer(None)  # no springer viewer (main backend)
    try:
        # No owner pinned at all -> unrestricted only when nothing is set.
        ownership.clear_current_owner()
        assert birds_analyze._resolve_viewer(None) is None

        # Owner pinned (as current_user does) -> birds scopes to that owner,
        # NOT see-all.
        ownership.set_current_owner(42)
        assert birds_analyze._resolve_viewer(None) == 42
        # A client-supplied viewer_id cannot override the server-pinned owner.
        assert birds_analyze._resolve_viewer(999) == 42

        # The springer request-scoped viewer still wins (its own auth surface).
        birds_analyze.set_request_viewer(7)
        assert birds_analyze._resolve_viewer(None) == 7
    finally:
        birds_analyze.set_request_viewer(None)
        ownership.clear_current_owner()
