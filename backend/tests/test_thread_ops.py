"""One exclusive operation per thread, and admission that respects it.

THE INCIDENT THESE PIN, measured 2026-08-14 03:20 on thread 57e6c4b8: the
operator pressed compact; the fold spent ~22s summarizing; two messages sent
during that window started a turn on the pre-fold 467k prompt; the boundary
then landed mid-turn and the reply committed on the far side of the divider
from the messages it answered.

The rule (services/thread_ops): a thread admits one operation at a time, an
operation lands only on a quiet thread, and a message that arrives while one is
pending queues behind it on the durable queue.
"""
import asyncio

import pytest

from services import build_streaming, chat_drain, chat_queue, chat_store, ownership, thread_ops


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    monkeypatch.setattr(build_streaming, "TURNS_DIR", tmp_path / "turns")
    ownership.set_current_owner(1)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()
        build_streaming.TURNS.clear()
        thread_ops._PENDING.clear()


@pytest.fixture()
def chatty(thread):
    tid = thread["id"]
    for i in range(10):
        chat_store.append_message(tid, "user", f"OLD-{i} " + ("filler " * 30))
        chat_store.append_message(tid, "assistant", f"reply-{i} " + ("filler " * 30))
    return tid


class TestTheRegistry:
    def test_one_operation_at_a_time(self, thread):
        tid = thread["id"]
        assert thread_ops.begin(tid, "compact") is True
        assert thread_ops.pending(tid) == "compact"
        assert thread_ops.begin(tid, "clear") is False
        thread_ops.end(tid)
        assert thread_ops.pending(tid) is None
        assert thread_ops.begin(tid, "clear") is True

    def test_end_without_begin_is_harmless(self, thread):
        thread_ops.end(thread["id"])


class TestAdmissionRespectsThePendingOp:
    def test_the_drain_refuses_while_an_op_is_pending(self, thread, monkeypatch):
        tid = thread["id"]
        chat_queue.enqueue(tid, text="waiting", client_id="c1", ts="2026-08-14T00:00:00Z")

        async def never(thread_id, batch):
            raise AssertionError("must not start a turn during a pending op")
        chat_drain.register_starter(never)
        thread_ops.begin(tid, "compact")
        assert asyncio.run(chat_drain.drain(tid)) is False
        # The message is still queued: refused, not dropped.
        assert chat_queue.read(tid)

    def test_a_send_queues_instead_of_starting_a_turn(self, thread, monkeypatch):
        import routes.chat as rc
        tid = thread["id"]
        thread_ops.begin(tid, "compact")

        async def never(**kw):
            raise AssertionError("must not start a turn during a pending op")
        monkeypatch.setattr(rc, "_start_assistant_turn", never)

        out = asyncio.run(rc._send(tid, "typed mid-fold"))
        assert out["queued"] is True and out["turn_id"] is None
        entries = chat_queue.read(tid)
        assert [e["text"] for e in entries] == ["typed mid-fold"]

    def test_ending_the_op_kicks_the_drain(self, thread, monkeypatch):
        tid = thread["id"]
        kicked = []
        monkeypatch.setattr(chat_drain, "drain_soon",
                            lambda t, *, reason="": kicked.append((t, reason)))
        thread_ops.begin(tid, "compact")
        thread_ops.end(tid)
        assert kicked and kicked[0][0] == tid


class TestWaitQuiet:
    def test_returns_at_once_on_an_idle_thread(self, thread):
        asyncio.run(asyncio.wait_for(thread_ops.wait_quiet(thread["id"]), timeout=1))

    def test_waits_for_the_live_run_to_finish(self, thread):
        tid = thread["id"]
        chat_store.begin_run(tid, "run-1")
        turn = build_streaming.Turn(id="run-1", thread_id=tid, agent_slug="a")
        turn.spawned = True
        build_streaming.TURNS["run-1"] = turn

        async def scenario():
            waiter = asyncio.create_task(thread_ops.wait_quiet(tid))
            await asyncio.sleep(0.05)
            assert not waiter.done()
            # The turn ends: the store clears the run and the event fires.
            chat_store.commit_run(tid, "run-1", [], "done")
            turn.finished.set()
            await asyncio.wait_for(waiter, timeout=1)
        asyncio.run(scenario())


class TestCompactDefersMidRun:
    def test_the_fold_lands_only_when_the_thread_goes_quiet(self, chatty, monkeypatch):
        import routes.chat as rc
        tid = chatty
        monkeypatch.setattr(rc, "_summarize_sync", lambda prev, msgs: "S")
        chat_store.begin_run(tid, "run-1")
        turn = build_streaming.Turn(id="run-1", thread_id=tid, agent_slug="a")
        turn.spawned = True
        build_streaming.TURNS["run-1"] = turn

        async def scenario():
            r = await rc.compact_thread(tid)
            assert r["deferred"] is True and r["folded"] > 0
            # Computed, not landed: the summary is not on the thread and the
            # operation still holds the door.
            assert not chat_store.get_thread(tid).get("summary")
            assert thread_ops.pending(tid) == "compact"
            # A second compact while one is pending is refused, not stacked.
            r2 = await rc.compact_thread(tid)
            assert r2["deferred"] is True and r2["folded"] == 0
            # The run ends; the landing task fires.
            chat_store.commit_run(tid, "run-1", [], "done")
            turn.finished.set()
            for _ in range(50):
                if thread_ops.pending(tid) is None:
                    break
                await asyncio.sleep(0.02)
            assert thread_ops.pending(tid) is None
            assert chat_store.get_thread(tid).get("summary") == "S"
        asyncio.run(scenario())

    def test_a_quiet_thread_lands_inline(self, chatty, monkeypatch):
        import routes.chat as rc
        monkeypatch.setattr(rc, "_summarize_sync", lambda prev, msgs: "S")

        async def scenario():
            r = await rc.compact_thread(chatty)
            assert r.get("deferred") is None or r.get("deferred") is False
            assert r["folded"] > 0
            assert chat_store.get_thread(chatty).get("summary") == "S"
            assert thread_ops.pending(chatty) is None
        asyncio.run(scenario())


class TestClearDefersMidRun:
    def test_the_cut_lands_only_when_the_thread_goes_quiet(self, chatty):
        """The mid-turn /clear race: the cut used to count messages as of its
        own read, so a message appended between the read and the boundary fell
        above the cut and silently left the next prompt. Deferring the landing
        to a quiet thread makes the count include everything the turn added."""
        import routes.chat as rc
        tid = chatty
        chat_store.begin_run(tid, "run-1")
        turn = build_streaming.Turn(id="run-1", thread_id=tid, agent_slug="a")
        turn.spawned = True
        build_streaming.TURNS["run-1"] = turn

        async def scenario():
            r = await rc.clear_thread_context(tid)
            assert r["deferred"] is True
            assert not chat_store.get_thread(tid).get("cleared_through")
            # The turn appends more conversation, then ends.
            chat_store.append_message(tid, "user", "LATE-ARRIVAL")
            chat_store.commit_run(tid, "run-1", [], "the reply")
            turn.finished.set()
            for _ in range(50):
                if thread_ops.pending(tid) is None:
                    break
                await asyncio.sleep(0.02)
            t = chat_store.get_thread(tid)
            # The cut includes the late messages: nothing survives above it.
            assert t.get("cleared_through") == len(t.get("messages") or [])
        asyncio.run(scenario())
