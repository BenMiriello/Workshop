"""Auth layer: password hashing, the user store, opaque sessions / device /
service tokens, and the cookie-based register/login/logout/me routes.

The redesign (docs/auth-redesign/) replaced JWT-in-localStorage with opaque
server-side sessions in an HttpOnly cookie, and made registration NEVER confer
operator status (that is set once by the bootstrap migration). These tests pin
that behavior."""
from __future__ import annotations

import os

# Local http test client can't store a Secure cookie, so disable Secure here.
# Production sets WORKSHOP_COOKIE_SECURE=1 (https end-to-end).
os.environ["WORKSHOP_COOKIE_SECURE"] = "0"
os.environ.setdefault("WORKSHOP_OPERATOR_EMAIL", "")
os.environ["WORKSHOP_REGISTRATION_OPEN"] = "1"

import pytest
from fastapi.testclient import TestClient

import deps_user
from services import auth_store, ownership
from main import app

CSRF = {"X-Workshop-Client": "1"}  # required on cookie-authed mutations


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.delenv("WORKSHOP_OPERATOR_EMAIL", raising=False)
    auth_store.init_db()
    return tmp_path


@pytest.fixture
def client(db):
    return TestClient(app)


# ----------------------------------------------------------------- hashing


def test_password_hash_roundtrip():
    enc = auth_store.hash_password("correct horse battery")
    assert enc.startswith("scrypt$")
    assert auth_store.verify_password("correct horse battery", enc)
    assert not auth_store.verify_password("wrong", enc)


def test_password_hash_is_salted():
    assert auth_store.hash_password("same") != auth_store.hash_password("same")


# ----------------------------------------------------------------- store + operator


def test_registration_never_confers_operator(db):
    """The core hardening: no account becomes operator just by registering — not
    the first one, not one matching WORKSHOP_OPERATOR_EMAIL."""
    os.environ["WORKSHOP_OPERATOR_EMAIL"] = "boss@example.com"
    try:
        u1 = auth_store.create_user("first@example.com", "password1")
        u2 = auth_store.create_user("boss@example.com", "password2")
        assert u1["is_operator"] is False
        assert u2["is_operator"] is False
        assert auth_store.operator_id() is None
    finally:
        os.environ["WORKSHOP_OPERATOR_EMAIL"] = ""


def test_set_operator_is_explicit_and_singular(db):
    a = auth_store.create_user("a@example.com", "password1")
    b = auth_store.create_user("b@example.com", "password2")
    auth_store.set_operator(a["id"])
    assert auth_store.operator_id() == a["id"]
    # Re-flagging moves it; only one operator at a time.
    auth_store.set_operator(b["id"])
    assert auth_store.operator_id() == b["id"]
    assert auth_store.get_by_id(a["id"])["is_operator"] is False


def test_duplicate_email_rejected(db):
    auth_store.create_user("dup@example.com", "password1")
    with pytest.raises(auth_store.EmailTaken):
        auth_store.create_user("DUP@example.com", "password2")


def test_short_password_rejected(db):
    with pytest.raises(ValueError):
        auth_store.create_user("x@example.com", "short")


def test_verify_credentials(db):
    auth_store.create_user("login@example.com", "password1")
    assert auth_store.verify_credentials("login@example.com", "password1")
    assert auth_store.verify_credentials("login@example.com", "nope") is None
    assert auth_store.verify_credentials("missing@example.com", "password1") is None


# ----------------------------------------------------------------- sessions / tokens


def test_session_roundtrip_and_revoke(db):
    u = auth_store.create_user("s@example.com", "password1")
    raw = auth_store.create_session(u["id"])
    assert auth_store.session_user(raw)["id"] == u["id"]
    auth_store.revoke_session(raw)
    assert auth_store.session_user(raw) is None
    assert auth_store.session_user("garbage") is None


def test_device_token_roundtrip_and_revoke(db):
    u = auth_store.create_user("d@example.com", "password1")
    raw = auth_store.create_device_token(u["id"], "iphone")
    assert auth_store.device_user(raw)["id"] == u["id"]
    devs = auth_store.list_devices(u["id"])["devices"]
    assert len(devs) == 1 and devs[0]["label"] == "iphone"
    auth_store.revoke_device_by_prefix(u["id"], devs[0]["id"])
    assert auth_store.device_user(raw) is None

    # re-provisioning the same label reuses the row instead of piling up a new one
    raw2 = auth_store.create_device_token(u["id"], "iphone")
    assert auth_store.device_user(raw2)["id"] == u["id"]
    raw3 = auth_store.create_device_token(u["id"], "iphone")
    result = auth_store.list_devices(u["id"])
    assert result["total"] == 1 and len(result["devices"]) == 1
    assert auth_store.device_user(raw2) is None  # rotated out by raw3
    assert auth_store.device_user(raw3)["id"] == u["id"]


def test_service_token_carries_scope(db):
    u = auth_store.create_user("svc@example.com", "password1")
    raw = auth_store.create_service_token(u["id"], "ingest:location", "owntracks")
    ident = auth_store.service_identity(raw)
    assert ident["user"]["id"] == u["id"]
    assert ident["scope"] == "ingest:location"
    assert auth_store.service_identity("nope") is None


def test_revoke_all_sweeps_sessions_and_devices(db):
    u = auth_store.create_user("z@example.com", "password1")
    s = auth_store.create_session(u["id"])
    d = auth_store.create_device_token(u["id"])
    auth_store.revoke_all_for_user(u["id"])
    assert auth_store.session_user(s) is None
    assert auth_store.device_user(d) is None


# ----------------------------------------------------------------- routes (cookie)


def test_register_login_me_flow(client):
    r = client.post("/auth/register", json={"email": "a@example.com", "password": "password1", "name": "A"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["is_operator"] is False  # registration never operator
    assert "access_token" not in body  # no JWT in the response anymore
    # The cookie is set and the test client carries it forward.
    me = client.get("/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "a@example.com"


def test_me_requires_auth():
    fresh = TestClient(app)
    assert fresh.get("/auth/me").status_code == 401


def test_register_duplicate_and_bad_email(client):
    client.post("/auth/register", json={"email": "dup@example.com", "password": "password1"})
    dup = client.post("/auth/register", json={"email": "dup@example.com", "password": "password2"})
    assert dup.status_code == 409
    bad = client.post("/auth/register", json={"email": "not-an-email", "password": "password1"})
    assert bad.status_code == 422


def test_login_wrong_password(client):
    client.post("/auth/register", json={"email": "b@example.com", "password": "password1"})
    assert client.post("/auth/login", json={"email": "b@example.com", "password": "password1"}).status_code == 200
    assert client.post("/auth/login", json={"email": "b@example.com", "password": "WRONG"}).status_code == 401


def test_cookie_mutation_requires_csrf_header(client):
    client.post("/auth/register", json={"email": "csrf@example.com", "password": "password1"})
    # Cookie present, but no X-Workshop-Client header on a mutating request -> 403.
    assert client.post("/auth/logout", json={}).status_code == 403
    assert client.post("/auth/logout", json={}, headers=CSRF).status_code == 200


def test_registration_closed(client, monkeypatch):
    monkeypatch.setenv("WORKSHOP_REGISTRATION_OPEN", "0")
    r = client.post("/auth/register", json={"email": "late@example.com", "password": "password1"})
    assert r.status_code == 403


def test_change_password_revokes_and_reissues(client):
    client.post("/auth/register", json={"email": "pw@example.com", "password": "password1"})
    bad = client.post("/auth/password", json={"current_password": "WRONG", "new_password": "password2"}, headers=CSRF)
    assert bad.status_code == 401
    ok = client.post("/auth/password", json={"current_password": "password1", "new_password": "password2"}, headers=CSRF)
    assert ok.status_code == 200
    # Old password no longer works; new one does.
    assert client.post("/auth/login", json={"email": "pw@example.com", "password": "password1"}).status_code == 401
    assert client.post("/auth/login", json={"email": "pw@example.com", "password": "password2"}).status_code == 200


def test_logout_everywhere(client):
    client.post("/auth/register", json={"email": "all@example.com", "password": "password1"})
    out = client.post("/auth/logout", json={"all_devices": True}, headers=CSRF)
    assert out.status_code == 200 and out.json()["scope"] == "all"
    # Session cookie was revoked; /auth/me is now unauthenticated.
    assert client.get("/auth/me").status_code == 401


def test_device_token_issue_and_use(client):
    client.post("/auth/register", json={"email": "dev@example.com", "password": "password1"})
    issued = client.post("/auth/device-token", json={"label": "iphone"}, headers=CSRF)
    assert issued.status_code == 200
    raw = issued.json()["device_token"]
    # A fresh client (no cookie) authenticates with the device token as a bearer.
    bare = TestClient(app)
    assert bare.get("/auth/me", headers={"Authorization": f"Bearer {raw}"}).status_code == 200
