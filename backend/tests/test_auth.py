"""Product-auth layer: password hashing, JWT issue/verify, store, and the
register/login/refresh/me/logout routes."""
from __future__ import annotations

import os
from types import SimpleNamespace

# Pin a deterministic secret + isolated DB BEFORE importing the auth modules,
# since the JWT secret and DB path are resolved at import time.
os.environ["WORKSHOP_JWT_SECRET"] = "test-secret-do-not-use-in-prod"
os.environ.setdefault("WORKSHOP_OPERATOR_EMAIL", "")
os.environ["WORKSHOP_REGISTRATION_OPEN"] = "1"

import pytest
from fastapi.testclient import TestClient

import deps_user
from services import auth_store, jwt_tokens
from main import app


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
    a = auth_store.hash_password("same")
    b = auth_store.hash_password("same")
    assert a != b  # distinct salts


# ----------------------------------------------------------------- store


def test_first_user_is_operator(db):
    u1 = auth_store.create_user("first@example.com", "password1")
    u2 = auth_store.create_user("second@example.com", "password2")
    assert u1["is_operator"] is True
    assert u2["is_operator"] is False


def test_operator_email_override(db, monkeypatch):
    monkeypatch.setenv("WORKSHOP_OPERATOR_EMAIL", "boss@example.com")
    u1 = auth_store.create_user("first@example.com", "password1")
    u2 = auth_store.create_user("boss@example.com", "password2")
    assert u1["is_operator"] is False
    assert u2["is_operator"] is True


def test_duplicate_email_rejected(db):
    auth_store.create_user("dup@example.com", "password1")
    with pytest.raises(auth_store.EmailTaken):
        auth_store.create_user("DUP@example.com", "password2")  # case-insensitive


def test_short_password_rejected(db):
    with pytest.raises(ValueError):
        auth_store.create_user("x@example.com", "short")


def test_verify_credentials(db):
    auth_store.create_user("login@example.com", "password1")
    assert auth_store.verify_credentials("login@example.com", "password1")
    assert auth_store.verify_credentials("login@example.com", "nope") is None
    assert auth_store.verify_credentials("missing@example.com", "password1") is None


# ----------------------------------------------------------------- jwt


def test_access_token_roundtrip(db):
    user = auth_store.create_user("jwt@example.com", "password1")
    token = jwt_tokens.make_access(user)
    claims = jwt_tokens.decode(token, "access")
    ident = jwt_tokens.claims_to_identity(claims)
    assert ident["id"] == user["id"]
    assert ident["is_operator"] is True


def test_access_token_wrong_type_rejected(db):
    user = auth_store.create_user("jwt2@example.com", "password1")
    refresh, _, _ = jwt_tokens.make_refresh(user)
    with pytest.raises(jwt_tokens.TokenError):
        jwt_tokens.decode(refresh, "access")  # refresh presented as access


def test_user_from_request_helper(db):
    user = auth_store.create_user("req@example.com", "password1")
    token = jwt_tokens.make_access(user)
    req = SimpleNamespace(headers={"authorization": f"Bearer {token}"})
    resolved = deps_user.user_from_request(req)  # type: ignore[arg-type]
    assert resolved and resolved["id"] == user["id"]
    assert deps_user.user_from_request(SimpleNamespace(headers={})) is None  # type: ignore[arg-type]


# ----------------------------------------------------------------- routes


def test_register_login_me_flow(client):
    r = client.post("/auth/register", json={"email": "a@example.com", "password": "password1", "name": "A"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["is_operator"] is True
    access = body["access_token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["email"] == "a@example.com"

    # No token -> 401
    assert client.get("/auth/me").status_code == 401


def test_register_duplicate_and_bad_email(client):
    client.post("/auth/register", json={"email": "dup@example.com", "password": "password1"})
    dup = client.post("/auth/register", json={"email": "dup@example.com", "password": "password2"})
    assert dup.status_code == 409
    bad = client.post("/auth/register", json={"email": "not-an-email", "password": "password1"})
    assert bad.status_code == 422


def test_login_wrong_password(client):
    client.post("/auth/register", json={"email": "b@example.com", "password": "password1"})
    ok = client.post("/auth/login", json={"email": "b@example.com", "password": "password1"})
    assert ok.status_code == 200
    bad = client.post("/auth/login", json={"email": "b@example.com", "password": "WRONG"})
    assert bad.status_code == 401


def test_refresh_rotates_and_revokes(client):
    reg = client.post("/auth/register", json={"email": "c@example.com", "password": "password1"}).json()
    old_refresh = reg["refresh_token"]

    rot = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert rot.status_code == 200
    new = rot.json()
    assert new["access_token"] and new["refresh_token"] != old_refresh

    # The old refresh token is now revoked (rotation) and can't be replayed.
    replay = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert replay.status_code == 401


def test_registration_closed(client, monkeypatch):
    monkeypatch.setenv("WORKSHOP_REGISTRATION_OPEN", "0")
    r = client.post("/auth/register", json={"email": "late@example.com", "password": "password1"})
    assert r.status_code == 403


def test_change_password(client):
    reg = client.post("/auth/register", json={"email": "pw@example.com", "password": "password1"}).json()
    access, old_refresh = reg["access_token"], reg["refresh_token"]
    hdr = {"Authorization": f"Bearer {access}"}

    # Wrong current password -> 401.
    bad = client.post("/auth/password", json={"current_password": "WRONG", "new_password": "password2"}, headers=hdr)
    assert bad.status_code == 401

    ok = client.post("/auth/password", json={"current_password": "password1", "new_password": "password2"}, headers=hdr)
    assert ok.status_code == 200
    assert ok.json()["access_token"]

    # Old password no longer works; new one does.
    assert client.post("/auth/login", json={"email": "pw@example.com", "password": "password1"}).status_code == 401
    assert client.post("/auth/login", json={"email": "pw@example.com", "password": "password2"}).status_code == 200
    # Prior refresh tokens were revoked by the change.
    assert client.post("/auth/refresh", json={"refresh_token": old_refresh}).status_code == 401


def test_change_password_requires_auth(client):
    assert client.post("/auth/password", json={"current_password": "x", "new_password": "password2"}).status_code == 401


def test_logout_everywhere_revokes_all(client):
    reg = client.post("/auth/register", json={"email": "all@example.com", "password": "password1"}).json()
    access, refresh = reg["access_token"], reg["refresh_token"]
    out = client.post("/auth/logout", json={"all_devices": True}, headers={"Authorization": f"Bearer {access}"})
    assert out.status_code == 200 and out.json()["scope"] == "all"
    assert client.post("/auth/refresh", json={"refresh_token": refresh}).status_code == 401


def test_logout_revokes_refresh(client):
    reg = client.post("/auth/register", json={"email": "d@example.com", "password": "password1"}).json()
    access, refresh = reg["access_token"], reg["refresh_token"]
    out = client.post(
        "/auth/logout",
        json={"refresh_token": refresh},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert out.status_code == 200
    # Revoked refresh can't mint new tokens.
    assert client.post("/auth/refresh", json={"refresh_token": refresh}).status_code == 401
