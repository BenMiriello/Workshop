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
        assert client.get("/chat/prefs").json()["modes"] == ["direct", "auto"]

    def test_the_retired_mode_is_not_offered(self, client):
        """`orchestrated` collapsed into `auto` on 2026-08-27. The settings
        control builds from this list, so leaving it here would let the operator
        put new chats back on a mode that no longer exists."""
        assert "orchestrated" not in client.get("/chat/prefs").json()["modes"]

    def test_setting_it_sticks(self, client):
        assert client.post("/chat/prefs", json={"default_mode": "auto"}).status_code == 200
        assert client.get("/chat/prefs").json()["default_mode"] == "auto"

    def test_a_retired_default_is_mapped_forward_not_refused(self, client):
        """A settings tab opened before the collapse still sends `orchestrated`.
        It stores as `auto`, which is the same behaviour under its surviving
        name -- not a 400, and specifically not `direct`."""
        assert client.post(
            "/chat/prefs", json={"default_mode": "orchestrated"}).status_code == 200
        assert client.get("/chat/prefs").json()["default_mode"] == "auto"

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
        client.post("/chat/prefs", json={"default_mode": "auto"})
        p = client.get("/chat/prefs").json()
        assert p["default_effort"] == "low"
        assert p["default_mode"] == "auto"

    def test_junk_is_refused(self, client):
        assert client.post("/chat/prefs", json={"default_effort": "ultra"}).status_code == 400
        assert client.post(
            "/chat/prefs", json={"default_permission_mode": "bypassPermissions"}).status_code == 400
        p = client.get("/chat/prefs").json()
        assert p["default_effort"] is None
        assert p["default_permission_mode"] is None


class TestCreateWithMode:
    def test_a_new_chat_follows_the_default(self, client):
        client.post("/chat/prefs", json={"default_mode": "auto"})
        r = client.post("/chat/threads", json={"context_key": "general"})
        assert r.json()["mode"] == "auto"

    def test_a_mode_chosen_on_the_blank_chat_wins(self, client):
        """The toolbar shows the control before the thread exists; the choice
        rides along on creation, or it is silently lost on the first message."""
        client.post("/chat/prefs", json={"default_mode": "auto"})
        r = client.post("/chat/threads", json={"context_key": "general", "mode": "direct"})
        assert r.json()["mode"] == "direct"

    def test_a_bad_mode_is_refused(self, client):
        assert client.post("/chat/threads",
                           json={"context_key": "general", "mode": "sideways"}).status_code == 400


class TestTimezone:
    """The user's own zone, which nothing could express before: the clock came
    from WORKSHOP_TIMEZONE or from whatever the host was set to, and a host's
    zone is not a person's zone.

    UTC stays canonical either way -- this setting changes what times are
    PRESENTED as, never what is stored -- so an unset value is a real answer and
    resolves to UTC labelled as UTC rather than to a guess.
    """

    def test_unset_is_reported_as_null_with_the_zone_in_force_beside_it(self, client, monkeypatch):
        """Two fields, because "what did I set" and "what is in force" are
        different questions. Without the second, an unset setting renders as a
        blank next to times that are plainly in some zone."""
        monkeypatch.setenv("WORKSHOP_TIMEZONE", "Asia/Tokyo")
        p = client.get("/chat/prefs").json()
        assert p["timezone"] is None
        assert p["resolved_timezone"] == "Asia/Tokyo"
        assert p["timezone_source"] == "installation"

    def test_setting_it_sticks_and_outranks_the_installation(self, client, monkeypatch):
        monkeypatch.setenv("WORKSHOP_TIMEZONE", "Asia/Tokyo")
        assert client.post("/chat/prefs",
                           json={"timezone": "Europe/Berlin"}).status_code == 200
        p = client.get("/chat/prefs").json()
        assert p["timezone"] == "Europe/Berlin"
        assert p["resolved_timezone"] == "Europe/Berlin"
        assert p["timezone_source"] == "user"

    def test_an_empty_string_clears_it(self, client, monkeypatch):
        """Clearing has to be expressible: the setting is OPTIONAL, so a user
        who set it by accident needs a way back to "not set"."""
        monkeypatch.setenv("WORKSHOP_TIMEZONE", "Asia/Tokyo")
        client.post("/chat/prefs", json={"timezone": "Europe/Berlin"})
        client.post("/chat/prefs", json={"timezone": ""})
        p = client.get("/chat/prefs").json()
        assert p["timezone"] is None
        assert p["resolved_timezone"] == "Asia/Tokyo"

    def test_with_nothing_declared_anywhere_it_resolves_to_utc(self, client, monkeypatch):
        monkeypatch.setenv("WORKSHOP_TIMEZONE", "")
        monkeypatch.setenv("TZ", "")
        monkeypatch.setattr("services.installation._host_zone_name", lambda: "")
        p = client.get("/chat/prefs").json()
        assert p["resolved_timezone"] == "UTC"
        assert p["timezone_source"] == "utc"

    def test_a_zone_that_is_not_a_zone_is_refused_rather_than_stored(self, client):
        """A stored name this host cannot load would read back as "not set" and
        the screen would show UTC beside the name just picked."""
        assert client.post("/chat/prefs",
                           json={"timezone": "Mars/Olympus_Mons"}).status_code == 400
        assert client.get("/chat/prefs").json()["timezone"] is None
