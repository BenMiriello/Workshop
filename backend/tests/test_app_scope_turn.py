"""An app's own assistant inherits none of the account's coding context.

What is pinned: an app-scoped turn runs with CLAUDE.md loading, auto-memory and
every settings source switched off, and without the subagent tool, and the
prompt does not tell it about a tool it lacks. Its system prompt is the
platform's own rather than the CLI's, and a compaction hands it back its own
tool calls. A build turn and a workshop chat are unchanged on every one of
those counts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import (app_chat, app_scope_turn, build_streaming, chat_context,
                      turn_progress)


def meta(**kw) -> dict:
    base = {"turn_id": "t1", "model": "claude-opus-5", "session_id": None,
            "resume": False, "cwd": "/tmp", "permission_mode": "bypassPermissions"}
    base.update(kw)
    return base


NARROW = {"allowed_tools": ["WebSearch", "mcp__some_app__*"], "mcp_servers": []}


@pytest.fixture()
def app_provider(tmp_path):
    decl = app_chat.ChatDeclaration(
        app_slug="some-app", project="some-app", root=Path(tmp_path),
        label="Some App", persona_file=None, mcp_service=None,
        mcp_path="/mcp", model=None)
    return chat_context.AppChatProvider(decl)


def _setting_sources(cmd: list[str]) -> str | None:
    i = cmd.index("--setting-sources") if "--setting-sources" in cmd else -1
    return cmd[i + 1] if i >= 0 else None


class TestTheTurn:
    def test_an_app_turn_loads_no_settings_source(self):
        cmd, _ = build_streaming._build_cmd(meta(app_scoped=True, **NARROW))
        assert _setting_sources(cmd) == ""

    def test_the_narrow_tool_surface_survives_the_flag(self):
        cmd, _ = build_streaming._build_cmd(meta(app_scoped=True, **NARROW))
        assert "--strict-mcp-config" in cmd
        assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"
        assert "mcp__some_app__*" in cmd

    @pytest.mark.parametrize("extra", [NARROW, {}])
    def test_any_other_turn_keeps_its_settings(self, extra):
        cmd, _ = build_streaming._build_cmd(meta(**extra))
        assert "--setting-sources" not in cmd

    def test_an_app_turn_loads_no_claude_md_and_no_memory(self):
        env = build_streaming._turn_env(meta(app_scoped=True), {})
        assert env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
        assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"

    def test_a_build_turn_keeps_both(self, monkeypatch):
        for k in app_scope_turn.ISOLATION_ENV:
            monkeypatch.delenv(k, raising=False)
        env = build_streaming._turn_env(meta(), {})
        assert not set(app_scope_turn.ISOLATION_ENV) & set(env)

    def test_the_isolation_rides_in_meta_so_a_respawn_keeps_it(self):
        assert "app_scoped" in build_streaming.start_turn.__code__.co_varnames


class TestTheSystemPrompt:
    def test_an_app_turn_replaces_the_cli_system_prompt(self):
        cmd, _ = build_streaming._build_cmd(
            meta(app_scoped=True, system_prompt="THE CONTRACT", **NARROW))
        assert cmd[cmd.index("--system-prompt") + 1] == "THE CONTRACT"
        assert "--append-system-prompt" not in cmd

    def test_without_text_the_cli_keeps_its_own(self):
        cmd, _ = build_streaming._build_cmd(meta(app_scoped=True, **NARROW))
        assert "--system-prompt" not in cmd

    @pytest.mark.parametrize("extra", [NARROW, {}])
    def test_any_other_turn_never_replaces_it(self, extra):
        cmd, _ = build_streaming._build_cmd(meta(system_prompt="X", **extra))
        assert "--system-prompt" not in cmd

    def test_it_is_the_kernel_and_the_contract(self, app_provider):
        out = chat_context.app_system_prompt(app_provider, enable_chat_tools=True)
        contract = chat_context.instruction_contract(app_provider, enable_chat_tools=True)
        assert out.startswith(chat_context.platform_kernel())
        assert out.endswith(contract)

    def test_the_first_message_then_leaves_both_out(self, app_provider):
        from routes import chat as rc
        persona = app_provider.persona()
        moved = rc._build_prompt(app_provider, [], "hi", preamble="PREAMBLE",
                                 contract_in_system=True)
        kept = rc._build_prompt(app_provider, [], "hi", preamble="PREAMBLE")
        assert chat_context.platform_kernel() not in moved
        assert "PREAMBLE" not in moved and persona not in moved
        assert chat_context.platform_kernel() in kept
        assert "PREAMBLE" in kept and persona in kept


class TestTheTools:
    def test_an_app_chat_has_no_subagent_tool(self, app_provider):
        tools = chat_context.effective_allowed_tools(app_provider)
        assert not set(app_scope_turn.SUBAGENT_TOOLS) & set(tools)
        assert "WebSearch" in tools

    def test_the_workshop_chat_keeps_it(self):
        general = chat_context.get_provider("general")
        assert chat_context.SUBAGENT_TOOL in chat_context.effective_allowed_tools(general)
        assert not chat_context.is_app_scoped(general)

    def test_a_build_provider_is_never_app_scoped(self, app_provider, tmp_path):
        project = chat_context.ProjectContextProvider.__new__(
            chat_context.ProjectContextProvider)
        project.app_scoped = True
        assert chat_context.is_app_scoped(app_provider)
        assert not chat_context.is_app_scoped(project)


class TestThePrompt:
    def test_an_app_chat_is_not_told_it_can_delegate(self, app_provider):
        out = chat_context.instruction_contract(app_provider, enable_chat_tools=True)
        assert "Agent tool" not in out
        assert "=== SUBAGENTS" not in out

    def test_a_tools_chat_without_subagents_keeps_its_other_rules(self):
        out = chat_context.delegation_block(True, subagents=False)
        assert out.startswith("NOTHING WAKES YOU LATER")
        assert "SUBAGENT" not in out

    def test_every_other_chat_still_is(self):
        out = chat_context.role_preamble(tools_active=True, is_build=True)
        assert "=== SUBAGENTS" in out and "Agent tool" in out


class TestAfterCompaction:
    HISTORY = [{"role": "user", "content": "old " * 4000},
               {"role": "assistant", "content": "an earlier answer"},
               {"role": "user", "content": "render the second panel"}]

    def test_the_tail_keeps_the_newest_messages_verbatim(self):
        out = chat_context.compaction_tail_block(self.HISTORY, max_chars=200)
        assert out.startswith("=== CONVERSATION SO FAR (restored after compaction)")
        assert "render the second panel" in out
        assert "an earlier answer" in out
        assert out.index("an earlier answer") < out.index("render the second panel")
        assert len(out) < 600

    def test_nothing_to_restore_is_nothing(self):
        assert chat_context.compaction_tail_block([]) == ""

    def _turn(self, monkeypatch, *, accepts=True):
        turn = build_streaming.Turn(id="t1", thread_id="th1", agent_slug="a")
        turn.accepts_input = accepts
        monkeypatch.setattr(build_streaming.chat_store, "get_thread",
                            lambda tid: {"messages": self.HISTORY})
        written = []
        monkeypatch.setattr(build_streaming, "write_to_turn",
                            lambda tid, text: written.append(text) or True)
        return turn, written

    def test_an_app_turn_is_handed_its_transcript(self, monkeypatch):
        turn, written = self._turn(monkeypatch)
        assert build_streaming._restore_after_compaction(turn, {"thread_id": "th1"})
        assert "render the second panel" in written[0]

    def test_a_command_turn_reads_nothing_more(self, monkeypatch):
        turn, written = self._turn(monkeypatch, accepts=False)
        assert not build_streaming._restore_after_compaction(turn, {"thread_id": "th1"})
        assert written == []


def _use(tid, name, inp, parent=None):
    return json.dumps({"type": "assistant", "parent_tool_use_id": parent, "message": {
        "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})


def _res(tid, content, error=False):
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": content,
         "is_error": error}]}})


JOURNAL = [
    json.dumps({"type": "stream_event", "event": {"type": "content_block_start",
                "content_block": {"type": "tool_use"}}}),
    _use("a", "mcp__some_app__list_things", {}),
    _res("a", [{"type": "text", "text": json.dumps(
        {"total": 2, "items": [{"id": 41, "title": "First"}, {"id": 42}]})}]),
    _use("b", "mcp__some_app__get_thing", {"thing_id": 42}),
    _res("b", "thing 42 not found", error=True),
    _use("s", "inner_tool", {}, parent="b"),
    _use("c", "mcp__some_app__get_thing", {"thing_id": 41}),
]


class TestTheTurnsOwnProgress:
    def test_its_calls_are_read_from_the_journal(self):
        calls = turn_progress.tool_calls(JOURNAL)
        assert [c["name"] for c in calls] == [
            "mcp__some_app__list_things", "mcp__some_app__get_thing",
            "mcp__some_app__get_thing"]
        assert calls[1]["error"] and calls[2]["result"] is None

    def test_newest_first_with_arguments_ids_and_errors(self, tmp_path):
        j = tmp_path / "stream.jsonl"
        j.write_text("\n".join(JOURNAL))
        out = turn_progress.progress_block(j)
        lines = out.splitlines()[2:]
        assert lines[0] == "- mcp__some_app__get_thing(thing_id=41) -> no result"
        assert lines[1].endswith("-> ERROR thing 42 not found")
        assert lines[2] == ('- mcp__some_app__list_things() -> total=2, '
                            'items=[2: 41 "First", 42]')

    def test_repeats_fold_and_the_cap_holds(self, tmp_path):
        j = tmp_path / "stream.jsonl"
        rows = []
        for i in range(200):
            rows += [_use(f"x{i}", "mcp__some_app__get_thing", {"thing_id": i}),
                     _res(f"x{i}", "ok")]
        rows += [_use(f"r{i}", "mcp__some_app__list_things", {}) for i in range(3)]
        j.write_text("\n".join(rows))
        out = turn_progress.progress_block(j, max_chars=1000)
        assert len(out) <= 1100
        assert "(x3, the same call repeated)" in out
        assert out.rstrip().endswith("earlier call(s) not shown")

    def test_no_journal_is_nothing(self, tmp_path):
        assert turn_progress.progress_block(tmp_path / "missing.jsonl") == ""

    def test_a_compacted_app_turn_is_handed_its_progress(self, monkeypatch, tmp_path):
        (tmp_path / "stream.jsonl").write_text("\n".join(JOURNAL))
        monkeypatch.setattr(build_streaming, "_journal_dir", lambda tid: tmp_path)
        monkeypatch.setattr(build_streaming.chat_store, "get_thread",
                            lambda tid: {"messages": [
                                {"role": "user", "content": "find thing 41"}]})
        written = []
        monkeypatch.setattr(build_streaming, "write_to_turn",
                            lambda tid, text: written.append(text) or True)
        turn = build_streaming.Turn(id="t1", thread_id="th1", agent_slug="a")
        assert build_streaming._restore_after_compaction(turn, {"thread_id": "th1"})
        assert written[0].index("find thing 41") < written[0].index(
            "=== THIS TURN'S PROGRESS")
        assert "get_thing(thing_id=41)" in written[0]
