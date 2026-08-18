"""A run is not a message: the lifecycle, the sync endpoint, and the races.

Pass B of docs/chat-rebuild-spec-2026-08-09.md. The run leaves the log — there
is no assistant placeholder while a turn is in flight, only `meta.active_turn_id`
and a live snapshot — and the reply is appended when it finishes. Everything
here pins a consequence of that, or one of the races it was supposed to close.

The ordering consequence itself (the operator's five messages rendering below
the reply that answered them) is pinned in test_chat_message_ordering.py, under
TestTheOperatorsMessagesRenderAboveTheReplyThatReadThem.
"""
import asyncio

import pytest

from services import build_streaming, chat_context, chat_log, chat_store, ownership


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    # THE JOURNAL DIR TOO. Without this these tests write their turn journals
    # into the real `.runtime/turns`, alongside the operator's live runs. They
    # were invisible only because every journal used to be deleted at commit; a
    # turn that produces nothing keeps its journal now (it is the only evidence
    # of why), so test residue would have started accumulating there.
    monkeypatch.setattr(build_streaming, "TURNS_DIR", tmp_path / "turns")
    ownership.set_current_owner(1)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()
        build_streaming.TURNS.clear()


async def _run_guarded(turn, meta, coro) -> None:
    """Drive one turn through the crash guard and wait for it to end."""
    task = build_streaming._drive_task(turn, meta, coro)
    await asyncio.wait_for(turn.finished.wait(), timeout=2)
    await task


def _live(turn_id: str, thread_id: str, *, spawned: bool = True) -> build_streaming.Turn:
    t = build_streaming.Turn(id=turn_id, thread_id=thread_id, agent_slug="a")
    t.spawned = spawned
    build_streaming.TURNS[turn_id] = t
    return t


class TestTheEventsEndpoint:
    """`GET /threads/{id}/events?after=<seq>` is THE sync endpoint. It has to be
    enough on its own: a transcript assembled by merging several endpoints with
    local state is the anti-pattern behind every reorder bug in this system."""

    def test_cursor_zero_returns_the_whole_transcript(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        chat_store.append_message(tid, "user", "one")
        chat_store.begin_turn(tid, "run-1")
        chat_store.commit_turn(tid, "run-1", [], "two")
        chat_store.append_message(tid, "user", "three")

        got = asyncio.run(rc.thread_events(tid, after=0))
        kinds = [(e["kind"], e.get("role") or e.get("state")) for e in got["events"]]
        assert kinds == [
            ("message", "user"),
            ("turn", "started"),
            ("message", "assistant"),
            ("turn", "finished"),
            ("message", "user"),
        ]
        assert [e["seq"] for e in got["events"]] == [1, 2, 3, 4, 5]
        assert got["last_seq"] == 5
        assert got["active_turn"] is None

    def test_a_mid_cursor_returns_exactly_the_delta(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        chat_store.append_message(tid, "user", "one")
        chat_store.append_message(tid, "user", "two")
        cursor = asyncio.run(rc.thread_events(tid, after=0))["last_seq"]
        chat_store.append_message(tid, "user", "three")

        got = asyncio.run(rc.thread_events(tid, after=cursor))
        assert [e.get("content") for e in got["events"]] == ["three"]
        assert got["last_seq"] == cursor + 1
        # Re-polling the same cursor after nothing happened returns nothing, so a
        # client on a focus/reconnect loop is not re-applying the transcript.
        assert asyncio.run(rc.thread_events(tid, after=got["last_seq"]))["events"] == []

    def test_a_run_in_flight_comes_back_with_its_live_snapshot(self, thread):
        """So the client renders a turn in progress from THIS call plus the SSE,
        and nothing else."""
        import routes.chat as rc
        tid = thread["id"]
        chat_store.append_message(tid, "user", "go")
        chat_store.begin_turn(tid, "run-1", model="m")
        chat_store.update_turn(tid, "run-1", [{"t": "text", "text": "half"}], "half")

        got = asyncio.run(rc.thread_events(tid, after=0))
        run = got["active_turn"]
        assert run["turn_id"] == "run-1"
        assert run["started_seq"] == 2
        assert run["live"]["content"] == "half"
        assert run["live"]["meta"]["model"] == "m"
        # And the log itself holds no assistant message for it yet.
        assert not [e for e in got["events"]
                    if e["kind"] == "message" and e["role"] == "assistant"]

    def test_a_missing_thread_is_a_404(self, thread):
        import routes.chat as rc
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as e:
            asyncio.run(rc.thread_events("0" * 32, after=0))
        assert e.value.status_code == 404


class TestTailAndBeforePaging:
    """Phase 3a: `tail`/`before` page the newest end of the transcript, the
    complement of the `after` cursor. No database is configured in this test
    environment (`chat_branches.branch_state_sync` degrades to `hidden=None`
    without one), so these pin the route's PARAM PLUMBING and RESPONSE SHAPE --
    additive keys, mutual exclusion with a real cursor, and page contiguity --
    against a small in-process thread. The branch-membership filter itself
    (`keep_visible` against a real hidden set) and the archive fallback are
    proven separately against real data and a synthetic large/branched thread;
    see the phase 3a handoff for those numbers, not expressible here without
    WORKSHOP_DATABASE_ADMIN_URL."""

    def _seed(self, tid: str, n: int) -> None:
        for i in range(n):
            chat_store.append_message(tid, "user", f"msg{i}")

    def test_after_zero_with_no_tail_is_unchanged(self, thread):
        """The additive requirement: `after=0`/no `tail` must look exactly as it
        did before this phase, plus the two new keys with harmless defaults."""
        import routes.chat as rc
        tid = thread["id"]
        self._seed(tid, 5)
        got = asyncio.run(rc.thread_events(tid, after=0))
        assert [e["seq"] for e in got["events"]] == [1, 2, 3, 4, 5]
        assert got["has_more"] is False
        assert got["first_seq"] == 1

    def test_tail_returns_the_newest_n_not_the_oldest(self, thread):
        """The gap this phase closes: `limit` alone counts from the oldest.
        `tail` must count from the newest end instead."""
        import routes.chat as rc
        tid = thread["id"]
        self._seed(tid, 30)
        got = asyncio.run(rc.thread_events(tid, after=0, tail=5))
        assert [e["seq"] for e in got["events"]] == [26, 27, 28, 29, 30]
        assert got["has_more"] is True
        assert got["first_seq"] == 26

    def test_tail_then_before_composes_without_gap_or_duplicate(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        self._seed(tid, 30)
        page1 = asyncio.run(rc.thread_events(tid, after=0, tail=10))
        assert [e["seq"] for e in page1["events"]] == list(range(21, 31))

        page2 = asyncio.run(
            rc.thread_events(tid, after=0, tail=10, before=page1["first_seq"]))
        assert [e["seq"] for e in page2["events"]] == list(range(11, 21))
        assert page2["has_more"] is True

        page3 = asyncio.run(
            rc.thread_events(tid, after=0, tail=10, before=page2["first_seq"]))
        assert [e["seq"] for e in page3["events"]] == list(range(1, 11))
        assert page3["has_more"] is False
        assert page3["first_seq"] == 1

        combined = page3["events"] + page2["events"] + page1["events"]
        assert [e["seq"] for e in combined] == list(range(1, 31))

    def test_a_real_cursor_wins_over_tail(self, thread):
        """`tail`/`before` are ignored the moment `after` is a real cursor: the
        incremental-sync path a client is mid-poll on must never be intercepted
        by a tail read instead."""
        import routes.chat as rc
        tid = thread["id"]
        self._seed(tid, 10)
        got = asyncio.run(rc.thread_events(tid, after=7, tail=2))
        assert [e["seq"] for e in got["events"]] == [8, 9, 10]

    def test_before_without_tail_uses_the_default_page_size(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        self._seed(tid, 80)
        got = asyncio.run(rc.thread_events(tid, after=0, before=61))
        assert len(got["events"]) == rc._BACK_PAGE_DEFAULT
        assert [e["seq"] for e in got["events"]] == list(range(11, 61))

    def test_tail_larger_than_the_transcript_returns_everything(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        self._seed(tid, 3)
        got = asyncio.run(rc.thread_events(tid, after=0, tail=50))
        assert [e["seq"] for e in got["events"]] == [1, 2, 3]
        assert got["has_more"] is False


class TestSSEResume:
    """The offset-addressed stream is kept as-is (it was already right). This
    pins the property the rest of the rebuild leans on: a reconnect carrying
    Last-Event-ID resumes exactly, with nothing replayed and nothing lost."""

    def _drain(self, turn, after):
        async def go():
            out = []
            async for offset, ev in build_streaming.iter_events(turn, after=after):
                out.append((offset, ev.kind))
            return out
        return asyncio.run(go())

    def test_reconnecting_mid_stream_neither_duplicates_nor_drops(self, thread):
        turn = _live("run-1", thread["id"])
        for i in range(5):
            build_streaming._push(turn, "text", {"content": f"chunk{i}"})
        turn.complete = True

        whole = self._drain(turn, after=0)
        assert [k for _o, k in whole] == ["text"] * 5

        # The client saw through the third frame and reconnects with its id.
        last_event_id = whole[2][0]
        resumed = self._drain(turn, after=last_event_id)
        assert resumed == whole[3:]
        # Concatenating what each connection delivered reproduces the stream
        # exactly once: no frame twice, no frame missing.
        assert whole[:3] + resumed == whole

    def test_a_resume_past_the_end_yields_nothing(self, thread):
        turn = _live("run-2", thread["id"])
        build_streaming._push(turn, "text", {"content": "only"})
        turn.complete = True
        assert self._drain(turn, after=99) == []


class TestPersistBeforeDeliver:
    """The bytes used to go to the turn's stdin FIFO first and the store write
    happened after, inside a best-effort `except` that only logged. A failed
    write meant the model had read a message the transcript never recorded."""

    def test_a_failed_delivery_leaves_exactly_one_copy(self, thread, monkeypatch):
        import routes.chat as rc
        tid = thread["id"]
        chat_store.append_message(tid, "user", "go")
        chat_store.begin_turn(tid, "run-1")
        _live("run-1", tid, spawned=True)   # spawned, and no FIFO: it is ending

        monkeypatch.setattr(build_streaming, "inject", lambda *a, **k: False)
        started = []

        async def fake_start(*, thread_id, t, provider, content, model, turn_id, **kw):
            started.append(turn_id)
            turn = build_streaming.TURNS[turn_id]
            turn.spawned = True
            return turn
        monkeypatch.setattr(rc, "_start_assistant_turn", fake_start)

        out = asyncio.run(rc._send(tid, "the message that could not be delivered"))

        # A fresh run was started for it, and it is in the log ONCE.
        assert len(started) == 1
        assert out["turn_id"] == started[0]
        bodies = [m["content"] for m in chat_store.get_thread(tid)["messages"]]
        assert bodies.count("the message that could not be delivered") == 1

    def test_a_run_that_has_not_spawned_yet_is_not_taken_over(self, thread, monkeypatch):
        """A claimed-but-not-spawned run has no FIFO either, and looks identical
        to `write_to_turn`. Treating them alike would kill a run in its startup
        window, which is the rival-turn bug wearing a different hat."""
        import routes.chat as rc
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-1")
        _live("run-1", tid, spawned=False)
        monkeypatch.setattr(build_streaming, "inject", lambda *a, **k: False)

        async def never(**kw):
            raise AssertionError("must not start a rival run")
        monkeypatch.setattr(rc, "_start_assistant_turn", never)

        out = asyncio.run(rc._send(tid, "hold on"))
        assert out == {"turn_id": "run-1", "injected": False, "at_segment": 0}
        assert chat_store.active_turn_id(tid) == "run-1"
        bodies = [m["content"] for m in chat_store.get_thread(tid)["messages"]]
        assert bodies.count("hold on") == 1

    def test_the_message_is_written_before_the_channel(self, thread, monkeypatch):
        """The ordering itself, observed: by the time anything is handed to the
        run, the transcript already has it."""
        import routes.chat as rc
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-1")
        _live("run-1", tid)
        seen: list[list[str]] = []

        def spy(turn_id, text, *a, **k):
            seen.append([m["content"] for m in chat_store.get_thread(tid)["messages"]])
            return True
        monkeypatch.setattr(build_streaming, "inject", spy)

        asyncio.run(rc._send(tid, "urgent"))
        assert seen and "urgent" in seen[0]


class TestSameTickRivals:
    """Two POSTs landing in the same event-loop tick must not both open a run.
    Observed live on 2026-08-07: one thread with two turns streaming at once and
    the operator's message reaching neither."""

    def test_the_second_post_joins_the_first_run(self, thread, monkeypatch):
        import routes.chat as rc
        tid = thread["id"]
        started: list[str] = []
        delivered: list[str] = []

        async def fake_start(*, thread_id, t, provider, content, model, turn_id, **kw):
            started.append(turn_id)
            turn = build_streaming.TURNS[turn_id]
            turn.spawned = True
            await asyncio.sleep(0)      # a real spawn awaits
            return turn
        monkeypatch.setattr(rc, "_start_assistant_turn", fake_start)
        monkeypatch.setattr(build_streaming, "inject",
                            lambda rid, text, *a, **k: delivered.append(text) or True)

        async def main():
            return await asyncio.gather(
                rc.start_streaming_turn(tid, rc.StartTurnIn(content="first")),
                rc.start_streaming_turn(tid, rc.StartTurnIn(content="second")),
            )
        a, b = asyncio.run(main())

        assert len(started) == 1, "exactly one run may be opened"
        assert a["turn_id"] == b["turn_id"] == started[0]
        # One of them started the run; the other joined it and was delivered.
        assert [r.get("injected") for r in (a, b)].count(True) == 1
        assert delivered == ["second"] or delivered == ["first"]
        # Both messages are in the transcript, exactly once each.
        bodies = [m["content"] for m in chat_store.get_thread(tid)["messages"]]
        assert bodies.count("first") == 1 and bodies.count("second") == 1

    def test_the_claim_is_released_when_the_turn_cannot_start(self, thread, monkeypatch):
        """A claim is taken before the prompt is assembled, because assembly
        awaits. If assembly fails, the thread must not be left claiming a run
        that never spawned — that is a chat which refuses every later message."""
        import routes.chat as rc
        tid = thread["id"]

        async def boom(**kw):
            raise RuntimeError("assembly failed")
        monkeypatch.setattr(rc, "_start_assistant_turn", boom)

        with pytest.raises(RuntimeError):
            asyncio.run(rc._send(tid, "hello"))
        assert chat_store.active_turn_id(tid) is None


class TestStartupReconcile:
    """A run that did not survive a restart leaves `active_turn_id` pointing at a
    journal that is dead or gone. One pass at startup closes it out; there is no
    per-read self-heal any more because there is no flag to orphan."""

    def test_a_crashed_run_becomes_a_lost_run_and_a_message(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "build the thing")
        chat_store.begin_turn(tid, "run-gone")
        chat_store.update_turn(tid, "run-gone", [{"t": "text", "text": "partial"}], "partial")

        build_streaming.reconcile_threads()

        assert chat_store.active_turn_id(tid) is None
        events = chat_log.read_events(tid)
        assert [e["state"] for e in events if e["kind"] == "turn"] == ["started", "lost"]
        last = chat_store.get_thread(tid)["messages"][-1]
        assert last["role"] == "assistant"
        assert last["content"] == chat_store.LOST_TURN_TEXT
        assert "restarted mid-turn" in last["content"]

    def test_a_run_this_process_is_driving_is_left_alone(self, thread):
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-live")
        _live("run-live", tid)
        build_streaming.reconcile_threads()
        assert chat_store.active_turn_id(tid) == "run-live"
        assert not [m for m in chat_store.get_thread(tid)["messages"]
                    if m["content"] == chat_store.LOST_TURN_TEXT]

    def test_an_idle_thread_is_untouched(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "hi")
        before = chat_log.read_events(tid)
        build_streaming.reconcile_threads()
        assert chat_log.read_events(tid) == before

    def test_it_runs_twice_without_saying_it_twice(self, thread):
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-gone")
        build_streaming.reconcile_threads()
        build_streaming.reconcile_threads()
        lost = [m for m in chat_store.get_thread(tid)["messages"]
                if m["content"] == chat_store.LOST_TURN_TEXT]
        assert len(lost) == 1


class TestArrivalNoticeFollowsTheMark:
    """Which messages waited is a LOOKUP on `meta.during_turn`, not a guess from
    where they sit. Reading the tail after the last assistant message stopped
    working the moment the reply started being appended when it FINISHED."""

    def test_it_names_exactly_the_messages_that_waited(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "start")
        chat_store.begin_turn(tid, "run-1")
        chat_store.append_message_during_turn(
            tid, "user", "also this", ts="2026-08-09T05:41:00+00:00",
            meta={"during_turn": "run-1"})
        chat_store.append_message_during_turn(
            tid, "user", "and this", ts="2026-08-09T05:42:00+00:00",
            meta={"during_turn": "run-1"})
        chat_store.commit_turn(tid, "run-1", [], "answered")
        chat_store.append_message(tid, "user", "next turn")

        msgs = chat_store.get_thread(tid)["messages"]
        out = chat_context.arrival_notice(msgs, turn_id=chat_store.previous_turn_id(tid))
        assert "2 of the messages" in out
        assert "05:41Z, 05:42Z" in out

    def test_the_mark_finds_them_although_they_sit_above_the_reply(self, thread):
        """The regression this rework exists for. The mid-turn messages are ABOVE
        the reply now (that is the fix), so a tail scan finds nothing at all."""
        tid = thread["id"]
        chat_store.append_message(tid, "user", "start")
        chat_store.begin_turn(tid, "run-1")
        chat_store.append_message_during_turn(
            tid, "user", "wait", ts="2026-08-09T05:41:00+00:00",
            meta={"during_turn": "run-1"})
        chat_store.commit_turn(tid, "run-1", [], "answered")
        chat_store.append_message(tid, "user", "next")
        msgs = chat_store.get_thread(tid)["messages"]

        assert chat_context.arrival_notice(msgs) == ""          # tail scan: blind
        assert "One message" in chat_context.arrival_notice(msgs, turn_id="run-1")

    def test_an_older_run_is_not_re_announced(self, thread):
        """A notice that fires on every turn forever says nothing."""
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-1")
        chat_store.append_message_during_turn(
            tid, "user", "old", meta={"during_turn": "run-1"})
        chat_store.commit_turn(tid, "run-1", [], "a")
        chat_store.append_message(tid, "user", "next")
        chat_store.begin_turn(tid, "run-2")
        chat_store.commit_turn(tid, "run-2", [], "b")

        msgs = chat_store.get_thread(tid)["messages"]
        assert chat_store.previous_turn_id(tid) == "run-2"
        assert chat_context.arrival_notice(msgs, turn_id="run-2") == ""

    def test_the_resumed_prompt_carries_the_notice(self, thread, monkeypatch):
        """End to end through the path that actually builds the turn's message."""
        import routes.chat as rc
        tid = thread["id"]
        chat_store.append_message(tid, "user", "start")
        chat_store.begin_turn(tid, "run-1")
        chat_store.append_message_during_turn(
            tid, "user", "wait", ts="2026-08-09T05:41:00+00:00",
            meta={"during_turn": "run-1"})
        chat_store.commit_turn(tid, "run-1", [], "answered")

        provider = chat_context.get_provider("general")

        async def no_recall(*a, **kw):
            return []
        monkeypatch.setattr(rc.knowledge, "search", no_recall)

        t = chat_store.get_thread(tid)
        prompt = asyncio.run(rc._resumed_prompt(t, provider, "so, about that"))
        assert "=== SENT WHILE YOU WERE WORKING ===" in prompt
        assert "05:41Z" in prompt


class TestTheBlockingPathIsTheSamePipeline:
    """POST /messages (voice, scripts) starts a run and awaits its finalize. It
    used to have its own completion path, with the action-block pipeline written
    out twice in code that had already drifted."""

    def test_it_returns_the_committed_message(self, thread, monkeypatch):
        import routes.chat as rc
        tid = thread["id"]

        async def fake_start(*, thread_id, t, provider, content, model, turn_id, **kw):
            turn = build_streaming.TURNS[turn_id]
            turn.spawned = True

            async def finish():
                await asyncio.sleep(0.02)
                turn.committed = chat_store.commit_turn(
                    thread_id, turn_id, [], "the spoken reply")
                turn.notes = ["Updated the plan"]
                turn.complete = True
                turn.finished.set()
            asyncio.create_task(finish())
            return turn
        monkeypatch.setattr(rc, "_start_assistant_turn", fake_start)

        out = asyncio.run(rc.send_message(tid, rc.PostMessage(content="say something")))
        assert out["message"]["content"] == "the spoken reply"
        assert out["message"]["role"] == "assistant"
        assert out["notes"] == ["Updated the plan"]
        # And the transcript holds the operator's message above the reply, once.
        bodies = [m["content"] for m in chat_store.get_thread(tid)["messages"]]
        assert bodies == ["say something", "the spoken reply"]

    def test_an_empty_message_is_rejected(self, thread):
        import routes.chat as rc
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as e:
            asyncio.run(rc.send_message(thread["id"], rc.PostMessage(content="   ")))
        assert e.value.status_code == 400


class TestTheDriveLoopAlwaysEndsTheRun:
    """A crash in the drive loop used to wedge the thread forever, silently.

    `_spawn_and_drive` was a bare `asyncio.create_task` with no exception
    handler and `_drive_turn` had no try/finally, so ANY raise skipped
    `_finalize_turn`: `turn.complete` and `turn.finished` never set,
    `active_turn_id` never cleared, journal left `status: running`. There is no
    wall clock on a chat turn (CHAT_TIMEOUT_S = 0), so nothing would ever end it
    and the chat showed "Still running" with a ticking clock indefinitely. The
    traceback surfaced only as a GC-time "Task exception was never retrieved".
    """

    @staticmethod
    def _meta(tid: str, turn_id: str) -> dict:
        return {"turn_id": turn_id, "thread_id": tid, "agent_slug": "a",
                "cwd": "", "model": "m", "prompt": "hi", "timeout_s": 0,
                "pid": None, "status": "running"}

    def test_a_raise_finalizes_the_run_instead_of_wedging_it(self, thread):
        tid = thread["id"]
        turn_id = "run-crash"
        chat_store.begin_turn(tid, turn_id)
        turn = _live(turn_id, tid)

        async def boom():
            raise TypeError("unexpected payload shape")

        async def go():
            task = build_streaming._drive_task(turn, self._meta(tid, turn_id), boom())
            await asyncio.wait_for(turn.finished.wait(), timeout=2)
            await task
        asyncio.run(go())

        assert turn.complete is True
        assert turn.finished.is_set()
        # The thread is free: the next message opens a run instead of joining a
        # dead one, which is what "only a restart recovers it" meant.
        assert chat_store.active_turn_id(tid) is None

    def test_the_operator_is_told_what_happened(self, thread):
        tid = thread["id"]
        turn_id = "run-crash-2"
        chat_store.begin_turn(tid, turn_id)
        turn = _live(turn_id, tid)

        async def boom():
            raise OSError("journal went away")

        asyncio.run(_run_guarded(turn, self._meta(tid, turn_id), boom()))
        reply = chat_store.get_thread(tid)["messages"][-1]
        assert reply["role"] == "assistant"
        assert "OSError" in reply["content"] or "OSError" in (turn.error or "")
        assert reply["meta"].get("error") is True

    def test_the_reply_keeps_whatever_the_turn_had_produced(self, thread):
        tid = thread["id"]
        turn_id = "run-crash-3"
        chat_store.begin_turn(tid, turn_id)
        turn = _live(turn_id, tid)
        turn.segments = [{"t": "text", "text": "Here is half an answer."}]

        async def boom():
            raise RuntimeError("mid-stream")

        asyncio.run(_run_guarded(turn, self._meta(tid, turn_id), boom()))
        reply = chat_store.get_thread(tid)["messages"][-1]
        assert "Here is half an answer." in reply["content"]
        assert "ended early" in reply["content"]

    def test_a_cancellation_ends_the_run_and_is_not_swallowed(self, thread):
        tid = thread["id"]
        turn_id = "run-cancel"
        chat_store.begin_turn(tid, turn_id)
        turn = _live(turn_id, tid)

        async def forever():
            await asyncio.sleep(60)

        async def go():
            task = build_streaming._drive_task(turn, self._meta(tid, turn_id), forever())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        asyncio.run(go())
        assert turn.finished.is_set()
        assert chat_store.active_turn_id(tid) is None

    def test_a_clean_run_is_finalized_exactly_once(self, thread):
        """The guard must not double-commit a turn that ended normally."""
        tid = thread["id"]
        turn_id = "run-clean"
        chat_store.begin_turn(tid, turn_id)
        turn = _live(turn_id, tid)
        meta = self._meta(tid, turn_id)

        async def body():
            turn.final_text = "all done"
            await build_streaming._finalize_turn(
                turn, meta, rc=0, timed_out=False, compacted=False)

        asyncio.run(_run_guarded(turn, meta, body()))
        replies = [m for m in chat_store.get_thread(tid)["messages"]
                   if m["role"] == "assistant"]
        assert [m["content"] for m in replies] == ["all done"]


class TestAPromptThatCannotBeDeliveredEndsTheRun:
    """It used to be a log line and nothing else: the CLI was spawned, idled on
    an open stdin with no prompt, produced nothing, emitted no result line, and
    stayed alive - so the drive loop never broke and no wall clock existed to
    end it. Same visible symptom as a crashed drive loop."""

    def test_the_run_ends_rather_than_idling_forever(self, thread, monkeypatch):
        tid = thread["id"]
        turn_id = "run-nowrite"
        chat_store.begin_turn(tid, turn_id)
        turn = _live(turn_id, tid)
        turn.pid = None

        meta = {"turn_id": turn_id, "thread_id": tid, "agent_slug": "a", "cwd": "",
                "model": "m", "prompt": "hi", "timeout_s": 0, "pid": None,
                "status": "running", "session_id": None, "resume": False}

        # The exact branch: the process exists, the write does not land.
        monkeypatch.setattr(build_streaming, "write_to_turn", lambda *a, **k: False)
        drove = []
        monkeypatch.setattr(build_streaming, "_drive_turn",
                            lambda *a, **k: drove.append(1))

        class FakeProc:
            pid = 4242
            returncode = 0

        async def fake_spawn(*a, **k):
            return FakeProc()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
        monkeypatch.setattr(build_streaming.llm_router, "_claude_env", lambda: {})

        async def noop_retire(*a, **k):
            return None
        monkeypatch.setattr(build_streaming, "_retire_turn_proc", noop_retire)

        asyncio.run(build_streaming._spawn_and_drive(turn, meta))

        assert not drove, "the drive loop must not be entered for a prompt that never landed"
        assert turn.finished.is_set()
        assert chat_store.active_turn_id(tid) is None
        reply = chat_store.get_thread(tid)["messages"][-1]
        assert reply["meta"].get("error") is True
        assert "input channel" in reply["content"]


class TestTheTurnRegistryIsBounded:
    """`TURNS` never shrank: every Turn kept its full event list and segment
    tree for the life of the process."""

    def test_a_turn_that_finished_long_ago_is_dropped(self, thread):
        import time as _t
        old = _live("old", thread["id"])
        old.complete = True
        old.finished_at = _t.time() - build_streaming.FINISHED_TURN_TTL_S - 1
        assert build_streaming._prune_turns() == 1
        assert "old" not in build_streaming.TURNS

    def test_a_running_turn_is_never_dropped(self, thread):
        live = _live("live", thread["id"])
        assert live.finished_at is None
        build_streaming._prune_turns()
        assert "live" in build_streaming.TURNS

    def test_a_turn_that_just_finished_is_still_reachable(self, thread):
        import time as _t
        recent = _live("recent", thread["id"])
        recent.complete = True
        recent.finished_at = _t.time()
        build_streaming._prune_turns()
        assert "recent" in build_streaming.TURNS, "a client may still be reading it"

    def test_starting_a_turn_sweeps_the_registry(self, thread):
        import time as _t
        old = _live("old2", thread["id"])
        old.complete = True
        old.finished_at = _t.time() - build_streaming.FINISHED_TURN_TTL_S - 1
        build_streaming._make_turn(thread["id"], "a", turn_id="fresh")
        assert "old2" not in build_streaming.TURNS
        assert "fresh" in build_streaming.TURNS

    def test_finalizing_stamps_the_time(self, thread):
        tid = thread["id"]
        chat_store.begin_turn(tid, "stamped")
        turn = _live("stamped", tid)
        turn.final_text = "done"
        asyncio.run(build_streaming._finalize_turn(
            turn, {"turn_id": "stamped", "thread_id": tid, "prompt": "x", "cwd": ""},
            rc=0, timed_out=False, compacted=False))
        assert turn.finished_at is not None


class TestTheSyncEndpointDoesNotReparseTheLog:
    """It read and parsed the WHOLE log twice per call - once inside
    `read_thread` to build a tail it discarded, once for the delta it wanted -
    every 10s, per open tab, for the life of the conversation."""

    def test_a_poll_with_nothing_new_does_not_read_the_log(self, thread, monkeypatch):
        import routes.chat as rc
        tid = thread["id"]
        chat_store.append_message(tid, "user", "one")
        head = asyncio.run(rc.thread_events(tid, after=0))

        reads = []
        real = chat_log.read_events
        monkeypatch.setattr(chat_log, "read_events",
                            lambda *a, **k: (reads.append(1), real(*a, **k))[1])
        got = asyncio.run(rc.thread_events(tid, after=head["last_seq"]))
        assert got["events"] == []
        assert got["last_seq"] == head["last_seq"]
        assert reads == [], "a caller that is already current needs no log read"

    def test_a_poll_with_something_new_reads_the_log_once(self, thread, monkeypatch):
        import routes.chat as rc
        tid = thread["id"]
        chat_store.append_message(tid, "user", "one")
        chat_store.append_message(tid, "user", "two")

        reads = []
        real = chat_log.read_events
        monkeypatch.setattr(chat_log, "read_events",
                            lambda *a, **k: (reads.append(1), real(*a, **k))[1])
        got = asyncio.run(rc.thread_events(tid, after=1))
        assert [e["content"] for e in got["events"]] == ["two"]
        assert len(reads) == 1

    def test_the_live_run_still_comes_back_without_a_log_read(self, thread, monkeypatch):
        import routes.chat as rc
        tid = thread["id"]
        chat_store.append_message(tid, "user", "one")
        chat_store.begin_turn(tid, "r9")
        head = asyncio.run(rc.thread_events(tid, after=0))
        assert head["active_turn"]["turn_id"] == "r9"
        started = head["active_turn"]["started_seq"]
        assert started is not None

        monkeypatch.setattr(chat_log, "read_events",
                            lambda *a, **k: pytest.fail("no log read is needed here"))
        got = asyncio.run(rc.thread_events(tid, after=head["last_seq"]))
        assert got["active_turn"]["turn_id"] == "r9"
        assert got["active_turn"]["started_seq"] == started

    def test_a_run_opened_before_the_seq_was_recorded_still_resolves(self, thread):
        """Threads written by the older build carry no `active_turn_seq`; the
        scan is still there for them, so nothing needs migrating."""
        tid = thread["id"]
        chat_store.begin_turn(tid, "legacy")
        chat_log.patch_meta_sync(tid, active_turn_seq=None)
        run = chat_log.active_turn(tid)
        assert run["turn_id"] == "legacy"
        assert run["started_seq"] is not None


class TestTheRunMarkerIsFenced:
    """R2: mark_turn_started runs after the claim lock is released, so a stop
    that finalized this very run can interleave before it. The marker write
    must lose that race, not resurrect `active_turn_seq` for a dead run."""

    def test_a_marker_for_a_run_no_longer_owning_the_thread_is_dropped(self, thread):
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-1", announce=False)
        # The run ends (a stop, a crash finalize) before the marker lands.
        chat_store.commit_turn(tid, "run-1", [], "done")
        before = len(chat_log.read_events(tid))
        chat_store.mark_turn_started(tid, "run-1")
        assert len(chat_log.read_events(tid)) == before
        meta = chat_log.read_meta(tid)
        assert not meta.get("active_turn_seq")

    def test_the_owning_run_still_writes_its_marker(self, thread):
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-1", announce=False)
        chat_store.mark_turn_started(tid, "run-1")
        kinds = [(e.get("kind"), e.get("state")) for e in chat_log.read_events(tid)]
        assert ("turn", "started") in kinds


class TestThePickersSurviveAJoin:
    """Model, effort and permission mode all commit on change and apply to the
    NEXT turn. A message sent while a turn is streaming JOINS that turn, and the
    turn's argv is fixed at spawn, so none of the three can reach it. What must
    still happen is that the pick is RECORDED, or the picker and the thread
    disagree the moment the run ends.

    Model was the odd one out and the only one that lost data: it was persisted
    only by `_turn_for_message`, which runs only when a message starts a fresh
    turn. Picked mid-turn it was dropped outright, so the composer silently
    snapped back to the old model on the next load.
    """

    def test_a_model_picked_mid_turn_is_recorded_on_the_thread(self, thread, monkeypatch):
        import routes.chat as rc
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-1")
        _live("run-1", tid)
        monkeypatch.setattr(build_streaming, "inject", lambda *a, **k: True)

        async def never(**kw):
            raise AssertionError("this message must join, not start a rival run")
        monkeypatch.setattr(rc, "_start_assistant_turn", never)

        picked = "claude-opus-5"
        out = asyncio.run(rc._send(tid, "switch while you work", model_in=picked))

        # It joined the running turn ...
        assert out["turn_id"] == "run-1"
        assert out["injected"] is True
        # ... and the pick still survived to the next turn.
        assert chat_store.get_thread(tid)["model"] == picked

    def test_effort_and_mode_picked_mid_turn_are_recorded_too(self, thread, monkeypatch):
        """The two that already worked, pinned beside it so the three cannot
        drift back apart.

        The model is picked in the same send on purpose: effort resolves against
        the model the operator is actually looking at, and the default chat model
        has no effort ladder at all, so `low` against it correctly resolves to
        None rather than riding along invisibly."""
        import routes.chat as rc
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-1")
        _live("run-1", tid)
        monkeypatch.setattr(build_streaming, "inject", lambda *a, **k: True)

        asyncio.run(rc._send(
            tid, "and these", model_in="claude-opus-5", effort_in="low", mode_in="plan"))

        t = chat_store.get_thread(tid)
        assert t["effort"] == "low"
        assert t["permission_mode"] == "plan"

    def test_an_effort_the_picked_model_cannot_do_is_not_recorded(self, thread, monkeypatch):
        """The other half of the same rule. Haiku has no effort ladder, so a
        level sent against it drops to the model's own default instead of being
        stored as a level the picker could never show."""
        import routes.chat as rc
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-1")
        _live("run-1", tid)
        monkeypatch.setattr(build_streaming, "inject", lambda *a, **k: True)

        asyncio.run(rc._send(
            tid, "no ladder here",
            model_in="claude-haiku-4-5-20251001", effort_in="low"))

        assert chat_store.get_thread(tid)["effort"] is None


class TestTheContractReachesAnOpenSession:
    """A claude session is told its protocols ONCE, at spawn: the role preamble
    and the persona go out on the first turn and every later turn resumes that
    session with only the volatile part. So a contract inside them (the ui-card
    format, the task-proposal field list) could change under a running chat and
    never reach it.

    Measured 2026-08-16: the task-proposal card was cut from eight fields to
    three at 01:45Z and the backend restarted onto it at 02:58Z; a chat spawned
    at 22:03Z the evening before was still emitting eight-field cards hours
    later. Disk held one correct copy the whole time. The running sessions were
    the stale thing and nothing could see it.
    """

    def _provider(self):
        from services import chat_context
        return chat_context.get_provider("general")

    def test_an_unchanged_contract_says_nothing(self, thread):
        import routes.chat as rc
        from services import chat_context
        tid = thread["id"]
        provider = self._provider()
        digest = chat_context.contract_digest(
            chat_context.instruction_contract(provider, enable_chat_tools=rc.ENABLE_CHAT_TOOLS))
        chat_store.set_turn_marks(tid, contract_hash=digest)

        t = chat_store.get_thread(tid)
        out = asyncio.run(rc._resumed_prompt(t, provider, "hello"))
        assert "=== PROTOCOL UPDATED ===" not in out

    def test_a_changed_contract_is_restated_as_a_replacement(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        # What a session spawned before the change holds: some other digest.
        chat_store.set_turn_marks(tid, contract_hash="0000stale0000")

        t = chat_store.get_thread(tid)
        out = asyncio.run(rc._resumed_prompt(t, self._provider(), "hello"))
        assert "=== PROTOCOL UPDATED ===" in out
        # It must supersede rather than merely append: the retired copy is still
        # in the session's history and cannot be removed.
        assert "REPLACES" in out
        # And it carries the contract that actually moved.
        assert "TASK PROPOSAL CARD" in out

    def test_the_refresh_happens_once_not_every_turn(self, thread):
        """The mark is recorded when the block goes out, so the next turn is
        silent again. Without this the contract would be re-sent on every turn
        of every chat forever, which is the cost this design exists to avoid."""
        import routes.chat as rc
        tid = thread["id"]
        chat_store.set_turn_marks(tid, contract_hash="0000stale0000")
        provider = self._provider()

        first = asyncio.run(rc._resumed_prompt(chat_store.get_thread(tid), provider, "one"))
        second = asyncio.run(rc._resumed_prompt(chat_store.get_thread(tid), provider, "two"))
        assert "=== PROTOCOL UPDATED ===" in first
        assert "=== PROTOCOL UPDATED ===" not in second

    def test_a_session_that_never_recorded_one_is_healed(self, thread):
        """A chat open from before this mechanism existed has no mark at all,
        and is stale by definition. It gets the block once."""
        import routes.chat as rc
        tid = thread["id"]
        assert "contract_hash" not in chat_store.get_turn_marks(chat_store.get_thread(tid))
        out = asyncio.run(rc._resumed_prompt(chat_store.get_thread(tid), self._provider(), "hi"))
        assert "=== PROTOCOL UPDATED ===" in out

    def test_the_fresh_path_records_what_it_sent(self, thread):
        """The first turn carries the contract in full, so the very next turn
        must not re-state it. That only works if the fresh assembly records the
        digest it just sent."""
        import routes.chat as rc
        tid = thread["id"]
        t = chat_store.get_thread(tid)
        asyncio.run(rc._assemble_full_prompt(t, self._provider(), "first message"))

        marks = chat_store.get_turn_marks(chat_store.get_thread(tid))
        assert marks.get("contract_hash")
        out = asyncio.run(rc._resumed_prompt(chat_store.get_thread(tid), self._provider(), "second"))
        assert "=== PROTOCOL UPDATED ===" not in out
