"""A record's process is judged dead by identity, not by pid number.

`os.kill(pid, 0)` only says some process holds a number. After a reboot a fresh
host hands low pids out again, so a Job or ticket claim written before the
reboot could read as alive forever and a chat waiting on it would never hear
back. `proc_liveness` settles it with the boot id and the pid's start time;
these pin that, and the two consumers: `jobs.reconcile_stuck` naming a reboot
as the reason, and `tasks.reclaim(dead_only=True)` releasing a dead claim at
once while still honouring the attempt cap.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from services import jobs, proc_liveness, tasks


def _reaped_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


# --- proc_liveness ------------------------------------------------------------

def test_a_live_pid_with_its_recorded_identity_is_alive():
    me = os.getpid()
    assert proc_liveness.check(me, **proc_liveness.identity(me)) == proc_liveness.ALIVE


def test_a_dead_pid_is_dead():
    assert proc_liveness.check(_reaped_pid()) == proc_liveness.DEAD


def test_a_recycled_pid_is_dead_by_its_start_time():
    """Same number, different process: the recorded start time does not match."""
    me = os.getpid()
    real = proc_liveness.read_pid_start(me)
    assert real is not None
    assert proc_liveness.check(me, pid_start=str(int(real) + 1)) == proc_liveness.DEAD


def test_a_legacy_record_detects_a_recycle_from_its_timestamp(monkeypatch):
    """No pid_start was recorded, but the process now at that pid started long
    after the record says the original already existed."""
    recorded = time.time() - 3600
    monkeypatch.setattr(proc_liveness, "boot_time", lambda: recorded - 3600)
    monkeypatch.setattr(proc_liveness, "started_at",
                        lambda pid: recorded + proc_liveness.REUSE_SLACK_S + 60)
    assert proc_liveness.check(os.getpid(), recorded_at=recorded) == proc_liveness.DEAD


def test_another_boot_id_is_a_reboot_even_for_a_live_pid():
    me = os.getpid()
    ident = proc_liveness.identity(me)
    if "boot_id" not in ident:
        pytest.skip("no boot id on this host")
    got = proc_liveness.check(me, boot_id="not-" + ident["boot_id"],
                              pid_start=ident.get("pid_start"))
    assert got == proc_liveness.REBOOTED


def test_a_record_older_than_the_boot_is_a_reboot_without_a_boot_id(monkeypatch):
    recorded = datetime.now(timezone.utc) - timedelta(hours=1)
    monkeypatch.setattr(proc_liveness, "boot_time",
                        lambda: recorded.timestamp() + 60)
    assert proc_liveness.check(os.getpid(), recorded_at=recorded.isoformat()) \
        == proc_liveness.REBOOTED
    assert proc_liveness.check(None, recorded_at=recorded.isoformat()) \
        == proc_liveness.REBOOTED


def test_no_pid_and_no_reboot_evidence_is_unknown():
    assert proc_liveness.check(None) == proc_liveness.UNKNOWN


def test_a_zombie_is_dead():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        for _ in range(200):
            if proc_liveness.is_zombie(child.pid):
                break
            time.sleep(0.01)
        else:
            pytest.skip("could not observe the child as a zombie")
        assert proc_liveness.check(child.pid) == proc_liveness.DEAD
    finally:
        child.wait(timeout=5)


# --- jobs.reconcile_stuck -----------------------------------------------------

@pytest.fixture
def job_log(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_LOG", tmp_path / "jobs.jsonl")
    from services import run_report
    monkeypatch.setattr(run_report, "post_for_job", lambda *a, **k: 0)
    monkeypatch.setattr(run_report, "post_to_tracker", lambda *a, **k: 0)
    return jobs


def _age(job_id: str, seconds: int) -> None:
    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    jobs._append({"id": job_id, "started_at": old})


def test_start_records_the_boot_and_the_pid_start(job_log):
    rec = job_log.start(trigger="manual", pid=os.getpid())
    assert rec.get("boot_id") == proc_liveness.current_boot_id()
    assert rec.get("pid_start") == proc_liveness.read_pid_start(os.getpid())


def test_a_job_from_before_a_reboot_fails_as_host_rebooted(job_log, monkeypatch):
    """The pid is live (this process), which is exactly the recycle a bare
    signal probe would have believed."""
    from services import background_runs
    told: list[tuple[str, str]] = []
    monkeypatch.setattr(background_runs, "report_lost",
                        lambda job, reason: told.append((job["id"], reason)))

    rec = job_log.start(trigger="manual", pid=os.getpid(), kind="background",
                        chat_id="thread-1")
    jobs._append({"id": rec["id"], "boot_id": "a-boot-that-is-over"})
    _age(rec["id"], 300)

    failed = job_log.reconcile_stuck()

    assert [j["id"] for j in failed] == [rec["id"]]
    after = job_log.get(rec["id"])
    assert after["status"] == "failed"
    assert after["evidence"]["halted"] == jobs.HOST_REBOOTED
    assert "host rebooted" in after["evidence"]["halted"]
    assert told == [(rec["id"], jobs.HOST_REBOOTED)]


def test_a_crashed_job_keeps_the_generic_reason(job_log):
    rec = job_log.start(trigger="manual", pid=_reaped_pid())
    _age(rec["id"], 300)
    job_log.reconcile_stuck()
    assert job_log.get(rec["id"])["evidence"]["halted"] == jobs.PROCESS_DIED


def test_the_same_boot_and_a_live_pid_is_left_running(job_log):
    rec = job_log.start(trigger="manual", pid=os.getpid())
    _age(rec["id"], 7200 * 2)
    assert job_log.reconcile_stuck() == []
    assert job_log.get(rec["id"])["status"] == "running"


# --- ticket claims ------------------------------------------------------------

@pytest.fixture
def root(tmp_path, monkeypatch):
    from services import ownership
    from tests.conftest import redirect_file_store

    redirect_file_store(monkeypatch, tmp_path)
    ownership.pin_operator()
    r = tmp_path / "scope"
    r.mkdir()
    return r


def _claimed(root, pid: int, **add_kw) -> str:
    task = tasks.add(root, text="a ticket whose worker may die", **add_kw)
    claimed = tasks.claim(root, task["id"], "job-1", pid=pid)
    assert claimed is not None and claimed["status"] == "doing"
    return task["id"]


def test_a_live_worker_is_not_reclaimed(root):
    task_id = _claimed(root, os.getpid())
    assert tasks.reclaim(root, dead_only=True) == []
    assert tasks.get_task(root, task_id)["status"] == "doing"


def test_a_dead_worker_is_released_without_waiting_for_the_lease(root):
    """Fresh heartbeat, lease an hour out: only the dead pid says reclaim."""
    task_id = _claimed(root, _reaped_pid())
    assert tasks.reclaim(root, dead_only=True) == [
        {"id": task_id, "action": "released"}]
    assert tasks.get_task(root, task_id)["status"] == "todo"


def test_a_claim_from_before_a_reboot_is_released_as_such(root, monkeypatch):
    task_id = _claimed(root, os.getpid())
    monkeypatch.setattr(proc_liveness, "boot_time", lambda: time.time() + 60)
    assert tasks.reclaim(root, dead_only=True) == [
        {"id": task_id, "action": "released", "reason": "host rebooted"}]


def test_a_reboot_still_respects_the_attempt_cap(root, monkeypatch):
    task_id = _claimed(root, os.getpid(), max_attempts=1)
    monkeypatch.setattr(proc_liveness, "boot_time", lambda: time.time() + 60)
    out = tasks.reclaim(root, dead_only=True)
    assert out == [{"id": task_id, "action": "parked", "reason": "host rebooted"}]
    after = tasks.get_task(root, task_id)
    assert after["status"] == "parked"
    assert "host reboot" in after["blocked_reason"]


def test_dead_only_leaves_an_expired_lease_on_a_live_worker(root):
    """The watchdog pass must never do the scheduler's lease expiry: a live but
    quiet worker keeps its claim however stale its heartbeat."""
    task_id = _claimed(root, os.getpid())
    far = datetime.now(timezone.utc) + timedelta(seconds=tasks.LEASE_TTL_SEC * 10)
    assert tasks.reclaim(root, now=far, dead_only=True) == []
    assert tasks.get_task(root, task_id)["status"] == "doing"


def test_reclaim_dead_claims_sweeps_every_scope(root):
    task_id = _claimed(root, _reaped_pid())
    assert tasks.reclaim_dead_claims([("scope-a", root)]) == [
        {"id": task_id, "action": "released", "scope": "scope-a"}]


def test_the_reconcile_loop_sweeps_the_scopes_it_is_handed(root, tmp_path, monkeypatch):
    """The loop's first pass runs at once and reclaims through the injected
    `scope_roots`, which is how main.py hands it the route-layer sweep."""
    import asyncio

    monkeypatch.setattr(jobs, "JOBS_LOG", tmp_path / "jobs.jsonl")
    task_id = _claimed(root, _reaped_pid())

    async def one_pass():
        loop = asyncio.create_task(
            jobs.reconcile_loop(interval_s=3600, scope_roots=lambda: [("scope-a", root)]))
        for _ in range(50):
            await asyncio.sleep(0.05)
            if tasks.get_task(root, task_id)["status"] != "doing":
                break
        loop.cancel()

    asyncio.run(one_pass())
    assert tasks.get_task(root, task_id)["status"] != "doing"
