"""A backend restart must PAUSE work, never end or duplicate it.

These cover the three ways a restart used to be destructive rather than merely
slow. Each maps to a specific defect found on 2026-08-13:

1. Shutdown killed the agent process it had gone to lengths to detach. The child
   is spawned with start_new_session=True and the unit sets KillMode=process so
   systemd's SIGTERM cannot reach it -- but asyncio cancels the drive loop on the
   way down, and the CancelledError handler ran _end_wedged, which SIGTERMs the
   child's process group and commits "This turn ended early" into the transcript.

2. A restart inside the commit window duplicated the reply. commit_turn appends
   the reply and clears active_turn_id, but the journal is marked done only
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

    def active_turn_id(self, thread_id):
        if self.boom:
            raise RuntimeError("log unreadable")
        return self.value


@pytest.mark.parametrize(
    "active,expected,why",
    [
        ("run-1", False, "thread still names this run: commit had NOT happened"),
        (None, True, "field cleared: commit_turn appended the reply and cleared it"),
        ("run-2", True, "another run owns the thread now; re-driving would interleave"),
    ],
)
def test_already_committed_reads_the_thread_not_the_journal(monkeypatch, active, expected, why):
    """The journal cannot answer this. It still says "running" in every one of
    these cases; the thread's active_turn_id is the durable record of who won."""
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

    def test_a_pid_owned_by_another_user_with_no_starttime_is_conservatively_alive(self):
        """init (root-owned; permission denied to signal it from here).

        Used to answer False unconditionally ("our agent children run as us,
        so this is a recycled pid"). That stopped being true once the uid
        layer (WORKSHOP_TURN_USER) can run a turn's real child as a different
        account: a PermissionError proves the pid EXISTS, just not who it
        belongs to, and without a starttime to check identity against, "can't
        signal it" must not be answered the same as "it's gone" -- that is
        exactly the failure that made every scoped turn's restart-adoption
        read as dead the moment isolation actually worked."""
        assert bs._proc_alive(1) is True

    def test_a_pid_owned_by_another_user_with_a_mismatched_starttime_is_dead(self):
        """Same permission-denied pid, but now identity CAN be checked: a
        starttime that does not match proves this is not the process the
        caller is asking about, regardless of who owns it."""
        assert bs._proc_alive(1, starttime="not-the-real-starttime") is False

    def test_a_pid_owned_by_another_user_with_the_real_starttime_is_alive(self):
        real = bs._proc_starttime(1)
        if real is None:
            pytest.skip("could not read /proc/1/stat on this box")
        assert bs._proc_alive(1, starttime=real) is True

    def test_a_real_agent_process_is_alive(self):
        """The positive control. Without it, a predicate that always said False
        would pass everything above while ending every healthy turn."""
        import subprocess

        found = subprocess.run(["pgrep", "-f", "claude"],
                               capture_output=True, text=True).stdout.split()
        if not found:
            pytest.skip("no claude process on this box to sample")
        assert any(bs._proc_alive(int(p)) for p in found[:5])


class TestAnOrphanedAgentIsRecovered:
    """The backend can go down between spawning the agent and journalling its
    pid. The journal then says `pid: null` while a real agent is running and
    writing to it -- and the re-attach read that as "died before it started",
    finalised the turn as lost, and deleted the journal out from under a live
    process, orphaning it with the session the next turn would try to resume.

    The session id is in the journal AND on the process's command line, so the
    orphan is identifiable.
    """

    @staticmethod
    def _a_live_agent():
        import re
        import subprocess

        out = subprocess.run(["pgrep", "-af", "claude"],
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            m = re.search(r"--(?:session-id|resume)\s+([0-9a-f-]{36})", line)
            if m:
                return int(line.split()[0]), m.group(1)
        return None, None

    def test_finds_a_running_agent_by_its_session_id(self):
        pid, sid = self._a_live_agent()
        if not sid:
            pytest.skip("no live agent with a session id on this box")
        assert bs._recover_pid(sid) == pid

    def test_an_unknown_session_finds_nothing(self):
        """Regression: matching on the command line alone matched the scanning
        shell itself, whose snapshot path contains `.claude/`. Identity is now
        `/proc/<pid>/comm`, which is exactly "claude"."""
        assert bs._recover_pid("00000000-0000-0000-0000-000000000000") is None

    def test_no_session_id_finds_nothing(self):
        assert bs._recover_pid(None) is None
        assert bs._recover_pid("") is None

    def test_identity_is_the_executable_not_the_arguments(self):
        import os

        assert bs._is_agent_proc(os.getpid()) is False
        pid, _ = self._a_live_agent()
        if pid:
            assert bs._is_agent_proc(pid) is True

    def test_an_unreadable_process_answers_the_callers_default(self):
        """Alive-checks must not invent a death; scans must not invent a match."""
        assert bs._is_agent_proc(999_999, unknown=True) is True
        assert bs._is_agent_proc(999_999) is False

    def test_bwrap_is_accepted_as_an_agent_proc(self):
        """The pid this module records for a scoped turn IS bwrap's, not the
        CLI's: bwrap forks a child rather than exec-replacing itself
        (confirmed live via `ps --forest`: the recorded pid's comm is "bwrap",
        with "claude" one level below it as its child). Before this, that pid
        failed `_is_agent_proc` outright, so every scoped turn's restart-
        adoption path (`_proc_alive` -> `_is_agent_proc`) read a live turn as
        dead the moment Layer 1 went live."""
        import shutil
        import subprocess
        import time

        if not shutil.which("bwrap"):
            pytest.skip("bwrap not installed")
        p = subprocess.Popen(
            ["bwrap", "--unshare-user", "--unshare-pid", "--ro-bind", "/usr", "/usr",
             "--proc", "/proc", "--", "/bin/sleep", "2"],
        )
        try:
            time.sleep(0.2)  # let it exec past the fork
            assert bs._is_agent_proc(p.pid) is True
        finally:
            p.kill()
            p.wait()


class TestProcStarttime:
    """The identity check the uid layer needs: two processes never share both
    a pid and a start time, so comparing it is exact across a recycle -- and
    unlike a signal, reading it does not depend on which uid owns the pid."""

    def test_reads_a_real_process(self):
        import os

        st = bs._proc_starttime(os.getpid())
        assert st is not None and st.isdigit()

    def test_a_nonexistent_pid_reads_nothing(self):
        assert bs._proc_starttime(999_999) is None

    def test_readable_across_a_uid_boundary(self):
        """/proc/1/stat is world-readable (mode 444) regardless of who owns
        pid 1 -- this is what makes starttime work under the uid layer, where
        `os.kill` cannot."""
        assert bs._proc_starttime(1) is not None

    def test_two_samples_of_the_same_process_agree(self):
        import os

        assert bs._proc_starttime(os.getpid()) == bs._proc_starttime(os.getpid())


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
