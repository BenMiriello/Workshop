"""Direct, Orchestrated, Auto: whether work leaves the conversation, and
whether it waits to be let out.

The first two already existed as a field with no behaviour, like `participants`.
These tests pin the behaviour they were given, and in particular that the guard
is REAL: the prompt asks the model not to file in Direct mode, but a prompt is a
request. A chat in Direct mode must be unable to file even if the model emits a
block anyway.
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import chat_context, task_scope  # noqa: E402


class TestModeStatement:
    def test_direct_forbids_filing(self):
        out = chat_context.chat_mode_block("direct")
        assert "DIRECT" in out
        assert "Do NOT file tickets" in out

    def test_direct_overrides_protocols_already_in_the_session(self):
        """The protocols are large and sent once, on the first turn. A switch to
        Direct later must override them, or switching mid-chat does nothing."""
        out = chat_context.chat_mode_block("direct")
        assert "do not apply while the chat is in Direct mode" in out

    def test_direct_points_at_the_alternative(self):
        """Removing a capability without naming the replacement just reads as a
        broken assistant."""
        assert "native subagent" in chat_context.chat_mode_block("direct")

    def test_orchestrated_names_the_ticket_as_the_record(self):
        out = chat_context.chat_mode_block("orchestrated")
        assert "ORCHESTRATED" in out
        assert "Ticket is the record" in out

    def test_unset_defaults_to_direct(self):
        """DIRECT IS THE DEFAULT (2026-08-05, operator's call). Orchestrated was,
        which made every passing remark a candidate for a ticket. Work that
        outlives the chat is the exception and should be the thing you ask for.

        A chat with no stored mode follows the default rather than being pinned
        to whatever it was created under — otherwise changing the default would
        only reach chats that do not exist yet."""
        assert "DIRECT" in chat_context.chat_mode_block(None)

    def test_junk_defaults_to_direct(self):
        assert "DIRECT" in chat_context.chat_mode_block("nonsense")

    def test_the_default_is_defined_once(self):
        """It used to be declared in both chat_store and chat_context, which is
        how a default ends up disagreeing with itself."""
        from services import chat_store
        assert chat_context.DEFAULT_CHAT_MODE is chat_store.DEFAULT_CHAT_MODE
        assert chat_store._clamp_mode(None) == "direct"
        assert chat_store._clamp_mode("orchestrated") == "orchestrated"


class TestAutoMode:
    """The third policy: file and run without asking, stop only for a question
    that cannot be answered. Without it the Work settings control is a two-way
    switch drawn as a three-way one."""

    def test_it_is_a_mode(self):
        from services import chat_store
        assert chat_store.CHAT_MODES == ("direct", "orchestrated", "auto")
        assert chat_store._clamp_mode("auto") == "auto"

    def test_it_does_not_become_the_default(self):
        """Direct stays the default. A mode that files and runs on its own is
        never something a chat acquires by not choosing."""
        from services import chat_store
        assert chat_store.DEFAULT_CHAT_MODE == "direct"
        assert "AUTO" not in chat_context.chat_mode_block(None)
        assert "AUTO" not in chat_context.chat_mode_block("nonsense")

    def test_the_mode_line_says_not_to_ask_first(self):
        out = chat_context.chat_mode_block("auto")
        assert "AUTO" in out
        assert "WITHOUT asking first" in out

    def test_it_keeps_the_ticket_as_the_record(self):
        """Auto is orchestrated minus the approval step, not a third place for
        work to live."""
        assert "Ticket is the record" in chat_context.chat_mode_block("auto")

    def test_the_question_goes_through_the_ticket(self):
        """A worker sees only the ticket text, so an answer that lands anywhere
        else never reaches the next attempt."""
        assert "through the ticket" in chat_context.chat_mode_block("auto")

    def test_the_db_constraint_knows_it(self):
        """The check constraint is not derived at runtime, so a value the store
        accepts and the schema refuses is a write that fails on the mirror."""
        from db import models
        assert "auto" in models.CHAT_MODES

    def test_an_auto_chat_may_file(self, tmp_path, monkeypatch):
        """The filing guard tests for `direct` and nothing else, which is what
        makes auto file like orchestrated does. Pinned because a guard written
        as an allowlist later would silently exclude it.

        Pointed at a temp store for the reason `test_unset_mode_still_files`
        is: filing into the real workshop queue is a production side effect."""
        monkeypatch.setattr(task_scope, "WORKSHOP_SCOPE_DIR", tmp_path)
        patch = 'Sure.\n\n```task-patch\n{"add":[{"text":"Filed by auto"}]}\n```\n'
        _, notes = task_scope.apply_task_patch("general", patch, "t1", "auto")
        assert not any("Direct mode" in n for n in notes)
        from services import tasks
        assert any("Filed by auto" in (t.get("text") or "")
                   for t in tasks._load(tmp_path))


class TestModeRoundTrip:
    """The stored value has to survive the route, the store and the prompt
    builder. A mode the enum accepts but the route rejects is a control that
    silently does nothing."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from routes import chat as chat_routes
        from services import ownership
        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
        ownership.set_current_owner(1)
        app = FastAPI()
        app.include_router(chat_routes.router, prefix="/chat")
        try:
            yield TestClient(app)
        finally:
            ownership.clear_current_owner()

    def test_the_route_accepts_auto(self, client):
        from services import chat_store
        t = chat_store.create_thread(context_key="general")
        r = client.post(f"/chat/threads/{t['id']}/mode", json={"mode": "auto"})
        assert r.status_code == 200
        assert r.json()["mode"] == "auto"
        assert chat_store.get_thread(t["id"])["mode"] == "auto"

    def test_the_stored_mode_reaches_the_prompt(self, client):
        """The mode line is re-stated every turn, which is what makes a
        mid-chat switch take effect on the next one."""
        from services import chat_store
        t = chat_store.create_thread(context_key="general", mode="orchestrated")
        assert "ORCHESTRATED" in chat_context.chat_mode_block(
            chat_store.get_thread(t["id"])["mode"])
        client.post(f"/chat/threads/{t['id']}/mode", json={"mode": "auto"})
        assert "AUTO" in chat_context.chat_mode_block(
            chat_store.get_thread(t["id"])["mode"])

    def test_junk_is_still_refused(self, client):
        from services import chat_store
        t = chat_store.create_thread(context_key="general")
        r = client.post(f"/chat/threads/{t['id']}/mode", json={"mode": "sideways"})
        assert r.status_code == 400
        assert chat_store.get_thread(t["id"])["mode"] == "direct"

    def test_a_new_chat_can_default_to_auto(self, client):
        """The operator's default for new chats is the same enum, so the
        settings screen can offer all three."""
        from services import chat_store
        assert client.post("/chat/prefs", json={"default_mode": "auto"}).status_code == 200
        assert chat_store.create_thread(context_key="general")["mode"] == "auto"


class TestSubagentsAreReal:
    """Direct mode tells the model to delegate to a native subagent instead of
    filing. For months a data chat had no tool that could do it: the allowlist
    was the declared MCP tools plus web, and the CLI reported no subagent tool at
    all when asked (probe, 2026-08-07). An instruction to use a capability that
    was not granted is just a lie the model then has to work around."""

    def test_the_subagent_tool_is_granted_to_a_data_chat(self):
        p = chat_context.ContextProvider()
        assert chat_context.SUBAGENT_TOOL in chat_context.effective_allowed_tools(p)

    def test_it_is_granted_centrally_not_per_provider(self):
        """A provider that declares nothing still gets it, for the same reason
        web is granted here: one place, so the route and capability_for can't
        disagree about what the session can do."""
        p = chat_context.ContextProvider()
        p.allowed_tools = []
        assert chat_context.effective_allowed_tools(p) == ["WebSearch", "WebFetch", "Agent"]

    def test_the_granted_name_matches_what_the_cli_emits(self):
        """`Agent` is the tool name in CLI 2.1.143 (`Task` is the older spelling,
        still accepted on --tools). Granting a name the CLI does not know would
        fail silently: the flag is accepted and the tool never appears."""
        from services import build_streaming
        assert chat_context.SUBAGENT_TOOL in build_streaming.SUBAGENT_TOOLS

    def test_the_rule_separates_a_subagent_from_a_ticket(self):
        """The operator's actual question: what needs a ticket and what does not.
        Isolation vs durability, not size."""
        rule = chat_context.DELEGATION_RULE
        assert "context isolation" in rule and "durability" in rule
        assert "Size is not the test" in rule

    def test_the_rule_ships_with_the_capabilities_the_model_is_given(self):
        out = chat_context.role_preamble(tools_active=True)
        assert "SUBAGENTS" in out
        assert "Agent tool" in out

    def test_a_tools_off_session_is_not_told_it_can_delegate(self):
        assert "SUBAGENTS" not in chat_context.role_preamble(tools_active=False)
        assert chat_context.delegation_block(False) == ""

    def test_installed_subagents_are_named_so_they_can_be_called(self):
        """`subagent_type` only works if the model knows the names. They come
        from the same registry the Agents page reads."""
        from services import agents_registry
        ours = [r for r in agents_registry.native_subagents() if not r.get("foreign")]
        block = chat_context.delegation_block(True)
        for r in ours:
            assert r["slug"] in block

    def test_another_surfaces_agent_is_not_offered(self):
        """discord-handler belongs to the chat bridge. Offering it as a
        delegate here would hand this chat's work to another tenant's agent."""
        block = chat_context.delegation_block(True)
        assert "discord-handler" not in block


class TestFilingGuard:
    PATCH = 'Sure.\n\n```task-patch\n{"add":[{"text":"Should not be filed"}]}\n```\n'

    def test_direct_mode_refuses_to_file(self, tmp_path):
        cleaned, notes = task_scope.apply_task_patch("general", self.PATCH, "t1", "direct")
        assert any("Direct mode" in n for n in notes)
        assert "task-patch" not in cleaned

    def test_the_block_is_stripped_even_when_refused(self):
        """A refused block must not be left in the visible reply as raw JSON."""
        cleaned, _ = task_scope.apply_task_patch("general", self.PATCH, "t1", "direct")
        assert "```" not in cleaned
        assert "Should not be filed" not in cleaned

    def test_no_block_is_a_no_op_in_direct_mode(self):
        reply = "Just talking, no ticket here."
        cleaned, notes = task_scope.apply_task_patch("general", reply, "t1", "direct")
        assert cleaned == reply
        assert notes == []

    def test_unset_mode_still_files(self, tmp_path, monkeypatch):
        """Backwards compatibility: a thread written before the mode existed
        must behave exactly as it did.

        Pointed at a temp store. The first version of this test filed into the
        REAL workshop queue every time it ran — a test with a production side
        effect, which is its own defect. The workshop scope is
        `WORKSHOP_SCOPE_DIR` since ADR 0014; WORKSHOP_ROOT is the platform repo
        and would resolve to `development` instead."""
        monkeypatch.setattr(task_scope, "WORKSHOP_SCOPE_DIR", tmp_path)
        cleaned, notes = task_scope.apply_task_patch("general", self.PATCH, "t1", None)
        assert not any("Direct mode" in n for n in notes)
        from services import tasks
        assert any("Should not be filed" in (t.get("text") or "")
                   for t in tasks._load(tmp_path))
