"""A turn a restart took is resumed once, automatically, and never otherwise.

The agent child survives a backend restart but not a host reboot. Until this,
a lost turn waited for the operator to read the notice and type "continue".
These pin the resume and every guard around it: once per turn, never for a
turn the operator stopped or one that failed on its own, never for an old one,
and never for a resume that was itself lost. They also pin that a stream closed
by a shutdown does not tell the client the turn ended.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from services import (build_streaming, chat_drain, chat_log, chat_queue, chat_store,
                      ownership)


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    monkeypatch.setattr(build_streaming, "TURNS_DIR", tmp_path / "turns")
    monkeypatch.setattr(chat_drain, "_locks", {})
    ownership.set_current_owner(1)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()
        build_streaming.TURNS.clear()


@pytest.fixture()
def starter(monkeypatch):
    """Stands in for the send path: records each batch and claims the thread
    the way a real turn would, so the drain can see which turn it started."""
    calls: list[list[dict]] = []

    async def fake(thread_id: str, batch: list[dict]) -> bool:
        calls.append(batch)
        chat_store.begin_turn(thread_id, f"run-resume-{len(calls)}")
        return True

    monkeypatch.setattr(chat_drain, "_starter", fake)
    return calls


def _lost_messages(tid: str) -> list[str]:
    return [m["content"] for m in chat_store.get_thread(tid)["messages"]
            if m["content"] in (chat_store.LOST_TURN_TEXT,
                                chat_store.LOST_TURN_RESUMING_TEXT)]


def _age_turn(tid: str, turn_id: str, seconds: float) -> None:
    live = dict(chat_log.read_meta(tid)["live_message"])
    live["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                               time.gmtime(time.time() - seconds))
    chat_log.patch_meta_sync(tid, live_message=live)


class TestTheStartupReconcileResumesOnce:
    def test_a_restart_lost_turn_is_resumed_once(self, thread):
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-gone")

        build_streaming.reconcile_threads()

        seed = chat_queue.read_continuation(tid)
        assert seed is not None
        assert seed["client_id"] == "auto-resume-run-gone"
        assert seed["text"] == chat_drain.AUTO_RESUME_SEED
        assert _lost_messages(tid) == [chat_store.LOST_TURN_RESUMING_TEXT]
        assert chat_log.read_meta(tid)["auto_resume"]["lost_turn"] == "run-gone"

    def test_the_marker_refuses_a_second_resume_of_the_same_turn(self, thread):
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-gone")
        build_streaming.reconcile_threads()
        chat_queue.clear_continuation(tid)
        assert chat_drain.may_auto_resume(tid, "run-gone", time.time()) is False

    def test_the_marker_is_not_on_the_thread_document(self, thread):
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-gone")
        build_streaming.reconcile_threads()
        assert "auto_resume" not in chat_store.get_thread(tid)

    def test_a_stopped_turn_is_not_resumed(self, thread):
        """Stop holds the queue on the thread, and that hold is what survives
        the restart to say the operator ended it."""
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-stopped")
        chat_queue.set_held(tid, True)

        build_streaming.reconcile_threads()

        assert chat_queue.read_continuation(tid) is None
        assert _lost_messages(tid) == [chat_store.LOST_TURN_TEXT]

    def test_a_turn_older_than_the_window_is_not_resumed(self, thread):
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-old")
        _age_turn(tid, "run-old", chat_drain.AUTO_RESUME_MAX_AGE_S + 60)

        build_streaming.reconcile_threads()

        assert chat_queue.read_continuation(tid) is None
        assert _lost_messages(tid) == [chat_store.LOST_TURN_TEXT]

    def test_a_turn_inside_the_window_is_resumed(self, thread):
        """The positive control for the age guard."""
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-recent")
        _age_turn(tid, "run-recent", chat_drain.AUTO_RESUME_MAX_AGE_S - 60)
        build_streaming.reconcile_threads()
        assert chat_queue.read_continuation(tid) is not None

    def test_queued_operator_messages_take_precedence(self, thread):
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-gone")
        chat_queue.enqueue(tid, text="do this next instead", client_id="c1")

        build_streaming.reconcile_threads()

        assert chat_queue.read_continuation(tid) is None
        assert _lost_messages(tid) == [chat_store.LOST_TURN_TEXT]

    def test_another_pending_continuation_is_not_overwritten(self, thread):
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-gone")
        chat_queue.write_continuation(tid, text="a run finished", client_id="auto-job-1")

        build_streaming.reconcile_threads()

        assert chat_queue.read_continuation(tid)["client_id"] == "auto-job-1"

    def test_a_lost_resume_is_not_resumed_again(self, thread, starter):
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-gone")
        build_streaming.reconcile_threads()

        assert asyncio.run(chat_drain.drain_continuation(tid, reason="resume-check")) is True
        marker = chat_log.read_meta(tid)["auto_resume"]
        assert (marker["lost_turn"], marker["resume_turn"]) == ("run-gone", "run-resume-1")

        # The host reboots again, taking the resume with it.
        build_streaming.reconcile_threads()

        assert chat_queue.read_continuation(tid) is None
        assert _lost_messages(tid) == [chat_store.LOST_TURN_RESUMING_TEXT,
                                       chat_store.LOST_TURN_TEXT]

    def test_an_operator_turn_after_a_resume_can_be_resumed(self, thread, starter):
        """The once-only rule is per turn, not per thread."""
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-gone")
        build_streaming.reconcile_threads()
        asyncio.run(chat_drain.drain_continuation(tid, reason="resume-check"))
        chat_store.commit_turn(tid, "run-resume-1", [], "done")

        chat_store.begin_turn(tid, "run-next")
        build_streaming.reconcile_threads()

        assert chat_queue.read_continuation(tid)["client_id"] == "auto-resume-run-next"


class TestTheReattachPathResumesOnlyARestartLoss:
    """The drive loop's ending for an adopted turn whose process is gone."""

    @pytest.fixture()
    def commit(self, thread, monkeypatch):
        from services import usage_ledger

        monkeypatch.setattr(usage_ledger, "record_turn", lambda *a, **k: None)
        monkeypatch.setattr(build_streaming.build_agents, "touch", lambda *a, **k: None)
        monkeypatch.setattr(build_streaming.build_agents, "mark_idle", lambda *a, **k: None)
        starts: list = []

        async def refuse(thread_id, batch):
            starts.append(batch)
            return False

        monkeypatch.setattr(chat_drain, "_starter", refuse)
        tid = thread["id"]

        def run(turn_id="run-adopted", *, started=None, error=None, stopped=False,
                timed_out=False, prompt="build it"):
            chat_store.begin_turn(tid, turn_id)
            turn = build_streaming.Turn(id=turn_id, thread_id=tid, agent_slug="general")
            turn.started = time.time() if started is None else started
            turn.error = error
            asyncio.run(build_streaming._commit_turn(
                turn, {"prompt": prompt, "cwd": "", "timeout_s": 0},
                rc=-1, timed_out=timed_out, compacted=False,
                lost=not stopped, stopped=stopped))
            return tid

        return run

    def test_a_restart_loss_is_resumed(self, commit):
        tid = commit()
        assert chat_queue.read_continuation(tid)["client_id"] == "auto-resume-run-adopted"
        assert _lost_messages(tid) == [chat_store.LOST_TURN_RESUMING_TEXT]

    def test_an_errored_turn_is_not_resumed(self, commit):
        tid = commit(error="authentication failed")
        assert chat_queue.read_continuation(tid) is None

    def test_a_stopped_turn_is_not_resumed(self, commit):
        tid = commit(stopped=True)
        assert chat_queue.read_continuation(tid) is None

    def test_a_timed_out_turn_is_not_resumed(self, commit):
        tid = commit(timed_out=True)
        assert chat_queue.read_continuation(tid) is None

    def test_a_command_turn_is_not_resumed(self, commit):
        tid = commit(prompt="/compact")
        assert chat_queue.read_continuation(tid) is None

    def test_an_old_turn_is_not_resumed(self, commit):
        tid = commit(started=time.time() - chat_drain.AUTO_RESUME_MAX_AGE_S - 60)
        assert chat_queue.read_continuation(tid) is None
        assert _lost_messages(tid) == [chat_store.LOST_TURN_TEXT]


class TestTheStreamEndsOnlyWhenTheTurnDoes:
    @pytest.fixture(autouse=True)
    def _restore_flag(self):
        before = build_streaming.SHUTTING_DOWN
        yield
        build_streaming.SHUTTING_DOWN = before

    @staticmethod
    def _frames(turn) -> list[str]:
        from routes import chat as chat_routes

        async def never():
            return False

        async def collect():
            return [f async for f in chat_routes._turn_sse(turn, 0, never)]

        return asyncio.run(asyncio.wait_for(collect(), timeout=3.0))

    def test_a_shutdown_closes_without_end(self):
        """`end` would tell the client the turn is over. It is not: the child
        is still running and the next process adopts it."""
        turn = build_streaming.Turn(id="t-down", thread_id="th-1", agent_slug="general")
        turn.events.append(build_streaming.TurnEvent(kind="text", payload={"text": "partial"}))
        build_streaming.SHUTTING_DOWN = True

        frames = self._frames(turn)

        assert len(frames) == 1 and "event: text" in frames[0]
        assert not any("event: end" in f for f in frames)

    def test_a_finished_turn_still_ends_with_end(self):
        turn = build_streaming.Turn(id="t-done", thread_id="th-1", agent_slug="general")
        turn.events.append(build_streaming.TurnEvent(kind="text", payload={"text": "all"}))
        turn.complete = True
        build_streaming.SHUTTING_DOWN = True

        frames = self._frames(turn)

        assert frames[-1] == "event: end\ndata: {}\n\n"
