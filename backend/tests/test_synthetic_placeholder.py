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
