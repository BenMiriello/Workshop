"""A turn is an interval; a message is a point. These pin what that means.

Every assertion here traces to a reported symptom, and the evidence for each is
quoted in docs/platform-redesign/chat-message-ordering-audit-2026-08-05.md:

  - "my message got inserted ABOVE compact" — a mid-turn message was stored
    above an assistant placeholder stamped 91 seconds EARLIER than itself.
  - "you won't even know that til now" — nothing told the next turn that three
    messages had been typed in sequence before any was read.
"""
import asyncio

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


class TestTheOperatorsMessagesRenderAboveTheReplyThatReadThem:
    """THE BUG THIS PASS EXISTS TO FIX.

    The operator sent five messages during one long turn. When the turn ended,
    all five rendered BELOW the assistant reply that had answered them, so the
    chat read as if he had been ignored.

    The cause was structural: the reply CLAIMED ITS SLOT when the turn started,
    so anything sent afterwards was filed below an empty placeholder, and the
    finished text was written back into that placeholder — above them all. The
    run is no longer in the log at all until it commits, so this cannot recur
    without someone putting the placeholder back.
    """

    def test_a_message_delivered_mid_run_lands_ABOVE_the_finalized_reply(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "go build it")
        chat_store.begin_run(tid, "run-1")
        for text in ("also the header", "and the footer", "wait, dark mode too"):
            chat_store.append_message_during_turn(
                tid, "user", text,
                meta={"injected": True, "during_run": "run-1", "at_segment": 4})
        chat_store.commit_run(tid, "run-1", [], "Done, all of it.")

        msgs = chat_store.get_thread(tid)["messages"]
        assert [m["role"] for m in msgs] == ["user", "user", "user", "user", "assistant"]
        assert msgs[-1]["content"] == "Done, all of it."
        # Every message the run was handed is above the reply that read it.
        reply_at = len(msgs) - 1
        for i, m in enumerate(msgs):
            if (m.get("meta") or {}).get("during_run") == "run-1":
                assert i < reply_at

    def test_a_message_that_never_reached_the_run_lands_BELOW_the_reply(self, thread):
        """The other half, and it has to fall out of the same rule. A message the
        run did not see must not sit above a reply that could not have answered
        it — that is the same lie in the other direction."""
        tid = thread["id"]
        chat_store.append_message(tid, "user", "go build it")
        chat_store.begin_run(tid, "run-1")
        chat_store.commit_run(tid, "run-1", [], "Done.")
        chat_store.append_message(tid, "user", "one more thing")

        msgs = chat_store.get_thread(tid)["messages"]
        assert [m["content"] for m in msgs] == ["go build it", "Done.", "one more thing"]

    def test_the_running_reply_is_rendered_last_while_it_runs(self, thread):
        """During the run the live snapshot is the trailing message, so nothing
        the operator sends jumps above output already on screen. When the run
        commits, the reply is still last — the bubble never moves."""
        tid = thread["id"]
        chat_store.append_message(tid, "user", "go")
        chat_store.begin_run(tid, "run-1")
        chat_store.update_run(tid, "run-1", [{"t": "text", "text": "working"}], "working")
        assert [m["role"] for m in chat_store.get_thread(tid)["messages"]] == \
            ["user", "assistant"]
        chat_store.append_message_during_turn(
            tid, "user", "wait", meta={"during_run": "run-1"})
        during = chat_store.get_thread(tid)["messages"]
        assert [m["role"] for m in during] == ["user", "user", "assistant"]
        assert during[-1]["meta"]["streaming"] is True
        chat_store.commit_run(tid, "run-1", [], "final")
        after = chat_store.get_thread(tid)["messages"]
        assert [m["role"] for m in after] == ["user", "user", "assistant"]
        assert after[-1]["content"] == "final"


class TestStoreOrdering:
    def test_mid_run_message_is_filed_with_the_run_it_arrived_in(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "go")
        chat_store.begin_run(tid, "run-1")
        chat_store.append_message_during_turn(
            tid, "user", "actually wait",
            meta={"injected": True, "during_run": "run-1", "at_segment": 3})
        # [-1] is the run's live view; the committed tail is the message.
        msgs = chat_store.get_thread(tid)["messages"]
        assert msgs[-2]["role"] == "user"
        assert msgs[-2]["meta"]["during_run"] == "run-1"
        assert msgs[-2]["meta"]["at_segment"] == 3

    def test_with_no_live_turn_it_is_an_ordinary_append(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "one")
        chat_store.append_message_during_turn(tid, "user", "two", meta={"injected": True})
        assert [m["content"] for m in chat_store.get_thread(tid)["messages"]] == ["one", "two"]

    def test_committing_records_when_the_run_started_and_ended(self, thread):
        """A TURN IS AN INTERVAL. `ts` is now when the reply LANDED, which is the
        honest answer to "when did this enter the conversation"; the start is
        recorded alongside it so the interval is still answerable."""
        tid = thread["id"]
        chat_store.begin_run(tid, "run-2")
        chat_store.commit_run(tid, "run-2", [], "done")
        m = chat_store.get_thread(tid)["messages"][-1]
        assert m["meta"]["ended_at"]
        assert m["meta"]["started_at"] <= m["meta"]["ended_at"]
        assert m["meta"]["streaming"] is False

    def test_committing_clears_the_active_run(self, thread):
        tid = thread["id"]
        chat_store.begin_run(tid, "run-3")
        assert chat_store.active_run_id(tid) == "run-3"
        chat_store.commit_run(tid, "run-3", [], "done")
        assert chat_store.active_run_id(tid) is None
        assert chat_store.previous_run_id(tid) == "run-3"


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
    """`at_segment` decides where the transcript shows a mid-run message.

    It used to be read from the STORED reply, whose segments are written on a
    throttle — so it lagged what was on screen. The error is one-directional: a
    low `at_segment` renders the message EARLIER than it arrived, above output it
    never saw, which is what makes a reply look like an answer to a message it
    had not read. Ask the live run instead.
    """

    def test_it_reads_the_running_turn_not_the_throttled_copy(self, thread):
        import routes.chat as rc
        from services import build_streaming

        tid = thread["id"]
        chat_store.append_message(tid, "user", "go")
        chat_store.begin_run(tid, "run-live")
        # On disk: 2 segments (the last throttled write). In flight: 7.
        chat_store.update_run(tid, "run-live", [{"t": "text", "text": "x"}] * 2, "x")
        turn = build_streaming.Turn(id="run-live", thread_id=tid, agent_slug="a")
        turn.segments = [{"t": "text", "text": "x"}] * 7
        build_streaming.TURNS["run-live"] = turn
        try:
            rc._file_during_run(tid, "run-live", "wait")
        finally:
            build_streaming.TURNS.pop("run-live", None)

        msg = chat_store.get_thread(tid)["messages"][-2]
        assert msg["content"] == "wait"
        assert msg["meta"]["at_segment"] == 7

    def test_it_falls_back_to_the_live_snapshot_for_an_adopted_run(self, thread):
        """A run adopted after a restart is not in this process's registry, so
        the persisted snapshot is the only number available — and is still better
        than nothing."""
        import routes.chat as rc

        tid = thread["id"]
        chat_store.append_message(tid, "user", "go")
        chat_store.begin_run(tid, "run-gone")
        chat_store.update_run(tid, "run-gone", [{"t": "text", "text": "x"}] * 3, "x")
        rc._file_during_run(tid, "run-gone", "wait")
        assert chat_store.get_thread(tid)["messages"][-2]["meta"]["at_segment"] == 3


class TestOneRunPerThread:
    """A message sent while a run is going JOINS it. It must never start a
    second run, and the server must be the one deciding — a browser only knows
    whether its own stream is open.

    Observed live on 2026-08-07: thread 919bc949 had turns 2c7dc100 and 470bd00c
    both streaming at once, and the operator's message reached neither.
    """

    def _live(self, run_id, thread_id, spawned=True):
        from services import build_streaming
        t = build_streaming.Turn(id=run_id, thread_id=thread_id, agent_slug="a")
        t.spawned = spawned
        build_streaming.TURNS[run_id] = t
        return t

    def test_the_second_mid_run_message_still_finds_the_run(self, thread):
        """THE OLD REGRESSION, kept pinned. Finding the live run used to mean
        scanning the transcript for a streaming flag, and the first mid-turn
        message filed after it broke the scan. It is one field now."""
        import routes.chat as rc
        from services import build_streaming

        tid = thread["id"]
        chat_store.append_message(tid, "user", "go")
        chat_store.begin_run(tid, "run-1")
        self._live("run-1", tid)
        try:
            assert rc._live_run_id(tid) == "run-1"
            chat_store.append_message_during_turn(
                tid, "user", "first", meta={"during_run": "run-1"})
            assert rc._live_run_id(tid) == "run-1"
            chat_store.append_message_during_turn(
                tid, "user", "second", meta={"during_run": "run-1"})
            assert rc._live_run_id(tid) == "run-1"
        finally:
            build_streaming.TURNS.pop("run-1", None)

    def test_a_finished_run_is_not_live(self, thread):
        import routes.chat as rc
        from services import build_streaming

        tid = thread["id"]
        chat_store.begin_run(tid, "run-done")
        t = self._live("run-done", tid)
        t.complete = True
        try:
            assert rc._live_run_id(tid) is None
        finally:
            build_streaming.TURNS.pop("run-done", None)

    def test_a_run_this_process_never_had_is_not_live(self, thread):
        """A run lost to a restart leaves `active_run_id` behind. If that counted
        as live, every later message would be delivered to a process that no
        longer exists and the thread would take no new turns at all."""
        import routes.chat as rc
        tid = thread["id"]
        chat_store.begin_run(tid, "run-vanished")
        assert rc._live_run_id(tid) is None

    def test_an_idle_thread_has_no_live_run(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        chat_store.append_message(tid, "user", "hi")
        assert rc._live_run_id(tid) is None

    def test_the_turn_route_joins_instead_of_starting_a_rival(self, thread, monkeypatch):
        import asyncio
        import routes.chat as rc
        from services import build_streaming

        tid = thread["id"]
        chat_store.append_message(tid, "user", "go")
        chat_store.begin_run(tid, "run-1")
        live = self._live("run-1", tid)
        live.segments = [{"t": "text", "text": "x"}] * 5

        started = []
        monkeypatch.setattr(build_streaming, "inject", lambda *a, **k: True)

        async def never(*a, **kw):
            started.append(a)
            raise AssertionError("a second run must not be started")
        monkeypatch.setattr(build_streaming, "start_turn", never)

        try:
            r = asyncio.run(rc.start_streaming_turn(tid, rc.StartTurnIn(content="joining")))
        finally:
            build_streaming.TURNS.pop("run-1", None)

        assert r == {"turn_id": "run-1", "injected": True, "at_segment": 5}
        assert not started
        last = chat_store.get_thread(tid)["messages"][-2]
        assert last["content"] == "joining"
        assert last["meta"]["during_run"] == "run-1"


class TestClientIdSurvivesTheApi:
    """The browser's send id has to reach the log, or the client is back to
    reconciling by matching text against the tail of the transcript.

    That matching is what pass C deletes: it fired on the wrong bubble whenever
    the operator sent the same words twice, and it could not tell an optimistic
    bubble apart from a committed one at all. The log already dedupes on
    `client_id` (chat_log.append_message_sync); until this landed, the HTTP layer
    accepted it nowhere, so nothing ever reached the log to dedupe on.
    """

    def test_a_turn_records_the_browsers_id(self, thread):
        import routes.chat as rc

        tid = thread["id"]
        rc._record_user_turn(tid, "hello", None, client_id="c-1")
        from services import chat_log
        ev = chat_log.find_message_by_client_id(tid, "c-1")
        assert ev is not None and ev["content"] == "hello"

    def test_the_same_id_twice_appends_once(self, thread):
        """A retry after a dropped response must not show the message twice."""
        import routes.chat as rc

        tid = thread["id"]
        rc._record_user_turn(tid, "hello", None, client_id="c-1")
        rc._record_user_turn(tid, "hello", None, client_id="c-1")
        assert [m["content"] for m in chat_store.get_thread(tid)["messages"]] == ["hello"]

    def test_each_queued_part_carries_its_own_id(self, thread):
        """A drained queue is several messages, so one id for the batch could
        only ever identify one of them."""
        import routes.chat as rc

        tid = thread["id"]
        parts = [rc.TurnPart(text="a", client_id="c-a"),
                 rc.TurnPart(text="b", client_id="c-b")]
        rc._record_user_turn(tid, "a\n\nb", parts, client_id="c-batch")
        from services import chat_log
        assert chat_log.find_message_by_client_id(tid, "c-a")["content"] == "a"
        assert chat_log.find_message_by_client_id(tid, "c-b")["content"] == "b"

    def test_a_mid_run_message_records_it_too(self, thread):
        """The path where it matters most: the message is persisted BEFORE
        delivery is attempted, so a client that never sees the response has no
        way besides this id to tell whether its message landed."""
        import routes.chat as rc

        tid = thread["id"]
        chat_store.append_message(tid, "user", "go")
        chat_store.begin_run(tid, "run-1")
        rc._file_during_run(tid, "run-1", "wait", client_id="c-mid")
        from services import chat_log
        ev = chat_log.find_message_by_client_id(tid, "c-mid")
        assert ev is not None
        assert ev["meta"]["during_run"] == "run-1"
