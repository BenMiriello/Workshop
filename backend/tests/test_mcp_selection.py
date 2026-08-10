"""Per-chat MCP server selection: the argv it produces, and the round trip.

Two things must be true and both are easy to get silently wrong.

**The default must be invisible.** A chat that has never opened the control has
`mcp_servers_enabled` absent, which means ALL servers, which must produce the
byte-identical argv it produced before this feature existed. `--strict-mcp-config`
is the hazard here: it does not error on an omission, it just removes a server's
tools from the turn. A regression would look like a chat that has quietly lost
Plaid, discovered days later.

**A subset must actually reach the CLI.** Not "is stored", not "is displayed" —
appears in argv, with a real launch spec read off real configuration.

The argv assertions run against `llm_router.tool_flags`, the one place all three
tool modes are built, so a streamed turn and a blocking one cannot drift.
"""
from __future__ import annotations

import json

import pytest

from services import chat_store, llm_router, mcp_registry, ownership


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    """A pinned data owner writing into a throwaway runtime dir — the chat store
    is owner-scoped, so there is no such thing as a thread without one."""
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield
    finally:
        ownership.clear_current_owner()


def flag_value(cmd: list[str], flag: str) -> str | None:
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


def mcp_json(cmd: list[str]) -> dict:
    raw = flag_value(cmd, "--mcp-config")
    assert raw is not None, f"no --mcp-config in {cmd}"
    return json.loads(raw)["mcpServers"]


def allowed(cmd: list[str]) -> list[str]:
    """Everything after --allowedTools up to the next flag. The option is
    variadic, so its value is a run of arguments, not one."""
    i = cmd.index("--allowedTools") + 1
    out = []
    while i < len(cmd) and not cmd[i].startswith("--"):
        out.append(cmd[i])
        i += 1
    return out


SPECS = {
    "plaid": {"type": "stdio", "command": "node", "args": ["/srv/plaid.js"]},
    "workshop-query": {"type": "stdio", "command": "node", "args": ["/srv/wq.js"]},
}


@pytest.fixture
def app_specs(monkeypatch):
    """The app-registered launch specs the narrow path draws on, pinned so the
    argv under test does not depend on which apps happen to be installed."""
    monkeypatch.setattr(llm_router, "_mcp_server_specs", lambda: dict(SPECS))
    return SPECS


# ---- the pure selectors ----------------------------------------------------


class TestSelectors:
    def test_no_selection_is_everything(self):
        assert llm_router.select_names(["a", "b"], None) == ["a", "b"]
        assert llm_router.select_servers(SPECS, None) == SPECS

    def test_a_selection_narrows_and_keeps_order(self):
        assert llm_router.select_names(["a", "b", "c"], ["c", "a"]) == ["a", "c"]

    def test_an_empty_selection_is_none_of_them(self):
        assert llm_router.select_names(["a", "b"], []) == []
        assert llm_router.select_servers(SPECS, []) == {}

    def test_a_selection_cannot_ADD_a_server(self):
        # A stale checkbox naming a server this path never offered must not
        # conjure it into the turn.
        assert llm_router.select_names(["a"], ["a", "ghost"]) == ["a"]
        assert "ghost" not in llm_router.select_servers(SPECS, ["plaid", "ghost"])

    def test_tools_of_a_disabled_server_leave_the_allowlist(self):
        tools = ["WebSearch", "mcp__plaid__get_balances", "mcp__workshop-query__ask"]
        kept = llm_router.filter_allowed_tools(tools, {"plaid": {}}, ["plaid"])
        assert kept == ["WebSearch", "mcp__plaid__get_balances"]

    def test_builtins_are_never_touched_by_a_selection(self):
        tools = ["WebSearch", "WebFetch", "Task"]
        assert llm_router.filter_allowed_tools(tools, {}, []) == tools

    def test_no_selection_leaves_the_allowlist_identical(self):
        tools = ["WebSearch", "mcp__plaid__get_balances"]
        assert llm_router.filter_allowed_tools(tools, {}, None) == tools


# ---- the argv, narrow path (general / data chats) --------------------------


class TestNarrowPathArgv:
    def test_unchosen_chat_gets_exactly_what_it_always_got(self, app_specs):
        tools = ["WebSearch", "mcp__plaid__get_balances"]
        before, _env, _b = llm_router.tool_flags(
            allowed_tools=tools, mcp_servers=["plaid"], permission_mode="auto")
        after, _env2, _b2 = llm_router.tool_flags(
            allowed_tools=tools, mcp_servers=["plaid"], permission_mode="auto",
            mcp_selection=None)
        assert before == after
        assert mcp_json(after) == {"plaid": SPECS["plaid"]}

    def test_a_subset_reaches_the_mcp_config(self, app_specs):
        cmd, _env, block = llm_router.tool_flags(
            allowed_tools=["WebSearch", "mcp__plaid__get_balances",
                           "mcp__workshop-query__ask"],
            mcp_servers=["plaid", "workshop-query"],
            permission_mode="auto",
            mcp_selection=["workshop-query"],
        )
        assert "--strict-mcp-config" in cmd
        assert mcp_json(cmd) == {"workshop-query": SPECS["workshop-query"]}
        # ...and plaid's tool is gone from the allowlist with it.
        assert allowed(cmd) == ["WebSearch", "mcp__workshop-query__ask"]
        assert block is True

    def test_turning_everything_off_still_spawns_a_valid_argv(self, app_specs):
        # The regression this pins: an empty --allowedTools written as a bare
        # flag would swallow --permission-mode as its value.
        cmd, env, block = llm_router.tool_flags(
            allowed_tools=["mcp__plaid__get_balances"],
            mcp_servers=["plaid"], permission_mode="auto", mcp_selection=[])
        assert mcp_json(cmd) == {}
        assert flag_value(cmd, "--permission-mode") == "auto"
        assert allowed(cmd) == [""]
        assert env == {} and block is False

    def test_the_variadic_mcp_config_terminates_itself(self, app_specs):
        # `--mcp-config` is `<configs...>`: it eats arguments until it meets a
        # flag. Measured 2026-08-10 — `--mcp-config '{...}' hi` sent the CLI
        # looking for a config file called `hi`, exit 1. Whatever follows this
        # group, the group must already have closed.
        flags = llm_router.strict_mcp_flags({"plaid": SPECS["plaid"]})
        assert flags[-1].startswith("--")
        assert flags.index("--mcp-config") + 1 < len(flags) - 1

    def test_tools_off_ignores_the_selection_entirely(self, app_specs):
        # There is nothing to narrow: this mode already spawns no servers.
        a, _e, _b = llm_router.tool_flags(tools_disabled=True)
        b, _e2, _b2 = llm_router.tool_flags(tools_disabled=True, mcp_selection=["plaid"])
        assert a == b
        assert mcp_json(a) == {}


# ---- the argv, full-agentic path (build / project / agent chats) -----------


class TestAgenticPathArgv:
    def test_unchosen_agentic_turn_sends_no_mcp_flags(self):
        flags, env, block = llm_router.tool_flags(permission_mode="auto")
        assert flags == [] and env == {} and block is False
        assert llm_router.agentic_mcp_flags(None, "/tmp") == []

    def test_a_subset_switches_the_turn_into_strict_mode(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mcp_registry, "CLAUDE_JSON", tmp_path / "claude.json")
        monkeypatch.setattr(mcp_registry, "INSTALLED_PLUGINS", tmp_path / "none.json")
        (tmp_path / "claude.json").write_text(json.dumps({"mcpServers": {
            "plaid": {"type": "stdio", "command": "node", "args": ["/srv/plaid.js"]},
            "playwright": {"type": "stdio", "command": "npx", "args": ["@playwright/mcp"]},
        }}))
        flags, env, block = llm_router.tool_flags(
            permission_mode="auto", mcp_selection=["plaid"], cwd=str(tmp_path))
        assert flags[0] == "--mcp-config"
        assert mcp_json(flags) == {
            "plaid": {"type": "stdio", "command": "node", "args": ["/srv/plaid.js"]}}
        # A server IS being spawned, so the CLI must block on its handshake.
        assert env == {"MCP_CONNECTION_NONBLOCKING": "false"} and block is True

    def test_a_selected_server_with_no_spec_is_dropped_not_invented(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(mcp_registry, "CLAUDE_JSON", tmp_path / "claude.json")
        monkeypatch.setattr(mcp_registry, "INSTALLED_PLUGINS", tmp_path / "none.json")
        (tmp_path / "claude.json").write_text(json.dumps({"mcpServers": {}}))
        flags = llm_router.agentic_mcp_flags(["ghost"], str(tmp_path))
        assert mcp_json(flags) == {}


# ---- spec resolution -------------------------------------------------------


class TestSpecResolution:
    def test_every_source_is_read(self, tmp_path, monkeypatch):
        home = tmp_path / "claude.json"
        proj = tmp_path / "proj"
        proj.mkdir()
        home.write_text(json.dumps({
            "mcpServers": {"u": {"command": "u"}},
            "projects": {str(proj): {"mcpServers": {"l": {"command": "l"}}}},
        }))
        (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"p": {"command": "p"}}}))
        monkeypatch.setattr(mcp_registry, "CLAUDE_JSON", home)
        monkeypatch.setattr(mcp_registry, "INSTALLED_PLUGINS", tmp_path / "none.json")
        got = mcp_registry.resolve_specs(str(proj))
        assert {k: v["scope"] for k, v in got.items()} == {
            "u": "user", "l": "local", "p": "project"}

    def test_a_plugin_server_is_named_the_way_the_cli_names_it(self, tmp_path, monkeypatch):
        root = tmp_path / "cache" / "discord" / "0.0.4"
        root.mkdir(parents=True)
        (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"discord": {
            "command": "bun",
            "args": ["run", "--cwd", "${CLAUDE_PLUGIN_ROOT}", "start"]}}}))
        installed = tmp_path / "installed.json"
        installed.write_text(json.dumps({"plugins": {
            "discord@clawd-a-plugins": [{"installPath": str(root)}]}}))
        monkeypatch.setattr(mcp_registry, "CLAUDE_JSON", tmp_path / "absent.json")
        monkeypatch.setattr(mcp_registry, "INSTALLED_PLUGINS", installed)
        got = mcp_registry.resolve_specs(str(tmp_path))
        # The name and the expansion both have to match `claude mcp list`, or
        # the health row and the spec never join.
        assert "plugin:discord:discord" in got
        assert got["plugin:discord:discord"]["spec"]["args"] == [
            "run", "--cwd", str(root), "start"]

    def test_a_malformed_config_contributes_nothing_and_does_not_raise(
            self, tmp_path, monkeypatch):
        bad = tmp_path / "claude.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mcp_registry, "CLAUDE_JSON", bad)
        monkeypatch.setattr(mcp_registry, "INSTALLED_PLUGINS", tmp_path / "none.json")
        assert mcp_registry.resolve_specs(str(tmp_path)) == {}

    def test_annotate_marks_a_server_it_cannot_spec(self):
        rows = mcp_registry.annotate(
            [{"name": "plaid"}, {"name": "claude.ai Gmail"}],
            {"plaid": {"spec": {}, "scope": "user", "source": "/x"}})
        assert [r["spec_available"] for r in rows] == [True, False]
        assert rows[0]["scope"] == "user" and rows[1]["scope"] == ""

    def test_annotate_never_leaks_the_spec(self):
        # A spec can hold credentials (~/.claude.json has a client secret on
        # this host). It exists to be handed to the CLI, never to a browser.
        rows = mcp_registry.annotate(
            [{"name": "g"}],
            {"g": {"spec": {"env": {"SECRET": "hunter2"}}, "scope": "user", "source": "/x"}})
        assert "spec" not in rows[0]
        assert "hunter2" not in json.dumps(rows[0])


# ---- persistence round trip ------------------------------------------------


class TestPersistence:
    def test_normalize_collapses_a_full_selection_to_all(self):
        assert chat_store.normalize_mcp_selection(["a", "b"], known=["a", "b"]) is None

    def test_normalize_keeps_a_real_subset(self):
        assert chat_store.normalize_mcp_selection(["a"], known=["a", "b"]) == ["a"]

    def test_normalize_keeps_an_empty_selection(self):
        # "no MCP servers at all" is a reachable choice and is NOT the default.
        assert chat_store.normalize_mcp_selection([], known=["a"]) == []

    def test_normalize_dedupes_and_keeps_order(self):
        assert chat_store.normalize_mcp_selection(["b", "a", "b"]) == ["b", "a"]

    def test_normalize_rejects_a_non_list(self):
        with pytest.raises(ValueError):
            chat_store.normalize_mcp_selection("plaid")

    def test_round_trip_through_the_store(self, owned):
        from services import chat_log
        t = chat_store.create_thread("general", title="t")
        tid = t["id"]

        # Never chosen: all servers, and the field is not even present.
        assert chat_store.get_thread_mcp_servers(tid) is None
        assert t.get("mcp_servers_enabled") is None

        chat_store.set_thread_mcp_servers(tid, ["plaid"], known=["plaid", "playwright"])
        assert chat_store.get_thread_mcp_servers(tid) == ["plaid"]
        assert chat_store.get_thread(tid)["mcp_servers_enabled"] == ["plaid"]

        chat_store.set_thread_mcp_servers(tid, [], known=["plaid", "playwright"])
        assert chat_store.get_thread_mcp_servers(tid) == []

        # Back to all: the field is REMOVED, so "all" has exactly one shape and
        # a chat can never look configured while behaving like the default.
        chat_store.set_thread_mcp_servers(tid, None)
        assert chat_store.get_thread_mcp_servers(tid) is None
        assert "mcp_servers_enabled" not in (chat_log.read_meta(tid) or {})

        # Ticking every box is the same thing as "all".
        chat_store.set_thread_mcp_servers(tid, ["plaid", "playwright"],
                                          known=["plaid", "playwright"])
        assert chat_store.get_thread_mcp_servers(tid) is None
