"""A run is not a message: the lifecycle, the sync endpoint, and the races.

Pass B of docs/chat-rebuild-spec-2026-08-09.md. The run leaves the log — there
is no assistant placeholder while a turn is in flight, only `meta.active_run_id`
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
    ownership.set_current_owner(1)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()
        build_streaming.TURNS.clear()


def _live(run_id: str, thread_id: str, *, spawned: bool = True) -> build_streaming.Turn:
    t = build_streaming.Turn(id=run_id, thread_id=thread_id, agent_slug="a")
    t.spawned = spawned
    build_streaming.TURNS[run_id] = t
    return t


class TestTheEventsEndpoint:
    """`GET /threads/{id}/events?after=<seq>` is THE sync endpoint. It has to be
    enough on its own: a transcript assembled by merging several endpoints with
    local state is the anti-pattern behind every reorder bug in this system."""

    def test_cursor_zero_returns_the_whole_transcript(self, thread):
        import routes.chat as rc
        tid = thread["id"]
        chat_store.append_message(tid, "user", "one")
        chat_store.begin_run(tid, "run-1")
        chat_store.commit_run(tid, "run-1", [], "two")
        chat_store.append_message(tid, "user", "three")

        got = asyncio.run(rc.thread_events(tid, after=0))
        kinds = [(e["kind"], e.get("role") or e.get("state")) for e in got["events"]]
        assert kinds == [
            ("message", "user"),
            ("run", "started"),
            ("message", "assistant"),
            ("run", "finished"),
            ("message", "user"),
        ]
        assert [e["seq"] for e in got["events"]] == [1, 2, 3, 4, 5]
        assert got["last_seq"] == 5
        assert got["active_run"] is None

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
        chat_store.begin_run(tid, "run-1", model="m")
        chat_store.update_run(tid, "run-1", [{"t": "text", "text": "half"}], "half")

        got = asyncio.run(rc.thread_events(tid, after=0))
        run = got["active_run"]
        assert run["run_id"] == "run-1"
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
        chat_store.begin_run(tid, "run-1")
        _live("run-1", tid, spawned=True)   # spawned, and no FIFO: it is ending

        monkeypatch.setattr(build_streaming, "inject", lambda *a: False)
        started = []

        async def fake_start(*, thread_id, t, provider, content, model, run_id, **kw):
            started.append(run_id)
            turn = build_streaming.TURNS[run_id]
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
        chat_store.begin_run(tid, "run-1")
        _live("run-1", tid, spawned=False)
        monkeypatch.setattr(build_streaming, "inject", lambda *a: False)

        async def never(**kw):
            raise AssertionError("must not start a rival run")
        monkeypatch.setattr(rc, "_start_assistant_turn", never)

        out = asyncio.run(rc._send(tid, "hold on"))
        assert out == {"turn_id": "run-1", "injected": False, "at_segment": 0}
        assert chat_store.active_run_id(tid) == "run-1"
        bodies = [m["content"] for m in chat_store.get_thread(tid)["messages"]]
        assert bodies.count("hold on") == 1

    def test_the_message_is_written_before_the_channel(self, thread, monkeypatch):
        """The ordering itself, observed: by the time anything is handed to the
        run, the transcript already has it."""
        import routes.chat as rc
        tid = thread["id"]
        chat_store.begin_run(tid, "run-1")
        _live("run-1", tid)
        seen: list[list[str]] = []

        def spy(run_id, text):
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

        async def fake_start(*, thread_id, t, provider, content, model, run_id, **kw):
            started.append(run_id)
            turn = build_streaming.TURNS[run_id]
            turn.spawned = True
            await asyncio.sleep(0)      # a real spawn awaits
            return turn
        monkeypatch.setattr(rc, "_start_assistant_turn", fake_start)
        monkeypatch.setattr(build_streaming, "inject",
                            lambda rid, text: delivered.append(text) or True)

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
        assert chat_store.active_run_id(tid) is None


class TestStartupReconcile:
    """A run that did not survive a restart leaves `active_run_id` pointing at a
    journal that is dead or gone. One pass at startup closes it out; there is no
    per-read self-heal any more because there is no flag to orphan."""

    def test_a_crashed_run_becomes_a_lost_run_and_a_message(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "build the thing")
        chat_store.begin_run(tid, "run-gone")
        chat_store.update_run(tid, "run-gone", [{"t": "text", "text": "partial"}], "partial")

        build_streaming.reconcile_threads()

        assert chat_store.active_run_id(tid) is None
        events = chat_log.read_events(tid)
        assert [e["state"] for e in events if e["kind"] == "run"] == ["started", "lost"]
        last = chat_store.get_thread(tid)["messages"][-1]
        assert last["role"] == "assistant"
        assert last["content"] == chat_store.LOST_RUN_TEXT
        assert "restarted mid-run" in last["content"]

    def test_a_run_this_process_is_driving_is_left_alone(self, thread):
        tid = thread["id"]
        chat_store.begin_run(tid, "run-live")
        _live("run-live", tid)
        build_streaming.reconcile_threads()
        assert chat_store.active_run_id(tid) == "run-live"
        assert not [m for m in chat_store.get_thread(tid)["messages"]
                    if m["content"] == chat_store.LOST_RUN_TEXT]

    def test_an_idle_thread_is_untouched(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "hi")
        before = chat_log.read_events(tid)
        build_streaming.reconcile_threads()
        assert chat_log.read_events(tid) == before

    def test_it_runs_twice_without_saying_it_twice(self, thread):
        tid = thread["id"]
        chat_store.begin_run(tid, "run-gone")
        build_streaming.reconcile_threads()
        build_streaming.reconcile_threads()
        lost = [m for m in chat_store.get_thread(tid)["messages"]
                if m["content"] == chat_store.LOST_RUN_TEXT]
        assert len(lost) == 1


class TestConcurrentSendAndCompact:
    """/compact reads the thread, spends ~30s summarizing, and records the fold.
    Anything the operator typed during that window used to be erased by the
    write-back. There is no whole-document write left to erase it with."""

    def test_a_message_sent_during_the_fold_survives_it(self, thread, monkeypatch):
        import time
        import routes.chat as rc
        tid = thread["id"]
        for i in range(8):
            chat_store.append_message(tid, "user", f"m{i}")

        def slow_summary(prev, to_fold):
            time.sleep(0.3)
            return "the folded summary"
        monkeypatch.setattr(rc, "_summarize_sync", slow_summary)
        monkeypatch.setattr(rc, "_schedule_distill", lambda *a, **kw: None)

        async def main():
            snapshot = chat_store.get_thread(tid)
            folding = asyncio.create_task(
                rc._compact_thread(snapshot, "workshop", force=True))
            await asyncio.sleep(0.05)
            chat_store.append_message(tid, "user", "typed while it folded")
            return await folding

        asyncio.run(main())
        t = chat_store.get_thread(tid)
        bodies = [m["content"] for m in t["messages"]]
        assert bodies.count("typed while it folded") == 1
        assert bodies[-1] == "typed while it folded"
        # And it is on the LIVE side of the cut: the fold summarized what it read,
        # which did not include this. The boundary is a seq, so a message that
        # landed after the read cannot fall behind it however the list grew.
        assert 0 < t["compacted_through"] < len(bodies)
        assert bodies[t["compacted_through"]:][-1] == "typed while it folded"
        assert t["summary"] == "the folded summary"

    def test_a_run_committing_during_the_fold_survives_it(self, thread, monkeypatch):
        import time
        import routes.chat as rc
        tid = thread["id"]
        for i in range(8):
            chat_store.append_message(tid, "user", f"m{i}")
        chat_store.begin_run(tid, "run-1")

        def slow_summary(prev, to_fold):
            time.sleep(0.3)
            return "summary"
        monkeypatch.setattr(rc, "_summarize_sync", slow_summary)
        monkeypatch.setattr(rc, "_schedule_distill", lambda *a, **kw: None)

        async def main():
            snapshot = chat_store.get_thread(tid)
            folding = asyncio.create_task(
                rc._compact_thread(snapshot, "workshop", force=True))
            await asyncio.sleep(0.05)
            chat_store.commit_run(tid, "run-1", [], "the reply")
            return await folding

        asyncio.run(main())
        bodies = [m["content"] for m in chat_store.get_thread(tid)["messages"]]
        assert bodies.count("the reply") == 1
        assert chat_store.active_run_id(tid) is None


class TestArrivalNoticeFollowsTheMark:
    """Which messages waited is a LOOKUP on `meta.during_run`, not a guess from
    where they sit. Reading the tail after the last assistant message stopped
    working the moment the reply started being appended when it FINISHED."""

    def test_it_names_exactly_the_messages_that_waited(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "start")
        chat_store.begin_run(tid, "run-1")
        chat_store.append_message_during_turn(
            tid, "user", "also this", ts="2026-08-09T05:41:00+00:00",
            meta={"during_run": "run-1"})
        chat_store.append_message_during_turn(
            tid, "user", "and this", ts="2026-08-09T05:42:00+00:00",
            meta={"during_run": "run-1"})
        chat_store.commit_run(tid, "run-1", [], "answered")
        chat_store.append_message(tid, "user", "next turn")

        msgs = chat_store.get_thread(tid)["messages"]
        out = chat_context.arrival_notice(msgs, run_id=chat_store.previous_run_id(tid))
        assert "2 of the messages" in out
        assert "05:41Z, 05:42Z" in out

    def test_the_mark_finds_them_although_they_sit_above_the_reply(self, thread):
        """The regression this rework exists for. The mid-run messages are ABOVE
        the reply now (that is the fix), so a tail scan finds nothing at all."""
        tid = thread["id"]
        chat_store.append_message(tid, "user", "start")
        chat_store.begin_run(tid, "run-1")
        chat_store.append_message_during_turn(
            tid, "user", "wait", ts="2026-08-09T05:41:00+00:00",
            meta={"during_run": "run-1"})
        chat_store.commit_run(tid, "run-1", [], "answered")
        chat_store.append_message(tid, "user", "next")
        msgs = chat_store.get_thread(tid)["messages"]

        assert chat_context.arrival_notice(msgs) == ""          # tail scan: blind
        assert "One message" in chat_context.arrival_notice(msgs, run_id="run-1")

    def test_an_older_run_is_not_re_announced(self, thread):
        """A notice that fires on every turn forever says nothing."""
        tid = thread["id"]
        chat_store.begin_run(tid, "run-1")
        chat_store.append_message_during_turn(
            tid, "user", "old", meta={"during_run": "run-1"})
        chat_store.commit_run(tid, "run-1", [], "a")
        chat_store.append_message(tid, "user", "next")
        chat_store.begin_run(tid, "run-2")
        chat_store.commit_run(tid, "run-2", [], "b")

        msgs = chat_store.get_thread(tid)["messages"]
        assert chat_store.previous_run_id(tid) == "run-2"
        assert chat_context.arrival_notice(msgs, run_id="run-2") == ""

    def test_the_resumed_prompt_carries_the_notice(self, thread, monkeypatch):
        """End to end through the path that actually builds the turn's message."""
        import routes.chat as rc
        tid = thread["id"]
        chat_store.append_message(tid, "user", "start")
        chat_store.begin_run(tid, "run-1")
        chat_store.append_message_during_turn(
            tid, "user", "wait", ts="2026-08-09T05:41:00+00:00",
            meta={"during_run": "run-1"})
        chat_store.commit_run(tid, "run-1", [], "answered")

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

        async def fake_start(*, thread_id, t, provider, content, model, run_id, **kw):
            turn = build_streaming.TURNS[run_id]
            turn.spawned = True

            async def finish():
                await asyncio.sleep(0.02)
                turn.committed = chat_store.commit_run(
                    thread_id, run_id, [], "the spoken reply")
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
