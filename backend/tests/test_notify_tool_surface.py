"""A chat can reach the operator, and the prompt says so exactly where it can.

Two halves, the same two the wake-up surface has.

REACHABLE: declared in the registry, offered by the server, and dispatched to
something that actually sends. A tool that is described but not dispatched is one
the model calls once and never again.

TRUTHFUL, WITH ONE EXTRA CONDITION. Every other chat-scope tool acts on the
platform's own state, so holding the tool is the whole of holding the capability.
This one acts on a channel the installation may not have configured -- so the
grant is the tool AND a destination, and an installation with nowhere to send
offers no tool and tells its chats plainly that they cannot reach anyone. Never
"if it is available": that hedge is the exact shape this project has removed once
already.
"""
from __future__ import annotations

import pytest

from mcp import workshop_query_server as wqs
from services import chat_context as cc
from services import chat_store, orientation, ownership
from services import tool_registry as tr


NOTIFY = "notify_operator"


@pytest.fixture(autouse=True)
def _no_credential_file(monkeypatch):
    """The host may have a real credential file; no test may depend on it."""
    monkeypatch.delenv("WORKSHOP_DISCORD_ENV", raising=False)


@pytest.fixture()
def reachable(monkeypatch):
    """An installation that has declared both halves of a destination."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
    monkeypatch.setenv("WORKSHOP_OPERATOR_DISCORD_USER", "42")


@pytest.fixture()
def unreachable(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("WORKSHOP_OPERATOR_DISCORD_USER", raising=False)


# ---- declared, offered, dispatched -----------------------------------------

def test_the_notify_tool_is_declared_in_the_registry():
    assert tr.NOTIFY_TOOL_ID in tr.PLATFORM_TOOL_IDS


def test_it_is_chat_scope_because_the_message_links_back_to_the_chat():
    tool = next(t for t in tr.PLATFORM_TOOLS if t.id == tr.NOTIFY_TOOL_ID)
    assert tool.scopes == ("chat",)


def test_its_description_has_one_source():
    tool = next(t for t in tr.PLATFORM_TOOLS if t.id == tr.NOTIFY_TOOL_ID)
    assert tool.description == wqs.NOTIFY_TOOL_DESC


def test_every_chat_provider_is_granted_it(reachable):
    """Including the narrow ones. A data chat could do nothing but wait to be
    read; saying "the thing you asked for is done" is not a builder's tool."""
    for key, provider in cc._PLATFORM_PROVIDERS.items():
        assert tr.NOTIFY_TOOL_ID in cc.effective_allowed_tools(provider), (
            f"the {key} chat cannot reach {tr.NOTIFY_TOOL_ID}")


def test_it_rides_the_shared_toolkit_so_narrow_chats_get_it():
    assert tr.NOTIFY_TOOL_ID in tr.WORKSHOP_TOOLKIT_IDS


def test_an_unreachable_installation_grants_it_to_nobody(unreachable):
    """The allowlist is not only what the CLI is launched with -- it is what the
    preamble enumerates. A tool listed there that can only ever fail is the
    told-but-absent error, printed in the paragraph about what the chat can do."""
    for key, provider in cc._PLATFORM_PROVIDERS.items():
        assert tr.NOTIFY_TOOL_ID not in cc.effective_allowed_tools(provider)


@pytest.fixture()
def bound(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    tid = chat_store.create_thread("general")["id"]
    monkeypatch.setenv("WORKSHOP_QUERY_THREAD", tid)
    monkeypatch.setenv("WORKSHOP_QUERY_OWNER", "1")
    monkeypatch.setenv("WORKSHOP_QUERY_SCOPE", "general")
    try:
        yield tid
    finally:
        ownership.clear_current_owner()


def _listed() -> set[str]:
    resp = wqs._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return {t["name"] for t in resp["result"]["tools"]}


def _call(name: str, args: dict | None = None) -> str:
    resp = wqs._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": name, "arguments": args or {}}})
    assert resp is not None
    return resp["result"]["content"][0]["text"]


def test_the_server_offers_it_only_when_a_chat_is_bound(bound, reachable, monkeypatch):
    assert NOTIFY in _listed()
    monkeypatch.delenv("WORKSHOP_QUERY_THREAD")
    assert NOTIFY not in _listed()


def test_the_server_does_not_offer_it_with_nowhere_to_send(bound, unreachable):
    """A tool that is listed and always fails is worse than an absent one: the
    model spends a call on it and then has to report that reporting failed."""
    assert NOTIFY not in _listed()


def test_sending_over_the_wire_reports_the_delivery(bound, reachable, monkeypatch):
    sent = {}

    def _fake_send(text, *, link=""):
        sent["text"] = text
        sent["link"] = link
        return {"ok": True, "message_id": "m9"}

    monkeypatch.setattr("services.operator_notify.send", _fake_send)
    out = _call(NOTIFY, {"message": "the migration finished"})
    assert "Delivered" in out
    assert sent["text"] == "the migration finished"


def test_a_failed_send_is_reported_as_not_delivered(bound, reachable, monkeypatch):
    """The failure mode this whole module exists to avoid: an assistant saying
    it let the operator know, over a message that never arrived."""
    monkeypatch.setattr("services.operator_notify.send",
                        lambda text, **k: {"ok": False, "error": "http 403"})
    out = _call(NOTIFY, {"message": "done"})
    assert "NOT delivered" in out
    assert "http 403" in out
    assert "Traceback" not in out


def test_an_empty_message_says_so_rather_than_sending_nothing(bound, reachable):
    assert "needs a message" in _call(NOTIFY, {"message": "  "})


def test_where_is_names_it_when_the_chat_can_actually_reach_someone(
        bound, reachable, monkeypatch):
    assert NOTIFY in _call("where_is")
    monkeypatch.delenv("DISCORD_BOT_TOKEN")
    assert NOTIFY not in _call("where_is")


# ---- what the prompt says --------------------------------------------------

def test_the_grant_needs_a_destination_as_well_as_the_tool(reachable, monkeypatch):
    provider = cc.get_provider("general")
    assert cc._grants_notify(provider) is True
    monkeypatch.delenv("WORKSHOP_OPERATOR_DISCORD_USER")
    assert cc._grants_notify(provider) is False


def test_the_grant_is_computed_not_assumed_for_both_kinds_of_chat(reachable):
    assert cc._grants_notify(cc.get_provider("general")) is True
    build = cc.get_provider("development")
    if cc.is_build_provider(build):
        assert cc._grants_notify(build) is True


def test_the_chat_that_has_it_is_told_it_has_it():
    block = cc.delegation_block(True, is_build=True, background=True,
                                wakeup=True, notify=True)
    assert NOTIFY in block
    assert "CANNOT REACH THE OPERATOR" not in block


def test_the_chat_that_does_not_is_told_the_opposite_rather_than_nothing():
    block = cc.delegation_block(True, is_build=True, background=True,
                                wakeup=True, notify=False)
    assert NOTIFY not in block
    assert "CANNOT REACH THE OPERATOR" in block


@pytest.mark.parametrize("notify", [True, False])
def test_neither_version_hedges(notify):
    block = cc.delegation_block(True, is_build=True, background=True,
                                wakeup=True, notify=notify)
    lowered = block.lower()
    for hedge in ("if it is available", "if available", "if you have",
                  "may be available", "if configured"):
        assert hedge not in lowered, f"the delegation block hedges: {hedge!r}"


def test_the_granted_paragraph_says_what_not_to_send():
    """Over-use is the likelier failure for this tool: it is an interruption,
    and a notification per step teaches the operator to ignore all of them."""
    block = cc.delegation_block(True, is_build=True, background=True,
                                wakeup=True, notify=True)
    assert "DONE" in block and "BLOCKED" in block
    assert "INTERRUPTION" in block
    assert "progress" in block


def test_the_orientation_map_names_it_only_where_it_is_granted():
    assert NOTIFY in orientation.manifest_text("workshop", notify=True)
    assert NOTIFY not in orientation.manifest_text("workshop", notify=False)


def test_the_preamble_carries_the_real_grant_end_to_end(reachable):
    for key in cc._PLATFORM_PROVIDERS:
        provider = cc.get_provider(key)
        contract = cc.instruction_contract(provider, enable_chat_tools=True)
        assert (NOTIFY in contract) == cc._grants_notify(provider)


def test_an_installation_with_nowhere_to_send_promises_nothing(unreachable):
    for key in cc._PLATFORM_PROVIDERS:
        provider = cc.get_provider(key)
        contract = cc.instruction_contract(provider, enable_chat_tools=True)
        assert NOTIFY not in contract
        assert "CANNOT REACH THE OPERATOR" in contract
