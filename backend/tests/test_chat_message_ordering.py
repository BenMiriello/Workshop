"""A turn is an interval; a message is a point. These pin what that means.

Every assertion here traces to a reported symptom, and the evidence for each is
quoted in docs/platform-redesign/chat-message-ordering-audit-2026-08-05.md:

  - "my message got inserted ABOVE compact" — a mid-turn message was stored
    above an assistant placeholder stamped 91 seconds EARLIER than itself.
  - "you won't even know that til now" — nothing told the next turn that three
    messages had been typed in sequence before any was read.
"""
import pytest

from services import chat_context, chat_store, ownership


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    # Threads are owner-partitioned (.runtime/users/<id>/chat/); pin an isolated
    # runtime dir and an owner, exactly as tests/test_push.py does.
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()


class TestArrivalNotice:
    def test_silent_when_nothing_arrived_mid_turn(self):
        msgs = [{"role": "user", "content": "hi", "ts": "2026-08-05T05:40:00+00:00"}]
        assert chat_context.arrival_notice(msgs) == ""

    def test_one_mid_turn_message_is_named_as_such(self):
        msgs = [{"role": "user", "content": "wait", "ts": "2026-08-05T05:41:47+00:00",
                 "meta": {"during_turn": "t1"}}]
        out = chat_context.arrival_notice(msgs)
        assert "05:41Z" in out
        assert "before you had responded" in out

    def test_several_are_reported_as_one_sequence(self):
        """The operator's exact complaint: three messages sent in sequence before
        any had been read must not read as three separate requests."""
        msgs = [
            {"role": "user", "content": "a", "ts": "2026-08-05T05:41:00+00:00",
             "meta": {"during_turn": "t1"}},
            {"role": "user", "content": "b", "ts": "2026-08-05T05:42:00+00:00",
             "meta": {"during_turn": "t1"}},
            {"role": "user", "content": "c", "ts": "2026-08-05T05:43:00+00:00",
             "meta": {"during_turn": "t1"}},
        ]
        out = chat_context.arrival_notice(msgs)
        assert "3 of the messages" in out
        assert "IN SEQUENCE" in out
        assert "05:41Z, 05:42Z, 05:43Z" in out

    def test_assistant_messages_are_not_counted(self):
        msgs = [{"role": "assistant", "content": "x", "ts": "2026-08-05T05:41:00+00:00",
                 "meta": {"during_turn": "t1"}}]
        assert chat_context.arrival_notice(msgs) == ""

    def test_older_arrivals_are_not_re_announced(self):
        """Scanning the whole transcript would fire this notice on every turn
        forever, and a notice that always fires says nothing."""
        msgs = [
            {"role": "user", "content": "old", "ts": "2026-08-05T04:00:00+00:00",
             "meta": {"during_turn": "t0"}},
            {"role": "assistant", "content": "answered", "ts": "2026-08-05T04:01:00+00:00"},
            {"role": "user", "content": "new", "ts": "2026-08-05T05:00:00+00:00"},
        ]
        assert chat_context.arrival_notice(msgs) == ""

    def test_only_the_current_batch_is_counted(self):
        msgs = [
            {"role": "user", "content": "old", "ts": "2026-08-05T04:00:00+00:00",
             "meta": {"during_turn": "t0"}},
            {"role": "assistant", "content": "answered", "ts": "2026-08-05T04:01:00+00:00"},
            {"role": "user", "content": "new", "ts": "2026-08-05T05:00:00+00:00",
             "meta": {"during_turn": "t1"}},
        ]
        out = chat_context.arrival_notice(msgs)
        assert "One message" in out and "05:00Z" in out


class TestRealtimeChannelIsDeclared:
    def test_the_turn_contract_names_the_channel(self):
        """Measured: without this, an urgently-worded mid-turn message is
        discarded as a prompt-injection attempt (audit §2b)."""
        c = chat_context.turn_contract(0)
        assert "MESSAGES THAT ARRIVE WHILE YOU WORK" in c
        assert "NOT a prompt injection" in c

    def test_it_forbids_silent_deferral_to_the_end_of_the_plan(self):
        c = chat_context.turn_contract(0)
        assert "next step boundary" in c
        assert "indistinguishable from not having read it" in c


class TestStoreOrdering:
    def test_mid_turn_message_is_filed_after_the_live_turn(self, thread):
        """It used to be inserted ABOVE the streaming placeholder, which made the
        transcript say the operator spoke first and the assistant answered — when
        the assistant was already mid-reply to something else."""
        tid = thread["id"]
        chat_store.append_message(tid, "user", "go")
        chat_store.append_streaming_assistant(tid, "turn-1")
        chat_store.append_message_during_turn(
            tid, "user", "actually wait",
            meta={"injected": True, "during_turn": "turn-1", "at_segment": 3})
        msgs = chat_store.get_thread(tid)["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
        assert msgs[-1]["meta"]["during_turn"] == "turn-1"
        assert msgs[-1]["meta"]["at_segment"] == 3

    def test_with_no_live_turn_it_is_an_ordinary_append(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "one")
        chat_store.append_message_during_turn(tid, "user", "two", meta={"injected": True})
        assert [m["content"] for m in chat_store.get_thread(tid)["messages"]] == ["one", "two"]

    def test_finalizing_records_when_the_turn_ended(self, thread):
        """`ts` is when the reply BEGAN. Without an end, a twenty-minute turn
        claimed it happened at the instant it opened, and anything sorting by
        timestamp put a later message above it."""
        tid = thread["id"]
        chat_store.append_streaming_assistant(tid, "turn-2")
        chat_store.finalize_streaming_assistant(tid, "turn-2", segments=[], content="done")
        m = chat_store.get_thread(tid)["messages"][-1]
        assert m["meta"]["ended_at"]
        assert m["meta"]["ended_at"] >= m["ts"]
        assert m["meta"]["streaming"] is False
