"""The pasted-token path, which was storage-complete and unreachable.

`tracker_credentials.set_token()` and `clear()` shipped with the tracker work
and had ZERO callers: no route reached them, and the connect form never sent
`credential_source`, so every binding silently used the local `gh` login. These
tests cover the routes that close that gap, and the two properties that matter
about a credential surface: a token is verified before it is stored, and no path
ever hands one back.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import ownership, secrets_store, tracker_bindings, tracker_credentials
from routes import trackers as trackers_route


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A tracker API with its own secrets store and its own binding file."""
    monkeypatch.setenv("WORKSHOP_SECRETS_ROOT", str(tmp_path / "secrets"))
    monkeypatch.setattr(tracker_bindings, "_path", lambda: tmp_path / "trackers.json")
    ownership.set_current_owner(6)
    app = FastAPI()
    app.include_router(trackers_route.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def good_token(monkeypatch):
    """A verifier that accepts one token, so no test touches the network."""
    def fake(provider: str, token: str) -> dict:
        if token == "valid-token":
            return {"ok": True, "login": "octocat", "detail": "Verified as octocat."}
        return {"ok": False, "login": "", "detail": "gh: Bad credentials (HTTP 401)"}
    monkeypatch.setattr(tracker_credentials, "verify", fake)


def test_a_bad_token_is_refused_and_not_stored(client, good_token):
    r = client.put("/api/trackers/credentials",
                   json={"provider": "github", "token": "nope"})
    assert r.status_code == 400
    assert "Bad credentials" in r.json()["detail"]
    assert tracker_credentials.token_for("github", "pat") is None


def test_a_good_token_is_stored(client, good_token):
    r = client.put("/api/trackers/credentials",
                   json={"provider": "github", "token": "valid-token"})
    assert r.status_code == 200
    assert r.json()["stored"] is True
    assert r.json()["account"] == "octocat"
    assert tracker_credentials.token_for("github", "pat") == "valid-token"


def test_the_token_never_comes_back_out(client, good_token):
    client.put("/api/trackers/credentials",
               json={"provider": "github", "token": "valid-token"})
    for path in ("/api/trackers", "/api/trackers/credentials"):
        body = client.get("/api/trackers").text
        assert "valid-token" not in body, path


def test_status_reports_presence_only(client, good_token):
    before = tracker_credentials.status("github", "pat")
    assert before["ready"] is False
    client.put("/api/trackers/credentials",
               json={"provider": "github", "token": "valid-token"})
    after = tracker_credentials.status("github", "pat")
    assert after["ready"] is True
    assert "valid-token" not in repr(after)


def test_forgetting_a_token_clears_it(client, good_token):
    client.put("/api/trackers/credentials",
               json={"provider": "github", "token": "valid-token"})
    r = client.delete("/api/trackers/credentials?provider=github")
    assert r.status_code == 200
    assert r.json()["stored"] is False
    assert tracker_credentials.token_for("github", "pat") is None


def test_clearing_when_nothing_is_stored_is_not_an_error(client):
    assert client.delete("/api/trackers/credentials?provider=github").status_code == 200


def test_an_empty_token_is_rejected_by_validation(client):
    assert client.put("/api/trackers/credentials",
                      json={"provider": "github", "token": ""}).status_code == 422


def test_the_sources_are_listed_with_honest_readiness(client):
    j = client.get("/api/trackers").json()
    by = {s["source"]: s for s in j["credential_sources"]}
    assert set(by) == set(tracker_credentials.SOURCES)
    # device_flow cannot work until an App is registered on github.com, and it
    # says so rather than presenting itself as an option that will work.
    assert by["device_flow"]["ready"] is False
    assert "client id" in by["device_flow"]["detail"]
    assert by["pat"]["ready"] is False


def test_source_can_change_without_disconnecting(client, good_token, monkeypatch):
    tracker_bindings.connect(provider="github", target="octocat/tickets")
    assert tracker_bindings.load()["credential_source"] == "gh"
    r = client.post("/api/trackers/credential-source", json={"source": "pat"})
    assert r.status_code == 200
    assert tracker_bindings.load()["credential_source"] == "pat"
    # The connection and its targets survived, which is the whole point.
    assert r.json()["connection"]["targets"]["workshop"] == "octocat/tickets"


def test_an_unknown_source_is_refused(client):
    tracker_bindings.connect(provider="github", target="octocat/tickets")
    r = client.post("/api/trackers/credential-source", json={"source": "telepathy"})
    assert r.status_code == 400


def test_changing_source_with_nothing_connected_is_refused(client):
    r = client.post("/api/trackers/credential-source", json={"source": "pat"})
    assert r.status_code == 400


def test_a_pat_binding_reports_not_ready_until_a_token_is_stored(client, good_token):
    tracker_bindings.connect(provider="github", target="octocat/tickets",
                             credential_source="pat")
    assert client.get("/api/trackers").json()["credential"]["ready"] is False
    client.put("/api/trackers/credentials",
               json={"provider": "github", "token": "valid-token"})
    assert client.get("/api/trackers").json()["credential"]["ready"] is True


def test_env_for_passes_the_token_to_gh(client, good_token):
    """The adapter's whole use of the seam: GH_TOKEN, and GITHUB_TOKEN removed so
    a stale one in the environment cannot win."""
    client.put("/api/trackers/credentials",
               json={"provider": "github", "token": "valid-token"})
    env = tracker_credentials.env_for({"provider": "github", "credential_source": "pat"})
    assert env["GH_TOKEN"] == "valid-token"
    assert "GITHUB_TOKEN" not in env


def test_gh_source_holds_no_token_of_its_own(client):
    """`gh` injects its own credential; there is nothing for us to store."""
    assert tracker_credentials.token_for("github", "gh") is None
    env = tracker_credentials.env_for({"provider": "github", "credential_source": "gh"})
    assert "GH_TOKEN" not in env


def test_secrets_are_written_0600(client, good_token, tmp_path):
    client.put("/api/trackers/credentials",
               json={"provider": "github", "token": "valid-token"})
    p = secrets_store._path("tracker:github")
    assert p.exists()
    assert oct(p.stat().st_mode & 0o777) == "0o600"


def test_verify_rejects_an_empty_token_without_shelling_out():
    assert tracker_credentials.verify("github", "   ")["ok"] is False


def test_verify_is_honest_about_providers_it_cannot_check():
    out = tracker_credentials.verify("linear", "some-token")
    assert out["ok"] is True
    assert "without checking" in out["detail"]
