"""Direct vs Orchestrated: whether work leaves the conversation.

The field already existed with these two names and no behaviour, like
`participants`. These tests pin the behaviour it was given, and in particular
that the guard is REAL: the prompt asks the model not to file in Direct mode,
but a prompt is a request. A chat in Direct mode must be unable to file even if
the model emits a block anyway.
"""
from services import chat_context, task_scope


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

    def test_unset_defaults_to_orchestrated(self):
        """Existing chats have no mode field and must keep working as they did."""
        assert "ORCHESTRATED" in chat_context.chat_mode_block(None)

    def test_junk_defaults_to_orchestrated(self):
        assert "ORCHESTRATED" in chat_context.chat_mode_block("nonsense")


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
        effect, which is its own defect."""
        monkeypatch.setattr(task_scope, "WORKSHOP_ROOT", tmp_path)
        cleaned, notes = task_scope.apply_task_patch("general", self.PATCH, "t1", None)
        assert not any("Direct mode" in n for n in notes)
        from services import tasks
        assert any("Should not be filed" in (t.get("text") or "")
                   for t in tasks._load(tmp_path))
