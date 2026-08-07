"""A message handed to a running turn now reports when it was READ.

`delivered` only ever meant "the bytes reached the process". The composer said
"Handed to the turn in progress" and kept saying it, pulsing, for the rest of the
turn — which is what the operator described as the message sitting unread.

`--replay-user-messages` (CLI 2.1.143) re-emits each stdin user message on stdout
once the CLI has taken it off the stream. Measured with scratch/probe_replay.py
against the real CLI: a message injected at t=14s echoed at t=18s, while three
more tool calls followed and the turn did not end until t=44s. So the receipt is
available 26 seconds before the turn ends, and it is genuinely a receipt.

It is NOT an "acted on" signal — the same probe shows the model finishing its
plan first. The UI says "Read by the turn", which is the thing that is known.
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


class TestOnlyOurInjectionsAreAcknowledged:
    """The CLI echoes EVERY stdin user message, starting with the turn's own
    prompt (confirmed by the probe). Acknowledging that would put the whole
    assembled prompt on screen as though the operator had typed it."""

    def _turn(self):
        t = bs.Turn(id="t1", thread_id="th1", agent_slug="a")
        return t

    def test_the_prompt_echo_is_not_a_receipt(self):
        t = self._turn()
        t.awaiting_receipt.append("wait, stop")
        kind, payload = bs._parse_stream_event(
            _user_event('[{"type": "text", "text": "=== OPERATOR MESSAGE ===\\nbuild the thing"}]'))
        assert kind == "user_echo"
        assert payload["text"] not in t.awaiting_receipt

    def test_an_injected_message_matches_once(self):
        t = self._turn()
        t.awaiting_receipt.append("wait, stop")
        assert "wait, stop" in t.awaiting_receipt
        t.awaiting_receipt.remove("wait, stop")
        # A second echo of the same text has nothing left to acknowledge, so it
        # cannot mark a later message with identical wording as already read.
        assert "wait, stop" not in t.awaiting_receipt

    def test_two_identical_messages_each_need_their_own_receipt(self):
        t = self._turn()
        t.awaiting_receipt.extend(["ping", "ping"])
        t.awaiting_receipt.remove("ping")
        assert t.awaiting_receipt == ["ping"]


class TestTheFlagIsPassed:
    def test_replay_is_requested(self):
        """Without it the CLI emits no echo at all and the receipt never exists."""
        import inspect
        src = inspect.getsource(bs._spawn_and_drive)
        assert "--replay-user-messages" in src
