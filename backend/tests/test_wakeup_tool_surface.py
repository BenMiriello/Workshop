"""The wake-up is reachable, and the prompt says so exactly where it is true.

Two halves, and the second is the one the operator asked for by name.

REACHABLE: the tool has to be declared in the registry, offered by the server,
and dispatched to something that actually arms an alarm. A tool that is described
but not dispatched is a tool the model calls once and never again.

TRUTHFUL: no hedging, in either direction. "Use wake_me if it is available" is
the exact shape this project has already removed once -- it spends the tokens of
naming a capability and still leaves the reader to find out whether it is real.
Where the grant exists the prompt states it; where it does not the prompt says
plainly that nothing wakes this chat, so no assistant promises a return it cannot
make.
"""
from __future__ import annotations


import pytest

from mcp import workshop_query_server as wqs
from services import chat_context as cc
from services import chat_store, orientation, ownership
from services import tool_registry as tr
from services import wakeups


# ---- declared, offered, dispatched -----------------------------------------

def test_the_wakeup_tools_are_declared_in_the_registry():
    ids = tr.PLATFORM_TOOL_IDS
    for tid in tr.WAKEUP_TOOL_IDS:
        assert tid in ids, f"{tid} is granted but has no PLATFORM_TOOLS entry"


def test_they_are_chat_scope_because_an_alarm_needs_a_thread_to_ring_on():
    for tid in tr.WAKEUP_TOOL_IDS:
        tool = next(t for t in tr.PLATFORM_TOOLS if t.id == tid)
        assert tool.scopes == ("chat",)


def test_their_descriptions_have_one_source():
    """The registry reads the server's own constants; duplicating the prose is
    how a catalog starts describing a tool the server no longer exposes."""
    wake = next(t for t in tr.PLATFORM_TOOLS if t.id.endswith("wake_me"))
    assert wake.description == wqs.WAKE_TOOL_DESC


def test_every_chat_provider_is_granted_them():
    """A narrow data chat had no way to be woken at ALL: no backgrounded Bash,
    no Monitor, nothing. It is the provider that needs this most."""
    for key, provider in cc._PLATFORM_PROVIDERS.items():
        granted = cc.effective_allowed_tools(provider)
        for tid in tr.WAKEUP_TOOL_IDS:
            assert tid in granted, f"the {key} chat cannot reach {tid}"


@pytest.fixture()
def bound(tmp_path, monkeypatch):
    """A server instance bound to a real chat, as a turn's is."""
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


def _call(name: str, args: dict | None = None) -> str:
    resp = wqs._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": name, "arguments": args or {}}})
    assert resp is not None
    return resp["result"]["content"][0]["text"]


def _listed() -> set[str]:
    resp = wqs._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return {t["name"] for t in resp["result"]["tools"]}


def test_the_server_offers_them_only_when_a_chat_is_bound(bound, monkeypatch):
    assert {"wake_me", "list_wakeups", "cancel_wakeup"} <= _listed()
    monkeypatch.delenv("WORKSHOP_QUERY_THREAD")
    assert not ({"wake_me", "list_wakeups", "cancel_wakeup"} & _listed())


def test_arming_listing_and_cancelling_over_the_wire(bound):
    out = _call("wake_me", {"reason": "check whether the deploy settled",
                            "in_minutes": 45})
    assert "Wake-up armed" in out
    wake_id = next(w["id"] for w in wakeups.read(bound))
    assert wake_id in out

    listed = _call("list_wakeups")
    assert wake_id in listed and "check whether the deploy settled" in listed

    assert "Cancelled" in _call("cancel_wakeup", {"id": wake_id})
    assert wakeups.read(bound) == []
    assert "No wake-ups are armed" in _call("list_wakeups")


def test_a_time_it_cannot_honour_comes_back_as_a_sentence_not_a_trace(bound):
    out = _call("wake_me", {"reason": "x", "at": "some time next week"})
    assert "No wake-up was armed" in out
    assert "Traceback" not in out
    assert wakeups.read(bound) == []


def test_cancelling_something_that_is_not_armed_says_so_plainly(bound):
    assert "No wake-up with id" in _call("cancel_wakeup", {"id": "wake_nope"})


def test_where_is_names_the_alarm_when_the_chat_has_one(bound, monkeypatch):
    assert "wake_me" in _call("where_is")
    monkeypatch.delenv("WORKSHOP_QUERY_THREAD")
    assert "wake_me" not in _call("where_is")


# ---- what the prompt says --------------------------------------------------

def test_the_grant_is_computed_not_assumed_for_both_kinds_of_chat():
    """A build provider declares an EMPTY allowlist, which means the full
    agentic surface, not "no tools". Reading it literally is what made the
    background paragraph absent from the chats that most obviously had it."""
    assert cc._grants_wakeup(cc.get_provider("general")) is True
    build = cc.get_provider("development")
    if cc.is_build_provider(build):
        assert cc._grants_wakeup(build) is True
        assert cc._grants_background(build) is True


def test_the_chat_that_has_it_is_told_it_has_it():
    block = cc.delegation_block(True, is_build=True, background=True, wakeup=True)
    assert "wake_me" in block
    assert "list_wakeups" in block and "cancel_wakeup" in block
    assert "NOTHING WAKES YOU LATER" not in block


def test_the_chat_that_does_not_is_told_the_opposite_rather_than_nothing():
    block = cc.delegation_block(True, is_build=True, background=True, wakeup=False)
    assert "wake_me" not in block
    assert "NOTHING WAKES YOU LATER" in block


@pytest.mark.parametrize("wakeup", [True, False])
def test_neither_version_hedges(wakeup):
    """The operator's rule, stated twice: say it has the tool, or do not mention
    the tool. Never "if it is available"."""
    block = cc.delegation_block(True, is_build=True, background=True, wakeup=wakeup)
    lowered = block.lower()
    for hedge in ("if it is available", "if available", "if you have", "may be available"):
        assert hedge not in lowered, f"the delegation block hedges: {hedge!r}"


def test_the_orientation_map_names_it_only_where_it_is_granted():
    assert "wake_me" in orientation.manifest_text("workshop", wakeup=True)
    assert "wake_me" not in orientation.manifest_text("workshop", wakeup=False)


def test_the_preamble_carries_the_real_grant_end_to_end():
    """The whole chain, from the provider's allowlist to the text: whatever
    `_grants_wakeup` answers is what the preamble says."""
    for key in cc._PLATFORM_PROVIDERS:
        provider = cc.get_provider(key)
        contract = cc.instruction_contract(provider, enable_chat_tools=True)
        assert ("wake_me" in contract) == cc._grants_wakeup(provider)


# ---- the clock -------------------------------------------------------------

def test_the_turn_contract_carries_a_clock():
    """It was not in the prompt at all: no time of day, no zone, no tool. A chat
    asked to come back after lunch had to shell out to find out what it was."""
    text = cc.turn_contract(0)
    assert "=== THE TIME NOW ===" in text
    from services import installation

    now = installation.local_now()
    assert now.strftime("%H:%M") in text


def test_the_clock_names_the_installations_zone(monkeypatch):
    monkeypatch.setenv("WORKSHOP_TIMEZONE", "America/Los_Angeles")
    line = cc.clock_line()
    assert "America/Los_Angeles" in line
    assert "UTC-0" in line or "UTC+0" in line


def test_the_clock_moves(monkeypatch):
    """It goes on the per-turn contract rather than the once-only prefix, which
    is the whole reason it is here and not in the orientation manifest: a clock
    sent at session spawn and never again is a stale number the model trusts."""
    from services import installation

    monkeypatch.setenv("WORKSHOP_TIMEZONE", "UTC")
    first = cc.clock_line()
    real = installation.local_now
    monkeypatch.setattr(installation, "local_now",
                        lambda: real().replace(year=2027))
    assert cc.clock_line() != first
    assert "2027" in cc.clock_line()


def test_the_clock_survives_an_installation_that_declares_no_zone(monkeypatch):
    monkeypatch.setenv("WORKSHOP_TIMEZONE", "")
    monkeypatch.setenv("TZ", "")
    monkeypatch.setattr("services.installation._host_zone_name", lambda: "")
    line = cc.clock_line()
    assert "=== THE TIME NOW ===" in line
    assert "UTC" in line  # the offset is always knowable even when the name is not


def test_an_unknown_zone_falls_back_rather_than_raising(monkeypatch):
    monkeypatch.setenv("WORKSHOP_TIMEZONE", "Mars/Olympus_Mons")
    assert cc.clock_line().startswith("=== THE TIME NOW ===")
