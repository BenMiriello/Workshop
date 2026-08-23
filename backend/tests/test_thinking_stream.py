"""The model's reasoning is captured from the partial-message stream and kept
out of the answer.

The shapes here were recorded from Claude Code CLI 2.1.143 run with
`--include-partial-messages` (probe in scratch_thinking_stream.jsonl): the
reasoning text arrives only as `thinking_delta` chunks inside `stream_event`
frames, because the final assembled `assistant` message redacts the thinking
block's text to "" and keeps just a signature. So the ONLY place the reasoning
text exists is these deltas, and dropping them (the pre-2026-08 behaviour) lost
the reasoning entirely.

These pin: a thinking_delta becomes a `thinking` segment; consecutive deltas
coalesce; text_delta and the redacted final block add nothing (no double count);
reasoning never reaches _turn_content (the answer); and a subagent's reasoning
nests under its chip.
"""
import json

from services import build_streaming as bs


def _delta(kind, value, index=0, parent=None):
    inner = {"type": kind}
    if kind == "thinking_delta":
        inner["thinking"] = value
    elif kind == "text_delta":
        inner["text"] = value
    ev = {"type": "stream_event",
          "event": {"type": "content_block_delta", "index": index, "delta": inner}}
    if parent:
        ev["parent_tool_use_id"] = parent
    return json.dumps(ev)


def _assistant(blocks, parent=None):
    ev = {"type": "assistant", "message": {"role": "assistant", "content": blocks}}
    if parent:
        ev["parent_tool_use_id"] = parent
    return json.dumps(ev)


def _turn():
    return bs.Turn(id="t1", thread_id="th1", agent_slug="general")


def _feed(turn, lines):
    for line in lines:
        parsed = bs._parse_stream_event(line)
        assert parsed is not None, line
        bs._segment_ingest(turn, *parsed)


class TestParsing:
    def test_a_thinking_delta_becomes_a_thinking_event(self):
        kind, payload = bs._parse_stream_event(_delta("thinking_delta", "weighing it up"))
        assert kind == "thinking"
        assert payload["content"] == "weighing it up"
        assert payload["parent"] is None

    def test_a_text_delta_is_ignored_so_it_cannot_double_the_answer(self):
        # Text is taken from the whole `assistant` message, never from the
        # partial stream, so a text_delta must be inert here.
        kind, _ = bs._parse_stream_event(_delta("text_delta", "the answer"))
        assert kind == "meta"

    def test_an_empty_thinking_delta_is_meta_not_an_empty_segment(self):
        kind, _ = bs._parse_stream_event(_delta("thinking_delta", ""))
        assert kind == "meta"

    def test_a_subagents_reasoning_carries_its_parent(self):
        _, payload = bs._parse_stream_event(_delta("thinking_delta", "hmm", parent="tu_9"))
        assert payload["parent"] == "tu_9"


class TestSegments:
    def test_reasoning_folds_into_one_segment_before_the_answer(self):
        turn = _turn()
        _feed(turn, [
            _delta("thinking_delta", "Let me work "),
            _delta("thinking_delta", "through this."),
            # The whole assistant message the CLI emits after the deltas: its
            # thinking block is redacted to "", and text/tool come from here.
            _assistant([
                {"type": "thinking", "thinking": "", "signature": "abc"},
                {"type": "text", "text": "The answer is 42."},
            ]),
        ])
        assert [s["t"] for s in turn.segments] == ["thinking", "text"]
        assert turn.segments[0]["text"] == "Let me work through this."
        assert turn.segments[1]["text"] == "The answer is 42."

    def test_reasoning_is_not_part_of_the_answer(self):
        turn = _turn()
        _feed(turn, [
            _delta("thinking_delta", "secret reasoning the operator should not read as the reply"),
            _assistant([{"type": "text", "text": "Short answer."}]),
        ])
        assert bs._turn_content(turn) == "Short answer."
        assert "secret reasoning" not in bs._turn_content(turn)

    def test_a_subagents_reasoning_nests_under_its_chip(self):
        turn = _turn()
        _feed(turn, [
            _assistant([{"type": "tool_use", "id": "tu_9", "name": "Agent",
                         "input": {"description": "probe", "prompt": "go"}}]),
            _delta("thinking_delta", "delegate reasoning", parent="tu_9"),
        ])
        assert len(turn.segments) == 1
        agent = turn.segments[0]
        assert agent["t"] == "agent"
        assert [s["t"] for s in agent["steps"]] == ["thinking"]
        assert agent["steps"][0]["text"] == "delegate reasoning"
