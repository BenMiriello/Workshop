"""The Providers screen's own routes: what a person can do, and what leaks.

The store and the licensing rule are covered by `test_model_credentials.py`.
What is covered HERE is the surface: a credential is verified before it is
stored, the answer says whether a turn can actually run on the provider, and no
path ever hands a stored value back.

The property that took the most work to make true is the last line of
`test_the_screen_does_not_say_signed_out_while_turns_are_working`. On an
installation whose CLI holds its own credentials, the store and the auth file
are both empty, and the screen used to report "Not signed in" while every turn
in the product was running fine.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import model_providers_routes as route
from services import auth_health, model_credentials as mc
from services import ownership

PRINCIPAL = 6


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    monkeypatch.setenv("WORKSHOP_SECRETS_ROOT", str(tmp_path / "secrets"))
    monkeypatch.setenv("WORKSHOP_AUTH_ENV", str(tmp_path / "auth.env"))
    (tmp_path / "auth.env").write_text("CLAUDE_CODE_OAUTH_TOKEN=file-token\n")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv(mc.MODE_ENV, mc.MODE_SINGLE_OPERATOR)
    ownership.set_current_owner(PRINCIPAL)
    yield
    ownership.clear_current_owner()


@pytest.fixture(autouse=True)
def no_cli(monkeypatch):
    """No test may shell out to `claude`. Each one says what the probe answers."""
    async def refuse(*_a, **_kw):  # pragma: no cover - a test that hits this is wrong
        raise AssertionError("a test reached the real CLI probe")

    monkeypatch.setattr(auth_health, "check_claude", refuse)
    monkeypatch.setattr(auth_health, "verify_claude_token", refuse)
    auth_health.invalidate_claude_cache()


@pytest.fixture
def cli_signed_out(monkeypatch):
    async def row():
        return {"name": "claude", "kind": auth_health.KIND_DANGER,
                "state": "Not signed in", "detail": "No Claude credential is configured."}

    monkeypatch.setattr(auth_health, "check_claude", row)


@pytest.fixture
def cli_signed_in(monkeypatch):
    async def row():
        return {"name": "claude", "kind": auth_health.KIND_OK, "state": "Configured",
                "detail": "", "unverified_because": "This check reads local configuration."}

    monkeypatch.setattr(auth_health, "check_claude", row)


@pytest.fixture
def verifier(monkeypatch):
    """A token checker that accepts one value, so nothing spends a real turn."""
    async def fake(token: str) -> tuple[bool, str]:
        if token == "good-token":
            return True, ""
        return False, "Failed to authenticate. API Error: 401 OAuth access token is invalid."

    monkeypatch.setattr(auth_health, "verify_claude_token", fake)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    return TestClient(app)


def _row(payload, provider_id):
    return next(r for r in payload if r["id"] == provider_id)


# ---- what the screen is told -------------------------------------------------


def test_a_key_that_nothing_routes_to_is_not_reported_usable(client, cli_signed_out):
    mc.set_credential(PRINCIPAL, "anthropic_api", "sk-ant-abcd1234")
    row = _row(client.get("/api/model-providers").json(), "anthropic_api")
    assert row["stored"] is True
    assert row["configured"] is True
    assert row["usable"] is False
    assert row["last4"] == "1234"


def test_the_subscription_reads_the_auth_file_when_nothing_is_set_here(
        client, cli_signed_out):
    row = _row(client.get("/api/model-providers").json(), "claude_cli")
    assert row["source"] == "deployment"
    assert row["usable"] is True


def test_the_screen_does_not_say_signed_out_while_turns_are_working(
        client, cli_signed_in, tmp_path):
    """Store empty, auth file empty, CLI holding its own credentials. Every turn
    works, so the screen may not say the opposite."""
    (tmp_path / "auth.env").write_text("# nothing here\n")
    row = _row(client.get("/api/model-providers").json(), "claude_cli")
    assert row["configured"] is True
    assert row["usable"] is True
    assert row["source"] == "cli"


def test_nothing_anywhere_is_reported_honestly(client, cli_signed_out, tmp_path):
    (tmp_path / "auth.env").write_text("# nothing here\n")
    row = _row(client.get("/api/model-providers").json(), "claude_cli")
    assert row["configured"] is False
    assert row["usable"] is False
    assert row["source"] is None


# ---- verify before store -----------------------------------------------------


def test_a_rejected_subscription_token_is_not_stored(client, verifier, cli_signed_out):
    r = client.put("/api/model-providers/claude_cli/credential",
                   json={"value": "bad-token"})
    assert r.status_code == 400
    assert "401" in r.json()["detail"]
    assert mc.has_credential(PRINCIPAL, "claude_cli") is False


def test_a_verified_subscription_token_is_stored_and_becomes_the_source(
        client, verifier, cli_signed_out):
    r = client.put("/api/model-providers/claude_cli/credential",
                   json={"value": "good-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["stored"] is True
    assert body["source"] == "principal"
    assert body["usable"] is True
    assert mc.subscription_token(PRINCIPAL) == "good-token"


def test_the_stored_token_is_what_the_next_turn_spends(
        client, verifier, cli_signed_out, monkeypatch):
    """No restart between Save and the next turn: `_claude_env` reads per call."""
    from services import auth_store, llm_router

    monkeypatch.setattr(auth_store, "operator_id", lambda: PRINCIPAL)
    assert llm_router._claude_env()["CLAUDE_CODE_OAUTH_TOKEN"] == "file-token"
    client.put("/api/model-providers/claude_cli/credential",
               json={"value": "good-token"})
    assert llm_router._claude_env()["CLAUDE_CODE_OAUTH_TOKEN"] == "good-token"


def test_a_paste_error_is_caught_before_any_network_call(client, cli_signed_out):
    """The shape check runs first, so an OpenAI key in the Anthropic field is a
    form error rather than a request."""
    r = client.put("/api/model-providers/anthropic_api/credential",
                   json={"value": "sk-openai-shaped"})
    assert r.status_code == 400
    assert "shape check" in r.json()["detail"]


def test_removing_the_token_falls_back_to_the_file(client, verifier, cli_signed_out):
    client.put("/api/model-providers/claude_cli/credential",
               json={"value": "good-token"})
    r = client.delete("/api/model-providers/claude_cli/credential")
    assert r.status_code == 200
    assert r.json()["stored"] is False
    assert r.json()["source"] == "deployment"
    assert mc.subscription_token(PRINCIPAL) == "file-token"


# ---- the value never leaks ---------------------------------------------------


def test_no_route_hands_a_credential_back(client, verifier, cli_signed_out):
    client.put("/api/model-providers/claude_cli/credential",
               json={"value": "good-token"})
    mc.set_credential(PRINCIPAL, "anthropic_api", "sk-ant-verysecret9999")
    for body in (client.get("/api/model-providers").text,
                 client.put("/api/model-providers/claude_cli/credential",
                            json={"value": "good-token"}).text,
                 client.delete("/api/model-providers/anthropic_api/credential").text):
        assert "good-token" not in body
        assert "verysecret" not in body
