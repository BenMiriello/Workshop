"""Right after a compaction the meter shows an estimate, not the old reading.

The next model reply can be a minute away on a local model, and until it lands
the meter held the pre-compaction figure. The boundary event states the size of
the summary that replaced the history; the estimate adds what the thread's
system prompt, tools and memory were seen to add after its previous compaction.
A real reading replaces it.
"""
from __future__ import annotations

import asyncio
import json

from services import build_streaming, chat_store, compaction_context
from tests.test_auto_compact_working_state import (  # noqa: F401  (fixtures)
    AUTO_BOUNDARY, RESULT, _chatty, _drive, scoped, summarised)


def _reply(input_tokens: int) -> str:
    return json.dumps({"type": "assistant", "message": {
        "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": input_tokens, "output_tokens": 10}}})


def _boundary(post: int) -> str:
    ev = json.loads(AUTO_BOUNDARY)
    ev["compact_metadata"]["post_tokens"] = post
    return json.dumps(ev)


def _record(monkeypatch):
    pushed, stored = [], []
    real_push = build_streaming._push
    real_set = chat_store.set_thread_context_tokens

    def push(turn, kind, payload):
        if kind == "context":
            pushed.append(payload["tokens"])
        return real_push(turn, kind, payload)

    def set_tokens(tid, tokens, measured_session=None):
        stored.append(tokens)
        return real_set(tid, tokens, measured_session=measured_session)
    monkeypatch.setattr(build_streaming, "_push", push)
    monkeypatch.setattr(chat_store, "set_thread_context_tokens", set_tokens)
    return pushed, stored


class TestTheBoundaryReplacesTheStaleReading:
    def test_with_no_remembered_overhead_the_summary_alone_is_shown(
            self, scoped, summarised, monkeypatch):
        tid = _chatty()
        pushed, stored = _record(monkeypatch)
        asyncio.run(_drive(tid, [_reply(71_694), _boundary(5_400), RESULT]))
        assert pushed == [71_694, 5_400]
        assert stored[:2] == [71_694, 5_400]
        assert chat_store.get_thread(tid)["context_tokens"] == 5_400

    def test_a_remembered_overhead_is_added(self, scoped, summarised, monkeypatch):
        tid = _chatty()
        chat_store.set_thread_context_overhead(tid, 17_000)
        pushed, stored = _record(monkeypatch)
        asyncio.run(_drive(tid, [_reply(71_694), _boundary(5_400), RESULT]))
        assert pushed == [71_694, 22_400]
        assert chat_store.get_thread(tid)["context_tokens"] == 22_400

    def test_a_boundary_without_a_size_leaves_the_reading_alone(
            self, scoped, summarised, monkeypatch):
        tid = _chatty()
        pushed, _ = _record(monkeypatch)
        bare = json.dumps({"type": "system", "subtype": "compact_boundary",
                           "session_id": "s1"})
        asyncio.run(_drive(tid, [_reply(71_694), bare, RESULT]))
        assert pushed == [71_694]


class TestARealReadingWinsAndTeachesTheOverhead:
    def test_the_first_reply_after_the_boundary_replaces_the_estimate(
            self, scoped, summarised, monkeypatch):
        tid = _chatty()
        pushed, stored = _record(monkeypatch)
        asyncio.run(_drive(tid, [
            _reply(71_694), _boundary(5_400), _reply(22_600), RESULT]))
        assert pushed == [71_694, 5_400, 22_600]
        assert stored[-1] == 22_600
        t = chat_store.get_thread(tid)
        assert t["context_tokens"] == 22_600
        assert t["context_overhead"] == 17_200

    def test_the_next_compaction_uses_what_the_last_one_taught(
            self, scoped, summarised, monkeypatch):
        tid = _chatty()
        asyncio.run(_drive(tid, [
            _reply(71_694), _boundary(5_400), _reply(22_600), RESULT]))
        pushed, _ = _record(monkeypatch)
        asyncio.run(_drive(tid, [_reply(80_000), _boundary(8_000), RESULT]))
        assert pushed == [80_000, 25_200]

    def test_a_reading_inside_the_summary_teaches_nothing(
            self, scoped, summarised, monkeypatch):
        tid = _chatty()
        asyncio.run(_drive(tid, [_boundary(5_400), _reply(5_000), RESULT]))
        assert "context_overhead" not in chat_store.get_thread(tid)


class TestTheHelpers:
    def test_post_tokens_reads_the_metadata(self):
        assert compaction_context.post_tokens_of(
            {"compact_metadata": {"post_tokens": 7_841}}) == 7_841
        assert compaction_context.post_tokens_of({}) == 0
        assert compaction_context.post_tokens_of(
            {"compact_metadata": {"post_tokens": 0}}) == 0

    def test_the_parser_keeps_the_metadata(self):
        kind, payload = build_streaming._parse_stream_event(AUTO_BOUNDARY)
        assert kind == "meta" and payload["compact_metadata"]["post_tokens"] == 3_000
