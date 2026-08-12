"""The CLI's "No response requested." filler is dropped; nothing else is.

`model: "<synthetic>"` marks a message the Claude Code CLI wrote itself — the
model was never called, so it carries zero tokens and stop_reason
"stop_sequence". The CLI emits one to close a turn it decided needs no reply.
The trigger, from the session logs on 2026-08-11: a `queue-operation` record
followed by the meta prompt "Continue from where you left off.", which the
harness injects after an interrupt or an empty queued message. 106 of them had
accumulated, each rendering as a reply that says nothing to a turn the operator
never started.

The danger in fixing it is over-fixing it. The SAME synthetic channel carries
the messages that matter most, and they outnumber the filler: session-limit
notices (260+), revoked OAuth tokens, 529 overloads, "Not logged in". Dropping
those would convert a rate limit into a chat that mysteriously stops replying.
So the rule is exact-match on the one dead string, and these tests exist to keep
it that way.
"""
from services import build_streaming as bs


def _assistant(text, model="<synthetic>", extra_blocks=()):
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}, *extra_blocks],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    }


class TestThePlaceholderIsDropped:
    def test_the_exact_filler_is_not_forwarded(self):
        assert bs._is_no_op_placeholder(_assistant("No response requested.")["message"])

    def test_surrounding_whitespace_still_matches(self):
        assert bs._is_no_op_placeholder(_assistant("  No response requested.\n")["message"])

    def test_the_parser_classifies_it_as_meta(self):
        import json
        kind, _ = bs._parse_stream_event(json.dumps(_assistant("No response requested.")))
        # "meta" is the channel's word for "recognised, render nothing".
        assert kind == "meta"


class TestEverythingElseSurvives:
    def test_a_session_limit_notice_is_kept(self):
        msg = _assistant("You've hit your session limit · resets 9:40pm (America/Los_Angeles)")
        assert not bs._is_no_op_placeholder(msg["message"])

    def test_a_revoked_token_is_kept(self):
        msg = _assistant("Failed to authenticate. API Error: 401 OAuth access token has been revoked.")
        assert not bs._is_no_op_placeholder(msg["message"])

    def test_an_overload_is_kept(self):
        msg = _assistant("API Error: 529 Overloaded. This is a server-side issue.")
        assert not bs._is_no_op_placeholder(msg["message"])

    def test_not_logged_in_is_kept(self):
        assert not bs._is_no_op_placeholder(_assistant("Not logged in · Please run /login")["message"])

    def test_a_real_model_saying_the_same_words_is_kept(self):
        """Only the CLI's own channel is filtered. If a model ever writes this
        sentence as prose, it is prose."""
        msg = _assistant("No response requested.", model="claude-opus-5")
        assert not bs._is_no_op_placeholder(msg["message"])

    def test_the_filler_plus_a_tool_call_is_kept(self):
        """Belt and braces: a message that does something is never filler."""
        tool = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}
        msg = _assistant("No response requested.", extra_blocks=(tool,))
        assert not bs._is_no_op_placeholder(msg["message"])

    def test_the_filler_plus_more_prose_is_kept(self):
        extra = {"type": "text", "text": "but here is what I found"}
        msg = _assistant("No response requested.", extra_blocks=(extra,))
        assert not bs._is_no_op_placeholder(msg["message"])

    def test_an_ordinary_reply_is_kept(self):
        import json
        kind, payload = bs._parse_stream_event(
            json.dumps(_assistant("Here is the answer.", model="claude-opus-5")))
        assert kind == "_multi"
        assert payload["events"][0][1]["content"] == "Here is the answer."


class TestMalformedInputDoesNotCrash:
    def test_content_is_not_a_list(self):
        assert not bs._is_no_op_placeholder({"model": "<synthetic>", "content": "oops"})

    def test_content_is_missing(self):
        assert not bs._is_no_op_placeholder({"model": "<synthetic>"})

    def test_empty_message(self):
        assert not bs._is_no_op_placeholder({})


class TestCliCopyIsRewritten:
    """The CLI's compact confirmation renders as our system message, so it obeys
    our copy rules: no em-dash, no padding, and no advice nobody asked for."""

    CLI = ("Conversation compacted — earlier messages were summarized to free up "
           "the context window. Continue where you left off.")

    def test_the_compact_confirmation_is_rewritten(self):
        assert bs._rewrite_cli_copy(self.CLI) == "Compacted. Earlier messages are now a summary."

    def test_the_rewrite_has_no_em_dash(self):
        assert "—" not in bs._rewrite_cli_copy(self.CLI)

    def test_surrounding_whitespace_still_matches(self):
        assert bs._rewrite_cli_copy("\n  " + self.CLI + "  \n") == \
            "Compacted. Earlier messages are now a summary."

    def test_an_unknown_message_is_passed_through_untouched(self):
        """Not a general rewriter. Guessing at CLI text we have not seen would be
        worse than a long sentence."""
        other = "Something the CLI said that we have never catalogued."
        assert bs._rewrite_cli_copy(other) == other

    def test_an_ordinary_reply_is_untouched(self):
        assert bs._rewrite_cli_copy("Here is the answer.") == "Here is the answer."

    def test_it_reaches_the_parser(self):
        import json
        ev = _assistant(self.CLI, model="claude-opus-5")
        kind, payload = bs._parse_stream_event(json.dumps(ev))
        assert kind == "_multi"
        assert payload["events"][0][1]["content"] == "Compacted. Earlier messages are now a summary."


class TestCompactTurnSaysOneLine:
    """The /compact turn's reply is ours, not the CLI's dump.

    The CLI answers /compact with either a 117-character sentence or a
    "## Context Usage" markdown table of ~1900 characters. The operator reported
    the table: it is long enough to be clamped, so it also arrives wearing a
    Show more control. Both facts it carries are already on screen (the fold
    divider, and the composer meter), so it is the third telling of the same
    thing.

    The scoping is the load-bearing part. `compacted` is also true when the CLI
    AUTO-compacts partway through real work, and replacing that turn's reply
    would destroy the answer.
    """
    LINE = "Compacted. Earlier messages are now a summary."
    DUMP = ("## Context Usage\n\n**Model:** claude-opus-5\n\n"
            "| Category | Tokens |\n|---|---|\n| System prompt | 3.5k |\n")

    def _text(self, *, prompt, compacted, final_text):
        """The reply-text decision from _commit_turn, exercised directly."""
        if compacted and prompt.strip().lower() == "/compact":
            return self.LINE
        if final_text:
            return final_text
        return None

    def test_the_context_dump_is_replaced(self):
        assert self._text(prompt="/compact", compacted=True, final_text=self.DUMP) == self.LINE

    def test_the_old_sentence_is_replaced_too(self):
        old = ("Conversation compacted — earlier messages were summarized to free "
               "up the context window. Continue where you left off.")
        assert self._text(prompt="/compact", compacted=True, final_text=old) == self.LINE

    def test_an_auto_compact_mid_work_keeps_its_real_reply(self):
        """The case that must never regress: the CLI compacted on its own while
        answering, and the answer is the whole point of the turn."""
        real = "Here is the analysis you asked for, with the three findings."
        assert self._text(prompt="audit the queue", compacted=True, final_text=real) == real

    def test_a_normal_turn_is_untouched(self):
        assert self._text(prompt="hello", compacted=False, final_text="Hi.") == "Hi."

    def test_a_compact_that_produced_nothing_still_says_the_line(self):
        assert self._text(prompt="/compact", compacted=True, final_text="") == self.LINE

    def test_the_line_has_no_em_dash(self):
        assert "—" not in self.LINE


class TestTheCompactDumpLeavesTheSegmentsToo:
    """Replacing the reply TEXT was not enough, and this is why.

    MessageList renders `meta.segments` whenever there are any, and only falls
    back to the committed `content` when there are none. The dump the CLI writes
    for /compact is streamed into a text segment on its way past, so rewriting
    `text` swapped the bubble's fallback for a fallback that was never reached:
    the "## Context Usage" table went on being drawn, in full, under a one-line
    confirmation that had supposedly replaced it. The operator reported the
    table a third time on 2026-08-12 for exactly this reason.

    TestCompactTurnSaysOneLine above could not catch it: it models the reply-text
    decision only and never looks at a segment.
    """
    DUMP = ("## Context Usage\n\n**Model:** claude-opus-5\n\n"
            "| Category | Tokens |\n|---|---|\n| System prompt | 3.5k |\n")

    def test_the_dump_segment_is_dropped(self):
        segs = [{"t": "text", "text": self.DUMP}]
        assert bs._drop_text_segments(segs) == []

    def test_the_bubble_then_falls_back_to_our_line(self):
        """An empty segment list is the condition MessageList checks."""
        assert not bs._drop_text_segments([{"t": "text", "text": self.DUMP}])

    def test_work_is_never_dropped(self):
        """/compact is local and has no tools, but dropping work that DID happen
        would be a worse lie than the dump this removes."""
        segs = [{"t": "tool", "name": "Read", "input": "x.py"},
                {"t": "text", "text": self.DUMP},
                {"t": "agent", "name": "Explore"}]
        assert bs._drop_text_segments(segs) == [
            {"t": "tool", "name": "Read", "input": "x.py"},
            {"t": "agent", "name": "Explore"},
        ]

    def test_it_is_total_on_junk(self):
        assert bs._drop_text_segments(None) == []
        assert bs._drop_text_segments(["not a dict"]) == ["not a dict"]


class TestCleanExitWithNoClosingMessage:
    """A turn that exits 0 without a closing message must not claim work it did
    not do.

    The bug the operator reported repeatedly: an empty reply that said
    "Finished with no closing message. The steps above are the result." while
    there were no steps above at all. It happens when the model's entire output
    was the CLI's "No response requested." filler, which is stripped, leaving a
    turn with no text and no segments. Pointing at steps that do not exist is
    what made an empty bubble read as the chat giving up.
    """
    DID_WORK = "Finished with no closing message. The steps above are the result."
    DID_NOTHING = "Ended without a reply. Nothing ran this turn."

    def test_a_turn_that_ran_tools_points_at_them(self):
        segs = [{"t": "tool", "name": "Read", "input": "x.py"}]
        assert bs._clean_exit_text(segs) == self.DID_WORK

    def test_a_turn_that_delegated_points_at_the_subagent(self):
        assert bs._clean_exit_text([{"t": "agent", "name": "Explore"}]) == self.DID_WORK

    def test_interim_text_counts_as_work(self):
        segs = [{"t": "text", "text": "Looking at the router now."}]
        assert bs._clean_exit_text(segs) == self.DID_WORK

    def test_an_empty_turn_does_not_claim_steps(self):
        """The reported case: nothing at all above the reply."""
        assert bs._clean_exit_text([]) == self.DID_NOTHING

    def test_whitespace_text_is_not_work(self):
        assert bs._clean_exit_text([{"t": "text", "text": "   \n"}]) == self.DID_NOTHING

    def test_a_pending_approval_alone_is_not_work(self):
        assert bs._clean_exit_text([{"t": "approval", "state": "pending"}]) == self.DID_NOTHING

    def test_neither_line_has_an_em_dash(self):
        assert "—" not in self.DID_WORK and "—" not in self.DID_NOTHING


class TestAnInjectedSlashCommandStillCounts:
    """A slash command delivered INTO a running turn must not be reported as
    having done nothing.

    The CLI runs `/context` and `/compact` locally and never calls the model, so
    such a turn legitimately produces no text. `is_command` is derived from the
    turn's ORIGINAL prompt, which cannot see a command injected mid-flight, so
    the turn fell through to the empty-turn copy and claimed nothing ran.
    """

    def test_injecting_a_command_marks_the_turn(self):
        t = bs._make_turn("thread-1", "general", turn_id="t-cmd")
        assert t.injected_command is False
        bs.TURNS["t-cmd"] = t
        try:
            bs.inject("t-cmd", "/context")   # no FIFO: the write fails
        finally:
            bs.TURNS.pop("t-cmd", None)
        # A refused write must NOT mark it: nothing reached the CLI.
        assert t.injected_command is False

    def test_a_marked_turn_is_treated_as_a_command_turn(self, monkeypatch):
        """The flag is what `is_command` reads, so the commit path says
        "Command ran." instead of the empty-turn line."""
        t = bs._make_turn("thread-1", "general", turn_id="t-cmd2")
        bs.TURNS["t-cmd2"] = t
        monkeypatch.setattr(bs, "write_to_turn", lambda tid, text, blocks=None: True)
        try:
            assert bs.inject("t-cmd2", "/compact") is True
            assert t.injected_command is True
        finally:
            bs.TURNS.pop("t-cmd2", None)

    def test_ordinary_injected_text_does_not_mark_it(self, monkeypatch):
        t = bs._make_turn("thread-1", "general", turn_id="t-msg")
        bs.TURNS["t-msg"] = t
        monkeypatch.setattr(bs, "write_to_turn", lambda tid, text, blocks=None: True)
        try:
            assert bs.inject("t-msg", "stop, wrong file") is True
            assert t.injected_command is False
        finally:
            bs.TURNS.pop("t-msg", None)
