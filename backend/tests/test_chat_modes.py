"""Direct and Auto: whether work leaves the conversation.

The field already existed with no behaviour. The other field in that state,
`participants`, was deleted in T2 rather than given one; this one was given one.
Those are the two honest endings, and a field must reach one of them. These
tests pin the behaviour it was given, and in particular that the guard is REAL:
the prompt asks the model not to file in Direct mode, but a prompt is a request.
A chat in Direct mode must be unable to file even if the model emits a block
anyway.

THERE WERE THREE MODES UNTIL 2026-08-27. `orchestrated` ("file it, then wait for
approval") and `auto` ("file it and run it") differed only in the prose
`chat_mode_block` emitted, never in a code path, and the operator states no
intended distinction between them. `auto` absorbed `orchestrated`. The retired
value is still on disk, so the tests that used to pin its behaviour now pin
where it LANDS -- on `auto`, and specifically not on `direct`, because falling
through to the default would turn 44 live threads that file into threads that
do not.
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

    def test_auto_names_the_ticket_as_the_record(self):
        """"Ticket is the record" is the load-bearing sentence of the mode that
        files: it says WHERE the work lives once it leaves. It was written on
        the orchestrated branch and had to survive the collapse, or the
        surviving mode would tell the model to file without saying where the
        filing goes."""
        out = chat_context.chat_mode_block("auto")
        assert "AUTO" in out
        assert "Ticket is the record" in out

    def test_the_retired_mode_still_names_the_ticket_as_the_record(self):
        """A thread stored as `orchestrated` gets the auto block, not the direct
        one. Its prompt still has to say where work goes."""
        out = chat_context.chat_mode_block("orchestrated")
        assert "AUTO" in out
        assert "ORCHESTRATED" not in out
        assert "Ticket is the record" in out

    def test_unset_defaults_to_direct(self):
        """DIRECT IS THE DEFAULT (2026-08-05, operator's call). The mode that
        files was, which made every passing remark a candidate for a ticket.
        Work that outlives the chat is the exception and should be the thing you
        ask for.

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


class TestTheRetiredModeMapsForward:
    """`orchestrated` is off the enum and still on disk: 44 threads and one
    prefs file carried it when it was retired, and neither is rewritten. Every
    read goes through `_clamp_mode`, so this is the whole migration."""

    def test_it_resolves_to_auto(self):
        from services import chat_store
        assert chat_store._clamp_mode("orchestrated") == "auto"

    def test_it_specifically_does_not_resolve_to_direct(self):
        """THE FAILURE THIS EXISTS TO CATCH. A retired value that falls through
        to the default is not a rename, it is a behaviour change: 44 chats that
        file work would silently stop filing it. Spelled as its own assertion
        because `== "auto"` above passes for the wrong reason if someone later
        makes `auto` the default."""
        from services import chat_store
        assert chat_store._clamp_mode("orchestrated") != chat_store.DEFAULT_CHAT_MODE
        assert chat_store._clamp_mode("orchestrated") != "direct"

    def test_it_is_not_offered_again(self):
        """Mapped on read, never handed back out. A retired value in the offered
        list is a control that can put a thread back on it."""
        from services import chat_store
        assert "orchestrated" not in chat_store.CHAT_MODES

    def test_junk_still_falls_to_the_default(self):
        """The forward map is one named value, not a general "anything that
        files" guess."""
        from services import chat_store
        assert chat_store._clamp_mode("sideways") == "direct"

    def test_a_stored_thread_reads_as_auto(self, tmp_path, monkeypatch):
        """The file log is the record and it is NOT rewritten, so a meta.json
        written before the collapse has to read as `auto` through the store."""
        from services import chat_log, chat_store, ownership
        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
        ownership.set_current_owner(1)
        try:
            tid = chat_store.create_thread(context_key="general")["id"]
            chat_log.patch_meta_sync(tid, mode="orchestrated")
            assert chat_store.get_thread(tid)["mode"] == "auto"
            assert [t["mode"] for t in chat_store.list_threads()] == ["auto"]
        finally:
            ownership.clear_current_owner()

    def test_a_stored_default_reads_as_auto(self, tmp_path, monkeypatch):
        """Same for the operator's default for new chats, which lives in
        prefs.json beside the threads."""
        from services import chat_store, ownership
        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
        ownership.set_current_owner(1)
        try:
            chat_store._write_pref("default_mode", "orchestrated")
            assert chat_store.get_default_mode() == "auto"
            assert chat_store.create_thread("general")["mode"] == "auto"
        finally:
            ownership.clear_current_owner()


class TestAutoMode:
    """The one policy for work that leaves: file and run without asking, stop
    only for a question that cannot be answered."""

    def test_it_is_a_mode(self):
        from services import chat_store
        assert chat_store.CHAT_MODES == ("direct", "auto")
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
        """The ticket is where work that leaves lives. The chat links to it; it
        is not a second place the work is kept."""
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
        makes auto file. Pinned because a guard written as an allowlist later
        would silently exclude it.

        Pointed at a temp store for the reason `test_unset_mode_still_files`
        is: filing into the real workshop queue is a production side effect."""
        monkeypatch.setattr(task_scope, "WORKSHOP_SCOPE_DIR", tmp_path)
        # ...and file-backed, which is what a temp store is. Naming a
        # directory as the `workshop` scope now also says "its tickets live
        # in Postgres" (services.task_scope.RELATIONAL_SCOPES), and this test
        # must not reach the real database. Narrowed to THIS directory: a
        # blanket answer would also declare the operator's real checkout
        # file-backed, and a read of it would then rebuild a store from TODO.md.
        _real_scope = task_scope.relational_scope_for_root
        monkeypatch.setattr(
            task_scope, "relational_scope_for_root",
            lambda r: None if Path(r) == tmp_path else _real_scope(r))
        patch = ('Sure.\n\n```task-patch\n{"add":[{"text":"Filed by auto",'
                 '"acceptance":{"kind":"command","spec":"pytest -q"}}]}\n```\n')
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
        t = chat_store.create_thread(context_key="general", mode="auto")
        assert "AUTO" in chat_context.chat_mode_block(
            chat_store.get_thread(t["id"])["mode"])
        client.post(f"/chat/threads/{t['id']}/mode", json={"mode": "direct"})
        assert "DIRECT" in chat_context.chat_mode_block(
            chat_store.get_thread(t["id"])["mode"])

    def test_the_route_maps_the_retired_value_forward(self, client):
        """A tab opened before the collapse still has `orchestrated` in its
        bundle. Refusing it would read as the control being broken on the one
        chat that has been filing for months; it stores as `auto`."""
        from services import chat_store
        t = chat_store.create_thread(context_key="general")
        r = client.post(f"/chat/threads/{t['id']}/mode", json={"mode": "orchestrated"})
        assert r.status_code == 200
        assert r.json()["mode"] == "auto"
        assert chat_store.get_thread(t["id"])["mode"] == "auto"

    def test_junk_is_still_refused(self, client):
        from services import chat_store
        t = chat_store.create_thread(context_key="general")
        r = client.post(f"/chat/threads/{t['id']}/mode", json={"mode": "sideways"})
        assert r.status_code == 400
        assert chat_store.get_thread(t["id"])["mode"] == "direct"

    def test_a_new_chat_can_default_to_auto(self, client):
        """The operator's default for new chats is the same enum, so the
        settings screen can offer both."""
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
    # Carries an acceptance because filing REQUIRES a runnable one
    # (services/ticket_format); these cases are about the mode guard, not about
    # the acceptance gate, which has its own file.
    PATCH = ('Sure.\n\n```task-patch\n{"add":[{"text":"Should not be filed",'
             '"acceptance":{"kind":"command","spec":"pytest -q"}}]}\n```\n')

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
        # ...and file-backed, which is what a temp store is. Naming a
        # directory as the `workshop` scope now also says "its tickets live
        # in Postgres" (services.task_scope.RELATIONAL_SCOPES), and this test
        # must not reach the real database. Narrowed to THIS directory: a
        # blanket answer would also declare the operator's real checkout
        # file-backed, and a read of it would then rebuild a store from TODO.md.
        _real_scope = task_scope.relational_scope_for_root
        monkeypatch.setattr(
            task_scope, "relational_scope_for_root",
            lambda r: None if Path(r) == tmp_path else _real_scope(r))
        cleaned, notes = task_scope.apply_task_patch("general", self.PATCH, "t1", None)
        assert not any("Direct mode" in n for n in notes)
        from services import tasks
        assert any("Should not be filed" in (t.get("text") or "")
                   for t in tasks._load(tmp_path))
