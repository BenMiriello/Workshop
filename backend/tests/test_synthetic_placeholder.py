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
