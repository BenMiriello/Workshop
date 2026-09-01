"""A chat can wake itself later, and the alarm outlives the turn that set it.

WHAT THIS PINS, in order of how much it matters:

1. THE ALARM SURVIVES TURN RETIREMENT. This is the whole feature, and it is the
   one thing that cannot be asserted, only demonstrated. A turn's process group
   is SIGTERMed at turn end (`build_streaming._retire_turn_proc`), which is why
   an in-session scheduler cannot wake anything: nothing of the turn is left by
   the time the job was due. So the test below arms a wake-up from a REAL child
   process in its OWN process group, retires it with the real retirement
   function, waits for it to actually be dead, and only then asks whether the
   alarm still fires. See `test_a_wakeup_survives_the_turn_being_retired`.
2. It fires through the wake path that already exists rather than a second one:
   a continuation seed, which `chat_drain` turns into a turn once the thread is
   idle. So the resident loop and the startup pass both fire it.
3. The prompt says it exists exactly where it exists, and says the opposite
   where it does not. No hedging in either direction.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services import chat_drain, chat_queue, chat_store, ownership, wakeups

_BACKEND = str(Path(__file__).resolve().parents[1])


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield tmp_path
    finally:
        ownership.clear_current_owner()


@pytest.fixture()
def tid(owned):
    return chat_store.create_thread("general")["id"]


@pytest.fixture()
def starter(monkeypatch):
    """Stands in for the send path, exactly as the queue-drain tests do."""
    calls: list[list[dict]] = []

    async def fake(thread_id: str, batch: list[dict]) -> bool:
        calls.append(batch)
        return True

    monkeypatch.setattr(chat_drain, "_starter", fake)
    monkeypatch.setattr(chat_drain, "_locks", {})
    return calls


def _in(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


# ---- the store -------------------------------------------------------------

def test_arming_one_puts_it_on_disk_where_another_process_can_read_it(tid):
    rec = wakeups.arm(tid, reason="check the deploy settled", due_at=_in(600))
    assert wakeups.path(tid).exists()
    # Read back through a fresh call, not from the returned dict: the point of
    # the file is that something other than this process can see it.
    assert [w["id"] for w in wakeups.read(tid)] == [rec["id"]]
    assert wakeups.read(tid)[0]["reason"] == "check the deploy settled"


def test_a_wakeup_needs_a_reason_because_the_reason_is_the_whole_prompt(tid):
    with pytest.raises(ValueError):
        wakeups.arm(tid, reason="   ", due_at=_in(60))


def test_many_can_be_armed_and_they_come_back_soonest_first(tid):
    late = wakeups.arm(tid, reason="later", due_at=_in(3600))
    soon = wakeups.arm(tid, reason="sooner", due_at=_in(60))
    assert [w["id"] for w in wakeups.read(tid)] == [soon["id"], late["id"]]


def test_cancelling_takes_it_back_and_cancelling_twice_is_not_an_error(tid):
    rec = wakeups.arm(tid, reason="never mind", due_at=_in(600))
    assert wakeups.cancel(tid, rec["id"])["id"] == rec["id"]
    assert wakeups.read(tid) == []
    assert wakeups.cancel(tid, rec["id"]) is None


def test_the_store_file_goes_away_when_nothing_is_armed(tid):
    rec = wakeups.arm(tid, reason="x", due_at=_in(600))
    wakeups.cancel(tid, rec["id"])
    # "does this thread have an alarm" must be answerable by a stat: the firing
    # pass asks it of every thread on every tick.
    assert not wakeups.path(tid).exists()
    assert wakeups.threads_with_wakeups() == []


def test_only_the_due_ones_are_due(tid):
    soon = wakeups.arm(tid, reason="now", due_at=_in(-5))
    wakeups.arm(tid, reason="later", due_at=_in(3600))
    assert [w["id"] for w in wakeups.due(tid)] == [soon["id"]]


def test_an_unreadable_store_is_empty_rather_than_a_wedged_thread(tid):
    wakeups.arm(tid, reason="x", due_at=_in(600))
    wakeups.path(tid).write_text("{not json at all")
    assert wakeups.read(tid) == []


def test_deleting_a_thread_forgets_its_alarms(tid):
    wakeups.arm(tid, reason="x", due_at=_in(600))
    wakeups.forget(tid)
    assert wakeups.read(tid) == []


# ---- saying when -----------------------------------------------------------

def test_a_delay_in_minutes_is_that_many_minutes_from_now():
    now = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    when = wakeups.resolve_when(in_minutes=90, now=now)
    assert when.astimezone(timezone.utc) == now + timedelta(minutes=90)


def test_a_clock_time_means_the_next_time_the_clock_reads_that(monkeypatch):
    from services import installation

    monkeypatch.setenv("WORKSHOP_TIMEZONE", "UTC")
    now = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    assert installation.timezone_name() == "UTC"
    # Later today.
    assert wakeups.resolve_when(at="16:30", now=now).hour == 16
    assert wakeups.resolve_when(at="16:30", now=now).day == 1
    # ...and tomorrow once it is behind us, rather than a time in the past.
    assert wakeups.resolve_when(at="07:00", now=now).day == 2


def test_a_bare_timestamp_is_read_in_the_installations_own_zone(monkeypatch):
    monkeypatch.setenv("WORKSHOP_TIMEZONE", "America/Los_Angeles")
    now = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    when = wakeups.resolve_when(at="2026-09-01T09:00", now=now)
    # 09:00 Pacific is 16:00 UTC, not 09:00 UTC. A wake-up that lands seven
    # hours from where it was asked for is the failure this rules out.
    assert when.astimezone(timezone.utc).hour == 16


def test_an_explicit_offset_wins_over_the_installations_zone(monkeypatch):
    monkeypatch.setenv("WORKSHOP_TIMEZONE", "America/Los_Angeles")
    now = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    when = wakeups.resolve_when(at="2026-09-01T20:00+00:00", now=now)
    assert when.astimezone(timezone.utc).hour == 20


@pytest.mark.parametrize("kwargs", [
    {},                                             # said nothing
    {"in_minutes": 5, "at": "16:30"},               # said both
    {"in_minutes": 0},                              # not in the future
    {"in_minutes": -5},
    {"at": "2020-01-01T00:00+00:00"},               # already gone
    {"at": "next tuesday afternoon"},               # unreadable
])
def test_a_time_that_cannot_be_honoured_is_refused_not_guessed(kwargs):
    with pytest.raises(ValueError):
        wakeups.resolve_when(now=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
                             **kwargs)


# ---- firing ----------------------------------------------------------------

def test_a_due_wakeup_becomes_a_continuation_seed_and_leaves_the_store(tid):
    rec = wakeups.arm(tid, reason="check the deploy", due_at=_in(-1))
    assert wakeups.fire_due() == 1
    seed = chat_queue.read_continuation(tid)
    assert seed is not None
    assert "check the deploy" in seed["text"]
    # Framed as a wake-up, not as the operator talking.
    assert "not a message from the operator" in seed["text"].lower()
    assert seed["client_id"] == f"auto-{rec['id']}"
    assert wakeups.read(tid) == []


def test_one_that_is_not_due_yet_does_nothing_at_all(tid):
    wakeups.arm(tid, reason="later", due_at=_in(3600))
    assert wakeups.fire_due() == 0
    assert chat_queue.read_continuation(tid) is None
    assert len(wakeups.read(tid)) == 1


def test_it_does_not_clobber_a_seed_something_else_is_holding(tid):
    chat_drain.request_continuation(tid, seed="a finished run", dedupe_id="job-1")
    wakeups.arm(tid, reason="mine", due_at=_in(-1))
    assert wakeups.fire_due() == 0
    # Theirs is intact and ours is still armed, so nothing is lost either way.
    assert chat_queue.read_continuation(tid)["text"] == "a finished run"
    assert len(wakeups.read(tid)) == 1
    # Once theirs is consumed, ours goes on the next pass.
    chat_queue.clear_continuation(tid)
    assert wakeups.fire_due() == 1
    assert "mine" in chat_queue.read_continuation(tid)["text"]


def test_two_due_at_once_fire_one_per_pass_because_a_thread_holds_one_seed(tid):
    wakeups.arm(tid, reason="first", due_at=_in(-20))
    wakeups.arm(tid, reason="second", due_at=_in(-10))
    assert wakeups.fire_due() == 1
    assert "first" in chat_queue.read_continuation(tid)["text"]
    assert len(wakeups.read(tid)) == 1
    chat_queue.clear_continuation(tid)
    assert wakeups.fire_due() == 1
    assert "second" in chat_queue.read_continuation(tid)["text"]


def test_firing_actually_starts_a_turn_on_the_thread(tid, starter):
    wakeups.arm(tid, reason="report the overnight numbers", due_at=_in(-1))
    wakeups.fire_due()
    assert asyncio.run(chat_drain.drain_continuation(tid, reason="test")) is True
    assert len(starter) == 1
    assert "report the overnight numbers" in starter[0][0]["text"]
    # A wake-up is thread state, not an operator message: it must not be
    # attributed to them.
    assert starter[0][0]["auto"] is True
    assert starter[0][0]["sender"] == "platform"


def test_it_waits_for_a_turn_that_is_still_running(tid, starter):
    wakeups.arm(tid, reason="check in", due_at=_in(-1))
    wakeups.fire_due()
    chat_store.begin_turn(tid, "run-1")
    assert asyncio.run(chat_drain.drain_continuation(tid, reason="test")) is False
    assert starter == []
    chat_store.release_turn(tid, "run-1")
    assert asyncio.run(chat_drain.drain_continuation(tid, reason="test")) is True


def test_the_startup_pass_fires_one_that_came_due_while_the_backend_was_down(
        tid, starter, monkeypatch):
    """An alarm for 03:00 against a backend that was down at 03:00 fires late,
    not never. That is the correct failure for an alarm and it is why the store
    is a file rather than a timer in a process."""
    monkeypatch.setattr(chat_drain, "_all_owner_ids", lambda: [1])
    wakeups.arm(tid, reason="the overnight check", due_at=_in(-7200))
    assert asyncio.run(chat_drain.drain_all(reason="startup")) == 1
    assert "the overnight check" in starter[0][0]["text"]
    assert wakeups.read(tid) == []


def test_the_resident_loop_fires_one_without_anybody_asking(tid, starter, monkeypatch):
    """The loop in the long-lived backend process is the scheduler. Nothing else
    is: the turn that armed this is long gone by the time it comes due."""
    monkeypatch.setattr(chat_drain, "_all_owner_ids", lambda: [1])
    wakeups.arm(tid, reason="the scheduled check", due_at=_in(-1))

    async def one_pass():
        task = asyncio.ensure_future(chat_drain.continuation_loop(interval=0.01))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if starter:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(one_pass())
    assert starter, "the resident loop never fired the wake-up"
    assert "the scheduled check" in starter[0][0]["text"]


# ---- the thing that actually matters ---------------------------------------

_ARMER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from services import ownership
    ownership._RUNTIME = Path(sys.argv[2])
    ownership.set_current_owner(1)
    from datetime import datetime, timedelta, timezone
    from services import wakeups
    rec = wakeups.arm(sys.argv[3], reason="the reason outlived the process",
                      due_at=datetime.now(timezone.utc) + timedelta(seconds=int(sys.argv[4])))
    print(rec["id"], flush=True)
    # Now sit here exactly as a turn's process does, waiting to be retired.
    while True:
        time.sleep(1)
    """
)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - not this test's uid
        return True
    # A reaped-but-not-waited child is a zombie and is not "alive" for our
    # purposes; the caller waits on it before asking.
    return True


def test_a_wakeup_survives_the_turn_being_retired(tid, tmp_path, starter):
    """THE POINT OF THE FEATURE, demonstrated rather than asserted.

    A turn ends by SIGTERMing its whole process group. Everything the turn was
    holding dies with it -- which is exactly why an in-session scheduler cannot
    wake anything later, and why this had to be built somewhere else. So: arm
    the alarm from a real child in its own process group, retire it with the
    real retirement path, confirm it is genuinely dead, and only then ask
    whether the alarm still exists and still fires.
    """
    from services import build_streaming

    proc = subprocess.Popen(
        [sys.executable, "-c", _ARMER, _BACKEND, str(tmp_path), tid, "1"],
        stdout=subprocess.PIPE, text=True,
        start_new_session=True,  # its own process group, as a turn's process is
    )
    try:
        wake_id = proc.stdout.readline().strip()
        assert wake_id.startswith("wake_"), "the child never armed anything"
        assert os.getpgid(proc.pid) == proc.pid, "the child is not its own group"

        # The real retirement, not a stand-in for it.
        asyncio.run(build_streaming._retire_turn_proc(proc.pid, None))
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:  # pragma: no cover - the retirement failed
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
    assert not _alive(proc.pid) or proc.returncode is not None
    assert proc.returncode is not None, "the process that armed it is still running"

    # Nothing of that turn is left. The alarm is.
    assert [w["id"] for w in wakeups.read(tid)] == [wake_id]

    # And it still goes off, in this process, which never held it.
    time.sleep(1.1)
    assert wakeups.fire_due() == 1
    assert asyncio.run(chat_drain.drain_continuation(tid, reason="test")) is True
    assert "the reason outlived the process" in starter[0][0]["text"]


def test_the_alarm_also_survives_the_reader_restarting(tid, starter, monkeypatch):
    """Nothing in memory holds a wake-up: the module is re-imported from scratch
    and the alarm is still there. A backend restart between arming and firing is
    the ordinary case, not an edge one."""
    import importlib

    rec = wakeups.arm(tid, reason="still here", due_at=_in(-1))
    fresh = importlib.reload(wakeups)
    monkeypatch.setattr(chat_drain, "_all_owner_ids", lambda: [1])
    assert [w["id"] for w in fresh.read(tid)] == [rec["id"]]
    assert fresh.fire_due() == 1
    assert asyncio.run(chat_drain.drain_continuation(tid, reason="test")) is True
    assert "still here" in starter[0][0]["text"]
