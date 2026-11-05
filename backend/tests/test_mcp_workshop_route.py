"""The workshop tool server over HTTP: signed turn claims are the only way in,
and a call runs with exactly the context its claims name."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from mcp import turn_context  # noqa: E402
from routes import mcp_workshop  # noqa: E402
from services import turn_claims  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path))
    monkeypatch.setattr(turn_claims, "_KEY", None)
    app = FastAPI()
    app.include_router(mcp_workshop.router)
    return TestClient(app)


def _rpc(client, token, method, params=None, mid=1):
    return client.post("/mcp/workshop", headers={"Authorization": f"Bearer {token}"},
                       json={"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def test_claims_round_trip_and_reject_tampering(client):
    tok = turn_claims.mint({"WORKSHOP_QUERY_SCOPE": "project:x", "WORKSHOP_QUERY_OWNER": "7"})
    assert turn_claims.verify(tok) == {"WORKSHOP_QUERY_SCOPE": "project:x", "WORKSHOP_QUERY_OWNER": "7"}
    payload, mac = tok.split(".")
    assert turn_claims.verify(payload + "." + "0" * len(mac)) is None
    assert turn_claims.verify("") is None


def test_expired_claims_are_refused(client):
    assert turn_claims.verify(turn_claims.mint({"a": "b"}, ttl_s=-1)) is None


def test_a_call_without_valid_claims_is_refused(client):
    r = client.post("/mcp/workshop", headers={"Authorization": "Bearer nope.nope"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401


def test_a_notification_is_accepted_without_a_body(client):
    tok = turn_claims.mint({"WORKSHOP_QUERY_SCOPE": "development"})
    r = client.post("/mcp/workshop", headers={"Authorization": f"Bearer {tok}"},
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202


def test_the_call_sees_its_own_claims_and_nothing_leaks_after(client, monkeypatch):
    seen = {}

    def fake_handle(msg):
        seen["scope"] = turn_context.get("WORKSHOP_QUERY_SCOPE")
        seen["http"] = turn_context.over_http()
        return {"jsonrpc": "2.0", "id": msg["id"], "result": {}}

    from mcp import workshop_query_server
    monkeypatch.setattr(workshop_query_server, "_handle", fake_handle)
    tok = turn_claims.mint({"WORKSHOP_QUERY_SCOPE": "project:x"})
    assert _rpc(client, tok, "ping").json() == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert seen == {"scope": "project:x", "http": True}
    assert turn_context.over_http() is False


def test_streams_are_not_offered(client):
    assert client.get("/mcp/workshop").status_code == 405


def test_the_key_survives_a_new_process(client, tmp_path, monkeypatch):
    tok = turn_claims.mint({"k": "v"})
    monkeypatch.setattr(turn_claims, "_KEY", None)
    assert turn_claims.verify(tok) == {"k": "v"}
    assert (tmp_path / "turn-claims.key").stat().st_mode & 0o777 == 0o600
