"""Echoed stdin messages are recognised so they can be DROPPED.

`--replay-user-messages` (CLI 2.1.143) makes the CLI re-emit every stdin user
message on stdout: the turn's own prompt, its synthetic /compact turns, a
subagent's prompt on the sidechain, and anything injected mid-turn. None of it is
output. Recognising the shape is what keeps it out of the transcript — a tool
result and an echoed message arrive as the same `user` event type.

There WAS a receipt built on this: an `injection_read` event driving a "Read by
the turn in progress" line under the operator's own message. That was an invented
pattern — standard chat does not annotate your messages with delivery state — and
it is gone. What remains is the classification, which is load-bearing.
"""
from services import build_streaming as bs


def _user_event(content):
    return '{"type": "user", "message": {"role": "user", "content": %s}}' % content


class TestClassification:
    def test_a_replayed_text_message_is_an_echo(self):
        line = _user_event('[{"type": "text", "text": "stop, wrong file"}]')
        kind, payload = bs._parse_stream_event(line)
        assert kind == "user_echo"
        assert payload["text"] == "stop, wrong file"
        # An operator message has no sidechain origin. A subagent's PROMPT
        # arrives in exactly this shape and does carry one, which is the only
        # thing that separates them. See test_subagent_stream.
        assert payload["parent"] is None

    def test_a_tool_result_is_not_an_echo(self):
        line = _user_event('[{"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"}]')
        kind, payload = bs._parse_stream_event(line)
        assert kind == "tool_result"
        assert payload["tool_use_id"] == "tu_1"

    def test_a_bare_string_is_an_echo(self):
        """The CLI's synthetic /compact turns carry a plain string. It must not
        crash the reader, and it is filtered out later by not matching anything
        we injected."""
        kind, payload = bs._parse_stream_event(_user_event('"compacted"'))
        assert (kind, payload["text"]) == ("user_echo", "compacted")

    def test_empty_content_does_not_crash(self):
        kind, payload = bs._parse_stream_event(_user_event("[]"))
        assert kind == "tool_result"
        assert payload["tool_use_id"] is None


class TestEchoesAreNotShown:
    def test_no_echo_reaches_the_event_stream(self):
        """An echo of the operator's own message would render it a second time —
        once where they sent it, once wherever the CLI got round to reading it.
        Every `user_echo` is dropped, whatever its origin."""
        import inspect
        src = inspect.getsource(bs._drive_turn)
        i = src.index('elif kind == "user_echo":')
        branch = src[i:src.index("_push(turn, kind, payload)", i)]
        assert "return saw_tool" in branch
        assert "_push" not in branch

    def test_nothing_tracks_delivery_state_any_more(self):
        """The "Read by the turn in progress" line was an invented pattern. Its
        bookkeeping went with it — a Turn holds no receipt list."""
        t = bs.Turn(id="t1", thread_id="th1", agent_slug="a")
        assert not hasattr(t, "awaiting_receipt")


class TestTheFlagIsPassed:
    def test_replay_is_requested(self):
        """Still required: without it the CLI emits no user echoes at all, and
        the sidechain subagent prompts this reader classifies never appear.

        Asserted against the argv the turn is actually spawned with, not against
        the source text of the function that used to build it — which is why
        extracting the builder broke this and not the behaviour."""
        cmd, _ = bs._build_cmd({
            "turn_id": "t1", "model": "claude-opus-5",
            "session_id": None, "resume": False, "cwd": "/tmp",
        })
        assert "--replay-user-messages" in cmd
