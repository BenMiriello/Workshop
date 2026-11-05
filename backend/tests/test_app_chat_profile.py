"""The prompt an app's own assistant is given is the narrow app profile.

What is pinned: an app-scoped chat is told the tools it has and nothing about
files, a shell, egress, tickets, delegation, skills, ongoing build work or the
builder's no-cap stop rule, and it reads only its own note layer. Every other
chat keeps each of those blocks exactly as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services import app_chat, chat_context, standing


@pytest.fixture()
def app_provider(tmp_path):
    decl = app_chat.ChatDeclaration(
        app_slug="some-app", project="some-app", root=Path(tmp_path),
        label="Some App", persona_file=None, mcp_service=None,
        mcp_path="/mcp", model=None)
    return chat_context.AppChatProvider(decl)


SHELL_WORDS = ("read and edit files", "run commands", "NETWORK", "git clone")
DELEGATION_WORDS = ("=== SUBAGENTS", "WHERE WORK GOES", "Agent tool")


class TestTheCapabilities:
    def test_an_app_chat_is_told_only_the_tools_it_has(self):
        out = chat_context.role_preamble(
            tools_active=True, tool_summary="tools: mcp__some_app__*, WebSearch",
            app_scoped=True)
        assert "You have tools: mcp__some_app__*, WebSearch." in out
        for words in (*SHELL_WORDS, *DELEGATION_WORDS):
            assert words not in out, words

    @pytest.mark.parametrize("is_build", [True, False])
    def test_every_other_chat_keeps_its_capabilities(self, is_build):
        out = chat_context.role_preamble(tools_active=True, is_build=is_build)
        assert "read and edit files" in out
        for words in DELEGATION_WORDS:
            assert words in out, words

    def test_the_contract_carries_the_app_profile(self, app_provider):
        out = chat_context.instruction_contract(app_provider, enable_chat_tools=True)
        for words in (*SHELL_WORDS, *DELEGATION_WORDS):
            assert words not in out, words


class TestTheCards:
    GENERIC = ("<card-kind>", "taskscope", "createProject")

    def test_an_app_chat_is_told_only_the_question_card(self):
        out = chat_context.role_preamble(tools_active=True, app_scoped=True)
        assert chat_context.APP_UI_CARD_PROTOCOL in out
        assert "`prompt` card" in out and "submitPrompt" in out
        for words in self.GENERIC:
            assert words not in out, words

    @pytest.mark.parametrize("tools_active", [True, False])
    def test_every_other_chat_keeps_the_whole_protocol(self, tools_active):
        out = chat_context.role_preamble(tools_active=tools_active)
        assert chat_context.UI_CARD_PROTOCOL in out
        assert chat_context.UI_CARD_PROTOCOL.endswith(chat_context._PROMPT_CARD_RULE)


class TestNoTickets:
    def test_the_app_contract_names_no_ticket(self, app_provider):
        out = chat_context.instruction_contract(app_provider, enable_chat_tools=True)
        assert "ticket" not in out.lower()
        assert "YOU CANNOT REACH THE OPERATOR" in out

    def test_the_app_map_names_no_ticket(self, app_provider):
        out = chat_context.orientation_manifest(app_provider)
        assert out.startswith("=== WHERE THINGS ARE")
        assert "ticket" not in out.lower()
        assert "workshop:note/<scope>/<id>" in out

    def test_the_app_working_state_names_no_ticket(self, app_provider, monkeypatch):
        from services import knowledge
        monkeypatch.setattr(knowledge, "read_working_state",
                            lambda scope, thread="": ("## Objective\n- a render", True))
        out = chat_context.working_state_block(app_provider, thread_id="t1")
        assert "- a render" in out and "ticket" not in out.lower()

    def test_every_other_chat_keeps_its_ticket_wording(self, monkeypatch):
        from services import knowledge
        general = chat_context.get_provider("general")
        monkeypatch.setattr(knowledge, "read_working_state",
                            lambda scope: ("## Objective\n- a render", True))
        assert "Work that must reach someone later leaves as a Ticket." in (
            chat_context.delegation_block(True))
        assert "filing a TICKET" in chat_context.orientation_manifest(general)
        assert ("It does NOT own ticket or job status; the ongoing-work digest "
                "does. It owns") in chat_context.working_state_block(general)


class TestTheStopRule:
    def test_an_app_chat_waits_rather_than_running_without_a_cap(self):
        out = chat_context.turn_contract(0, app_scoped=True)
        assert "There is NO cap on this turn" not in out
        assert "WAITING ON LONG WORK" in out

    def test_every_other_chat_keeps_the_no_cap_rule(self):
        out = chat_context.turn_contract(0)
        assert "There is NO cap on this turn" in out
        assert "WAITING ON LONG WORK" not in out


class TestWhatItReads:
    def test_no_ongoing_build_work(self, app_provider):
        assert chat_context.ambient_digest(app_provider) == ""

    def test_its_own_note_layer_only(self, app_provider):
        assert chat_context.knowledge_layers(app_provider) == ["app:some-app"]
        assert chat_context.knowledge_layers(chat_context.get_provider("general")) is None

    def test_standing_notes_come_from_the_layers_named(self, monkeypatch):
        notes = {"workshop": "finish by merging into main", "app:some-app": "an app rule"}
        monkeypatch.setattr(standing, "_standing_notes", lambda layer: [
            {"id": layer, "text": notes[layer], "hash": layer, "seen": 1}]
            if layer in notes else [])
        out = standing.block("app:some-app", layers=["app:some-app"])
        assert "an app rule" in out and "merging" not in out


class TestTheAssembledPrompt:
    @pytest.fixture()
    def build_prompt(self, monkeypatch):
        from routes import chat as rc
        from services import skills
        monkeypatch.setattr(skills, "descriptions_block", lambda scope: "- a-skill: ship it")
        return lambda provider: rc._build_prompt(provider, [], "hi")

    def test_an_app_chat_gets_no_skills(self, build_prompt, app_provider):
        assert "=== SKILLS" not in build_prompt(app_provider)

    def test_the_workshop_chat_still_does(self, build_prompt):
        assert "=== SKILLS" in build_prompt(chat_context.get_provider("general"))


class TestTheResumedTurn:
    @pytest.fixture()
    def resumed(self, monkeypatch):
        import asyncio

        from routes import chat as rc
        t = {"id": "th1", "messages": [], "mode": "auto"}
        monkeypatch.setattr(rc.chat_store, "get_turn_marks",
                            lambda thread: {"contract_hash": "0000stale0000"})
        monkeypatch.setattr(rc.chat_store, "set_turn_marks", lambda tid, **kw: None)
        monkeypatch.setattr(rc.chat_store, "get_thread", lambda tid: t)
        monkeypatch.setattr(rc.chat_store, "previous_turn_id", lambda tid: None)
        monkeypatch.setattr(rc.task_scope, "key_for_root", lambda root: None)
        return lambda provider: asyncio.run(rc._resumed_prompt(t, provider, "hello"))

    def test_an_app_chat_gets_no_mode_block_and_no_contract_resend(
            self, resumed, app_provider):
        out = resumed(app_provider)
        assert "=== CHAT MODE" not in out
        assert "=== PROTOCOL UPDATED ===" not in out
        assert "WAITING ON LONG WORK" in out

    def test_the_workshop_chat_still_gets_both(self, resumed):
        out = resumed(chat_context.get_provider("general"))
        assert "=== CHAT MODE: AUTO ===" in out
        assert "=== PROTOCOL UPDATED ===" in out
