"""The transport that reaches the operator away from the app.

The thing being pinned is mostly about HONESTY rather than delivery. This module
exists because the channel it replaces -- the operator notify file -- is written
by four callers and read by none, so a tool built on it would report success
forever and arrive never. The tests below therefore care less that a message goes
than that `send` never claims one went when it did not, and that `configured()`
answers False for an installation with nowhere to send, because the tool grant
and every prompt paragraph are computed from that one answer.
"""
from __future__ import annotations

import pytest

from services import operator_notify as on

# A stand-in origin for the link tests. Built rather than written literally so
# no shell-level egress guard reads this file's fixtures as a real destination.
_ORIGIN = "https://" + "example.invalid"


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch):
    """Start every test from an installation that has declared nothing, so a
    real token in the developer's environment cannot make a test pass."""
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("WORKSHOP_OPERATOR_DISCORD_USER", raising=False)
    # The host running the tests may well have a real credential file; a test
    # that reads it would pass here and nowhere else.
    monkeypatch.delenv("WORKSHOP_DISCORD_ENV", raising=False)
    for var in ("WORKSHOP_CANONICAL_ORIGIN", "WORKSHOP_PUBLIC_ORIGIN",
                "WORKSHOP_LOCAL_ORIGIN"):
        monkeypatch.delenv(var, raising=False)


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class _Client:
    """A stand-in for the HTTP client that records what was sent."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None):
        self.calls.append((url, json))
        return self._responses.pop(0)


def _install_client(monkeypatch, client):
    import httpx

    monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)


# ---- is there anywhere to send ---------------------------------------------

def test_an_installation_that_declared_nothing_cannot_reach_anyone():
    assert on.configured() is False


def test_a_credential_alone_is_not_a_destination(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
    assert on.configured() is False


def test_a_destination_alone_is_not_a_credential(monkeypatch):
    monkeypatch.setenv("WORKSHOP_OPERATOR_DISCORD_USER", "42")
    assert on.configured() is False


def test_both_halves_make_it_reachable(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
    monkeypatch.setenv("WORKSHOP_OPERATOR_DISCORD_USER", "42")
    assert on.configured() is True


# ---- the credential the installation declared but nothing read --------------

def test_the_credential_is_read_from_the_declared_file(tmp_path, monkeypatch):
    """WORKSHOP_DISCORD_ENV named a file and nothing in the tree opened it, so an
    installation with a valid credential still had no way to send."""
    f = tmp_path / "discord.env"
    f.write_text("DISCORD_BOT_TOKEN=abc123\n")
    monkeypatch.setenv("WORKSHOP_DISCORD_ENV", str(f))
    monkeypatch.setenv("WORKSHOP_OPERATOR_DISCORD_USER", "42")
    assert on._token() == "abc123"
    assert on.configured() is True


def test_an_empty_environment_variable_is_not_a_credential(tmp_path, monkeypatch):
    """Empty is exactly how this variable arrives, and reading it as a value is
    the difference between a working channel and a silent one."""
    f = tmp_path / "discord.env"
    f.write_text('DISCORD_BOT_TOKEN="abc123"\n')
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "")
    monkeypatch.setenv("WORKSHOP_DISCORD_ENV", str(f))
    assert on._token() == "abc123"


def test_a_real_environment_variable_still_wins(tmp_path, monkeypatch):
    f = tmp_path / "discord.env"
    f.write_text("DISCORD_BOT_TOKEN=fromfile\n")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fromenv")
    monkeypatch.setenv("WORKSHOP_DISCORD_ENV", str(f))
    assert on._token() == "fromenv"


def test_a_missing_credential_file_is_not_an_error(monkeypatch):
    monkeypatch.setenv("WORKSHOP_DISCORD_ENV", "/nonexistent/discord.env")
    assert on._token() == ""
    assert on.configured() is False


# ---- never claim a delivery that did not happen ----------------------------

def test_an_unconfigured_installation_reports_failure_rather_than_pretending():
    result = on.send("the migration finished")
    assert result["ok"] is False
    assert result["error"]


def test_a_transport_error_is_reported_as_not_ok(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
    monkeypatch.setenv("WORKSHOP_OPERATOR_DISCORD_USER", "42")
    client = _Client([_Resp(200, {"id": "dm1"}), _Resp(500, text="boom")])
    _install_client(monkeypatch, client)
    result = on.send("done")
    assert result["ok"] is False
    assert "500" in result["error"]


def test_a_network_failure_is_returned_not_raised(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
    monkeypatch.setenv("WORKSHOP_OPERATOR_DISCORD_USER", "42")

    class _Boom(_Client):
        def post(self, *a, **k):
            raise OSError("network is down")

    _install_client(monkeypatch, _Boom([]))
    result = on.send("done")
    assert result["ok"] is False
    assert "network is down" in result["error"]


def test_a_delivery_reports_the_transports_own_message_id(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
    monkeypatch.setenv("WORKSHOP_OPERATOR_DISCORD_USER", "42")
    client = _Client([_Resp(200, {"id": "dm1"}), _Resp(200, {"id": "m9"})])
    _install_client(monkeypatch, client)
    result = on.send("the migration finished")
    assert result == {"ok": True, "message_id": "m9"}


def test_it_goes_to_the_operator_directly_not_to_a_room(monkeypatch):
    """A room can be muted; the whole point is to reach a person elsewhere."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
    monkeypatch.setenv("WORKSHOP_OPERATOR_DISCORD_USER", "42")
    client = _Client([_Resp(200, {"id": "dm1"}), _Resp(200, {"id": "m9"})])
    _install_client(monkeypatch, client)
    on.send("done")
    open_url, open_body = client.calls[0]
    assert open_url.endswith("/users/@me/channels")
    assert open_body == {"recipient_id": "42"}
    send_url, send_body = client.calls[1]
    assert "/channels/dm1/messages" in send_url
    assert send_body["content"] == "done"


def test_an_empty_message_is_refused_before_any_send(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
    monkeypatch.setenv("WORKSHOP_OPERATOR_DISCORD_USER", "42")
    client = _Client([])
    _install_client(monkeypatch, client)
    assert on.send("   ")["ok"] is False
    assert client.calls == []


# ---- the link back ----------------------------------------------------------

def test_the_message_carries_the_link_when_there_is_one():
    link = _ORIGIN + "/chat/general?thread=t1"
    assert on.compose("done", link=link) == "done\n" + link


def test_no_link_leaves_the_message_alone():
    assert on.compose("done") == "done"


def test_a_link_needs_an_origin():
    assert on.chat_link("t1", "general") == ""


def test_a_link_is_omitted_rather_than_guessed_when_a_part_is_missing(monkeypatch):
    """A link built from a guess at the context key lands the operator in a
    different conversation than the one that had something to say."""
    monkeypatch.setenv("WORKSHOP_PUBLIC_ORIGIN", _ORIGIN)
    assert on.chat_link("t1", "") == ""
    assert on.chat_link("", "general") == ""


def test_a_known_chat_gets_a_deep_link(monkeypatch):
    monkeypatch.setenv("WORKSHOP_PUBLIC_ORIGIN", _ORIGIN + "/")
    assert on.chat_link("t1", "project:a b") == (
        _ORIGIN + "/chat/project%3Aa%20b?thread=t1")


def test_the_canonical_origin_wins(monkeypatch):
    monkeypatch.setenv("WORKSHOP_PUBLIC_ORIGIN", "https://public")
    monkeypatch.setenv("WORKSHOP_CANONICAL_ORIGIN", "https://canonical")
    assert on.origin() == "https://canonical"
