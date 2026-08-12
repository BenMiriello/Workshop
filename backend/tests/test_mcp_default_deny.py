"""A non-operator chat must not inherit the operator's MCP credentials.

Every MCP server on an instance (Plaid, Google, Discord) is configured with the
OPERATOR's own host-level credentials — there is no per-user credential
provisioning. `routes.chat._start_assistant_turn` reads a thread's own
`mcp_selection` as `None` ("never chosen") meaning "every server this provider
can reach" on BOTH the narrow path (a data/app-persona chat's declared
`provider.mcp_servers`) and the full-agentic path (project/build/development).
That is correct for the operator's own chats and was always exercised against
them. `ownership.caller_is_operator` is the gate that keeps it that way for
anyone else: see docs/security-audit-2026-08-11-scope-separation.md section 4.

**These tests assert the argv, not the predicate.** The first version of this
file tested only that `caller_is_operator()` returns False for a second account
— which it did, while the value it computed was dropped on the floor two
functions later and never reached the spawn. On the full-agentic path
`_build_cmd` took an `else` branch that emitted a bare `--permission-mode` and
no `--strict-mcp-config` at all, so the CLI inherited every server in
`~/.claude.json` regardless. A test that stops at the decision and never checks
the command cannot see that, so this one goes all the way to argv.
"""
from __future__ import annotations

import json

import pytest

from services import auth_store, build_streaming, mcp_registry, model_caps, ownership


@pytest.fixture
def two_users(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    auth_store.init_db()
    op = auth_store.create_user("operator@example.com", "password1")
    member = auth_store.create_user("member@example.com", "password2")
    auth_store.set_operator(op["id"])
    try:
        yield {"op": op, "member": member}
    finally:
        ownership.clear_current_owner()


@pytest.fixture
def host_servers(tmp_path, monkeypatch):
    """A host `~/.claude.json` carrying the operator's real servers — what an
    unrestricted agentic turn would inherit."""
    monkeypatch.setattr(mcp_registry, "CLAUDE_JSON", tmp_path / "claude.json")
    monkeypatch.setattr(mcp_registry, "INSTALLED_PLUGINS", tmp_path / "none.json")
    (tmp_path / "claude.json").write_text(json.dumps({"mcpServers": {
        "plaid": {"type": "stdio", "command": "node", "args": ["/srv/plaid.js"]},
        "google-docs": {"type": "stdio", "command": "node", "args": ["/srv/gdocs.js"]},
    }}))
    return tmp_path


def _agentic_meta(cwd: str, **over) -> dict:
    """Meta for a project/build turn: the full-agentic mode, allowed_tools None."""
    meta = {
        "turn_id": "t1", "thread_id": "th1", "agent_slug": "proj", "cwd": cwd,
        "model": "sonnet", "session_id": None, "resume": False, "prompt": "hi",
        "allowed_tools": None, "disallowed_tools": None, "mcp_servers": None,
        "tools_disabled": False, "effort": None, "permission_mode": None,
        "attachments": [],
    }
    meta.update(over)
    return meta


def flag_value(cmd: list[str], flag: str) -> str | None:
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


def mcp_json(cmd: list[str]) -> dict:
    raw = flag_value(cmd, "--mcp-config")
    assert raw is not None, f"no --mcp-config in {cmd}"
    return json.loads(raw)["mcpServers"]


class TestCallerIsOperator:
    def test_the_operator_is_the_operator(self, two_users):
        ownership.set_current_owner(two_users["op"]["id"])
        assert ownership.caller_is_operator() is True

    def test_a_second_real_account_is_not(self, two_users):
        ownership.set_current_owner(two_users["member"]["id"])
        assert ownership.caller_is_operator() is False

    def test_no_owner_pinned_fails_closed_not_open(self, two_users):
        # OwnerUnset is a programming error on a personal-data path, but here it
        # must read as "not the operator", never crash the turn or default to
        # trusting it.
        ownership.clear_current_owner()
        assert ownership.caller_is_operator() is False

    def test_no_operator_bootstrapped_yet_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth2.db")
        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime2")
        auth_store.init_db()
        someone = auth_store.create_user("first@example.com", "password1")
        ownership.set_current_owner(someone["id"])
        try:
            # No set_operator() call: operator_id() is None. A pre-bootstrap
            # instance must not treat the first authenticated caller as trusted.
            assert ownership.caller_is_operator() is False
        finally:
            ownership.clear_current_owner()


class TestTheAgenticArgvActuallyDenies:
    """The spawn itself, on the path that carries Bash/Edit/Write."""

    def test_an_empty_selection_pins_an_empty_strict_config(self, host_servers):
        cmd, _env = build_streaming._build_cmd(
            _agentic_meta(str(host_servers), mcp_selection=[]))
        assert "--strict-mcp-config" in cmd, (
            "without --strict-mcp-config the CLI reads ~/.claude.json and the "
            "empty config below is advisory at best")
        assert mcp_json(cmd) == {}

    def test_denial_does_not_cost_the_turn_its_permission_mode(self, host_servers):
        # tool_flags' agentic branch returns the strict flags ONLY — the caller
        # owns the mode there. Drop it and a headless turn stalls asking for
        # approval on a channel that does not exist.
        cmd, _env = build_streaming._build_cmd(
            _agentic_meta(str(host_servers), mcp_selection=[]))
        assert cmd.count("--permission-mode") == 1
        assert flag_value(cmd, "--permission-mode") == model_caps.DEFAULT_PERMISSION_MODE

    def test_a_chosen_subset_reaches_the_agentic_argv(self, host_servers):
        # The same wiring the operator's own per-thread MCP picker rides on: it
        # was honored on the narrow path and silently dropped on this one.
        cmd, _env = build_streaming._build_cmd(
            _agentic_meta(str(host_servers), mcp_selection=["plaid"]))
        assert set(mcp_json(cmd)) == {"plaid"}
        assert cmd.count("--permission-mode") == 1

    def test_never_chosen_is_byte_identical_to_before_the_gate(self, host_servers):
        # The operator's own chats are the overwhelming majority and must not
        # move: no selection means no MCP flags, exactly as before.
        cmd, _env = build_streaming._build_cmd(_agentic_meta(str(host_servers)))
        assert "--strict-mcp-config" not in cmd and "--mcp-config" not in cmd
        assert cmd.count("--permission-mode") == 1

    def test_an_old_journal_with_no_key_reads_as_never_chosen(self, host_servers):
        # Restart-recovery replays meta written before `mcp_selection` existed.
        meta = _agentic_meta(str(host_servers))
        meta.pop("mcp_selection", None)
        cmd, _env = build_streaming._build_cmd(meta)
        assert "--strict-mcp-config" not in cmd
