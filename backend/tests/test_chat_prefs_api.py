"""The chat-settings routes, over HTTP.

The default mode is only useful if the surface that creates chats can read it and
the settings screen can write it, so these go through the router rather than the
store: a route that isn't wired 404s no matter how correct the service is.
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import chat as chat_routes  # noqa: E402
from services import ownership  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/chat")
    try:
        yield TestClient(app)
    finally:
        ownership.clear_current_owner()


class TestPrefs:
    def test_the_platform_default_is_reported(self, client):
        r = client.get("/chat/prefs")
        assert r.status_code == 200
        assert r.json()["default_mode"] == "direct"

    def test_the_choices_are_named_by_the_server(self, client):
        """The settings control builds from these, so the server owns the list."""
        assert client.get("/chat/prefs").json()["modes"] == ["direct", "orchestrated"]

    def test_setting_it_sticks(self, client):
        assert client.post("/chat/prefs", json={"default_mode": "orchestrated"}).status_code == 200
        assert client.get("/chat/prefs").json()["default_mode"] == "orchestrated"

    def test_junk_is_refused_rather_than_stored(self, client):
        assert client.post("/chat/prefs", json={"default_mode": "sideways"}).status_code == 400
        assert client.get("/chat/prefs").json()["default_mode"] == "direct"


class TestTurnPostureDefaults:
    """The other two prefs: the effort and permission mode a chat resolves
    through when it never picked for itself. Unlike `default_mode` these are NOT
    baked in at creation — they are consulted per turn, so the settings screen
    needs to be able to set them and to clear them again."""

    def test_no_preference_is_reported_as_null(self, client):
        p = client.get("/chat/prefs").json()
        assert p["default_effort"] is None
        assert p["default_permission_mode"] is None

    def test_the_choices_are_named_by_the_server(self, client):
        p = client.get("/chat/prefs").json()
        assert p["efforts"] == ["low", "medium", "high", "xhigh", "max"]
        # Bypass is not on offer, so it cannot be defaulted to either.
        assert "bypassPermissions" not in p["permission_modes"]
        assert p["platform_permission_mode"] == "auto"

    def test_setting_them_sticks(self, client):
        r = client.post("/chat/prefs",
                        json={"default_effort": "max", "default_permission_mode": "plan"})
        assert r.status_code == 200
        p = client.get("/chat/prefs").json()
        assert p["default_effort"] == "max"
        assert p["default_permission_mode"] == "plan"

    def test_an_empty_string_clears_one(self, client):
        client.post("/chat/prefs", json={"default_effort": "max"})
        client.post("/chat/prefs", json={"default_effort": ""})
        assert client.get("/chat/prefs").json()["default_effort"] is None

    def test_a_field_that_is_not_sent_is_left_alone(self, client):
        """The settings screen must be able to save one control without echoing
        the others back — otherwise every save races the others."""
        client.post("/chat/prefs", json={"default_effort": "low"})
        client.post("/chat/prefs", json={"default_mode": "orchestrated"})
        p = client.get("/chat/prefs").json()
        assert p["default_effort"] == "low"
        assert p["default_mode"] == "orchestrated"

    def test_junk_is_refused(self, client):
        assert client.post("/chat/prefs", json={"default_effort": "ultra"}).status_code == 400
        assert client.post(
            "/chat/prefs", json={"default_permission_mode": "bypassPermissions"}).status_code == 400
        p = client.get("/chat/prefs").json()
        assert p["default_effort"] is None
        assert p["default_permission_mode"] is None


class TestCreateWithMode:
    def test_a_new_chat_follows_the_default(self, client):
        client.post("/chat/prefs", json={"default_mode": "orchestrated"})
        r = client.post("/chat/threads", json={"context_key": "general"})
        assert r.json()["mode"] == "orchestrated"

    def test_a_mode_chosen_on_the_blank_chat_wins(self, client):
        """The toolbar shows the control before the thread exists; the choice
        rides along on creation, or it is silently lost on the first message."""
        client.post("/chat/prefs", json={"default_mode": "orchestrated"})
        r = client.post("/chat/threads", json={"context_key": "general", "mode": "direct"})
        assert r.json()["mode"] == "direct"

    def test_a_bad_mode_is_refused(self, client):
        assert client.post("/chat/threads",
                           json={"context_key": "general", "mode": "sideways"}).status_code == 400
