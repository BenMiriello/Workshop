"""A backend restart must PAUSE work, never end or duplicate it.

These cover the three ways a restart used to be destructive rather than merely
slow. Each maps to a specific defect found on 2026-08-13:

1. Shutdown killed the agent process it had gone to lengths to detach. The child
   is spawned with start_new_session=True and the unit sets KillMode=process so
   systemd's SIGTERM cannot reach it -- but asyncio cancels the drive loop on the
   way down, and the CancelledError handler ran _end_wedged, which SIGTERMs the
   child's process group and commits "This turn ended early" into the transcript.

2. A restart inside the commit window duplicated the reply. commit_run appends
   the reply and clears active_run_id, but the journal is marked done only
   afterwards, with a seconds-long git checkpoint in between. reattach checked
   only the journal status, so it re-drove a turn whose reply was already on the
   thread: a second assistant message, and every note/ticket/learning block in
   that turn applied twice.

3. An open browser tab pinned the shutdown. The chat SSE generators end only
   when their turn does, and uvicorn's graceful shutdown waits forever by
   default, so systemd SIGKILLed the backend at TimeoutStopSec (90s). A
   three-second restart became a minute and a half of dead backend.
"""
from __future__ import annotations

import asyncio

import pytest

from services import build_streaming as bs


@pytest.fixture(autouse=True)
def _restore_flag():
    """Never leak the shutdown flag into another test: it is module state, and a
    stuck True would make every later turn release itself immediately."""
    before = bs.SHUTTING_DOWN
    yield
    bs.SHUTTING_DOWN = before


def test_begin_shutdown_is_idempotent():
    bs.SHUTTING_DOWN = False
    bs.begin_shutdown()
    assert bs.SHUTTING_DOWN is True
    bs.begin_shutdown()
    assert bs.SHUTTING_DOWN is True


def test_event_stream_releases_on_shutdown_instead_of_pinning_the_process():
    """The generator must END, not heartbeat, once the process is going down.

    Without this the only thing that stopped it was the turn completing or the
    client hanging up, so a single idle tab held uvicorn open until SIGKILL.
    """
    turn = bs.Turn(id="t-shutdown", thread_id="th-1", agent_slug="general")
    turn.events.append(bs.TurnEvent(kind="text", payload={"text": "partial"}))

    async def drain():
        seen = []
        async for _off, ev in bs.iter_events(turn, after=0):
            seen.append(ev)
        return seen

    async def run():
        bs.SHUTTING_DOWN = True
        # 2s is far longer than this should need and far shorter than the 1s
        # heartbeat loop would run for (forever). If the generator regresses to
        # heartbeating, this fails as a timeout rather than hanging the suite.
        return await asyncio.wait_for(drain(), timeout=2.0)

    seen = asyncio.run(run())
    # The already-queued event is still delivered: releasing the stream must not
    # swallow output the client had not seen yet.
    assert [e.kind for e in seen] == ["text"]
    # And the turn itself is untouched -- it is paused, not ended. Its child is
    # still running and the next process adopts it.
    assert turn.complete is False


def test_event_stream_still_blocks_when_not_shutting_down():
    """The positive control. Without it, a generator that always returned
    immediately would pass the test above while breaking every live stream."""
    turn = bs.Turn(id="t-live", thread_id="th-1", agent_slug="general")

    async def run():
        bs.SHUTTING_DOWN = False
        agen = bs.iter_events(turn, after=0)
        # The turn is not complete and has no events, so the only thing this can
        # yield within the window is a heartbeat ping -- never a clean end.
        off, ev = await asyncio.wait_for(agen.__anext__(), timeout=3.0)
        await agen.aclose()
        return ev

    assert asyncio.run(run()).kind == "ping"


class _FakeStore:
    def __init__(self, value, boom=False):
        self.value = value
        self.boom = boom

    def active_run_id(self, thread_id):
        if self.boom:
            raise RuntimeError("log unreadable")
        return self.value


@pytest.mark.parametrize(
    "active,expected,why",
    [
        ("run-1", False, "thread still names this run: commit had NOT happened"),
        (None, True, "field cleared: commit_run appended the reply and cleared it"),
        ("run-2", True, "another run owns the thread now; re-driving would interleave"),
    ],
)
def test_already_committed_reads_the_thread_not_the_journal(monkeypatch, active, expected, why):
    """The journal cannot answer this. It still says "running" in every one of
    these cases; the thread's active_run_id is the durable record of who won."""
    monkeypatch.setattr(bs, "chat_store", _FakeStore(active))
    meta = {"thread_id": "th-1", "turn_id": "run-1"}
    assert bs._already_committed(meta) is expected, why


class TestALiveProcessMeansOurProcess:
    """Turn journals outlive the process that wrote them -- across a restart by
    design and across a reboot by accident -- and pids get recycled. `os.kill(
    pid, 0)` answers "does some process have this number", which made a long-dead
    turn read as live: the drive loop then tails a journal nothing is writing,
    never sees the `result` line it waits for, and (chat turns have no deadline)
    spins forever while the thread shows "Still running"."""

    def test_no_pid_is_not_alive(self):
        assert bs._proc_alive(None) is False
        assert bs._proc_alive(0) is False

    def test_a_pid_that_does_not_exist(self):
        assert bs._proc_alive(999_999) is False

    def test_a_recycled_pid_running_something_else(self):
        """This very interpreter exists and is emphatically not an agent."""
        import os

        assert bs._proc_alive(os.getpid()) is False

    def test_a_pid_owned_by_another_user(self):
        """init. Our agent children run as us, so another user's process is a
        recycled pid. This answered True before, which is the same hang."""
        assert bs._proc_alive(1) is False

    def test_a_real_agent_process_is_alive(self):
        """The positive control. Without it, a predicate that always said False
        would pass everything above while ending every healthy turn."""
        import subprocess

        found = subprocess.run(["pgrep", "-f", "claude"],
                               capture_output=True, text=True).stdout.split()
        if not found:
            pytest.skip("no claude process on this box to sample")
        assert any(bs._proc_alive(int(p)) for p in found[:5])


def test_already_committed_fails_toward_re_driving(monkeypatch):
    """When the answer cannot be read, re-drive.

    Re-driving a committed turn duplicates a message, which is bad; NOT driving
    an uncommitted one drops the operator's reply entirely, which is worse and
    is not recoverable by waiting. So the unreadable case must not look like
    "already committed".
    """
    monkeypatch.setattr(bs, "chat_store", _FakeStore(None, boom=True))
    assert bs._already_committed({"thread_id": "th-1", "turn_id": "run-1"}) is False
    # Same for a journal too incomplete to identify a run at all.
    assert bs._already_committed({}) is False
    assert bs._already_committed({"thread_id": "th-1"}) is False
