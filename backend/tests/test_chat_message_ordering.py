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


class TestQueuedPartsKeepTheirSendTimes:
    """A queue that drains several messages into one turn used to store one blob
    stamped at the instant it drained. The client had rendered them as separate
    bubbles all along, so client and server disagreed after a reload — and the
    model could not tell a burst typed in one minute from thoughts spread over an
    afternoon."""

    def test_queued_messages_are_reported_as_a_sequence(self):
        msgs = [
            {"role": "assistant", "content": "prev", "ts": "2026-08-05T05:00:00+00:00"},
            {"role": "user", "content": "a", "ts": "2026-08-05T05:56:38+00:00",
             "meta": {"queued": True}},
            {"role": "user", "content": "b", "ts": "2026-08-05T05:58:02+00:00",
             "meta": {"queued": True}},
        ]
        out = chat_context.arrival_notice(msgs)
        assert "2 of the messages" in out
        assert "05:56Z, 05:58Z" in out

    def test_queued_and_injected_count_together(self):
        """Both mean 'written before you had answered any of them'."""
        msgs = [
            {"role": "user", "content": "a", "ts": "2026-08-05T05:56:00+00:00",
             "meta": {"during_turn": "t1"}},
            {"role": "user", "content": "b", "ts": "2026-08-05T05:57:00+00:00",
             "meta": {"queued": True}},
        ]
        assert "2 of the messages" in chat_context.arrival_notice(msgs)

    def test_an_ordinary_single_message_says_nothing(self):
        msgs = [{"role": "user", "content": "hi", "ts": "2026-08-05T05:00:00+00:00"}]
        assert chat_context.arrival_notice(msgs) == ""


class TestRecordUserTurn:
    """routes/chat._record_user_turn — what the server stores for one turn."""

    def test_a_single_message_is_stored_once(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        rc._record_user_turn(tid, "just one", None)
        msgs = chat_store.get_thread(tid)["messages"]
        assert [m["content"] for m in msgs] == ["just one"]
        assert not (msgs[0].get("meta") or {}).get("queued")

    def test_parts_are_stored_separately_at_their_send_times(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        parts = [rc.TurnPart(text="a", sent_at="2026-08-05T05:56:38+00:00"),
                 rc.TurnPart(text="b", sent_at="2026-08-05T05:58:02+00:00")]
        rc._record_user_turn(tid, "a\n\nb", parts)
        msgs = chat_store.get_thread(tid)["messages"]
        assert [m["content"] for m in msgs] == ["a", "b"]
        assert [m["ts"] for m in msgs] == ["2026-08-05T05:56:38+00:00",
                                           "2026-08-05T05:58:02+00:00"]
        assert all(m["meta"]["queued"] for m in msgs)

    def test_the_notice_needs_a_FRESH_read_not_the_route_snapshot(self, thread):
        """The route fetches the thread BEFORE recording (the fresh-session path
        needs that pre-append snapshot as history). Building the arrival notice
        from it would make it silently never fire for the queued case."""
        import routes.chat as rc
        tid = thread["id"]
        stale = chat_store.get_thread(tid)
        rc._record_user_turn(tid, "a\n\nb", [
            rc.TurnPart(text="a", sent_at="2026-08-05T05:56:38+00:00"),
            rc.TurnPart(text="b", sent_at="2026-08-05T05:58:02+00:00")])
        assert chat_context.arrival_notice(stale.get("messages") or []) == ""
        fresh = chat_store.get_thread(tid)
        assert "2 of the messages" in chat_context.arrival_notice(fresh["messages"])

    def test_blank_parts_fall_back_to_the_joined_content(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        rc._record_user_turn(tid, "fallback", [rc.TurnPart(text="   "),
                                               rc.TurnPart(text="")])
        assert [m["content"] for m in chat_store.get_thread(tid)["messages"]] == ["fallback"]


class TestAtSegmentIsMeasuredLive:
    """`at_segment` decides where the transcript shows a mid-turn message.

    It used to be read from the STORED reply, whose segments are written on a
    throttle — so it lagged what was on screen. The error is one-directional: a
    low `at_segment` renders the message EARLIER than it arrived, above output it
    never saw, which is what makes a reply look like an answer to a message it
    had not read. Ask the live turn instead.
    """

    def test_it_reads_the_running_turn_not_the_throttled_copy(self, thread):
        import routes.chat as rc
        from services import build_streaming

        tid = thread["id"]
        chat_store.append_message(tid, "user", "go")
        chat_store.append_streaming_assistant(tid, "turn-live")
        # On disk: 2 segments (the last throttled write). In flight: 7.
        chat_store.update_streaming_assistant(
            tid, "turn-live", [{"t": "text", "text": "x"}] * 2, "x")
        turn = build_streaming.Turn(id="turn-live", thread_id=tid, agent_slug="a")
        turn.segments = [{"t": "text", "text": "x"}] * 7
        build_streaming.TURNS["turn-live"] = turn
        try:
            rc._file_during_turn(tid, "turn-live", "wait")
        finally:
            build_streaming.TURNS.pop("turn-live", None)

        msg = chat_store.get_thread(tid)["messages"][-1]
        assert msg["content"] == "wait"
        assert msg["meta"]["at_segment"] == 7

    def test_it_falls_back_to_the_stored_count_for_an_adopted_turn(self, thread):
        """A turn adopted after a restart is not in this process's registry, so
        the persisted count is the only number available — and is still better
        than nothing."""
        import routes.chat as rc

        tid = thread["id"]
        chat_store.append_message(tid, "user", "go")
        chat_store.append_streaming_assistant(tid, "turn-gone")
        chat_store.update_streaming_assistant(
            tid, "turn-gone", [{"t": "text", "text": "x"}] * 3, "x")
        rc._file_during_turn(tid, "turn-gone", "wait")
        assert chat_store.get_thread(tid)["messages"][-1]["meta"]["at_segment"] == 3
