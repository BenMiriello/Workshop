"""What happens between "Run" and a worker actually existing.

Three things were wrong on this path and each one hid the next:

  * `launch_task_run` opened a Job and then Popen'd, recording no pid. Without a
    pid, `reconcile_stuck` cannot probe the process and falls back to a two-hour
    age ceiling, so every manual and chat-launched run that died read `running`
    for two hours. Five such Jobs were sitting in the live store.
  * `reconcile_stuck` had no caller that ever ran. Its shell caller is the
    dispatcher, which exits early whenever the autonomy brake is engaged. (A
    second shell caller, a watchdog script that was never in crontab, was
    deleted in 2026-08-17; `jobs.reconcile_loop` in the backend is the caller
    that actually runs.)
  * The chat's "file and run" called `launch_task_run` directly, skipping the
    readiness gate the /run route applied, so a BROKEN ticket launched from a card.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from services import jobs


@pytest.fixture
def job_log(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_LOG", tmp_path / "jobs.jsonl")
    # finish() posts a run report; keep the test off the real chat stores.
    from services import run_report
    monkeypatch.setattr(run_report, "post_for_job", lambda *a, **k: 0)
    return jobs


# --- pid recording ----------------------------------------------------------

def test_record_pid_attaches_to_an_open_job(job_log):
    rec = job_log.start(trigger="manual", task_id="t1")
    assert job_log.get(rec["id"]).get("pid") is None
    job_log.record_pid(rec["id"], 4242)
    after = job_log.get(rec["id"])
    assert after["pid"] == 4242
    # The append must not disturb anything else about the record.
    assert after["status"] == "running" and after["task_id"] == "t1"


def test_a_pid_append_cannot_resurrect_a_finished_job(job_log):
    """Ordering guard: the pid line is a partial update, so a Job that finished
    between the Popen and the append stays finished."""
    rec = job_log.start(trigger="manual")
    job_log.finish(rec["id"], status="done")
    job_log.record_pid(rec["id"], 4242)
    assert job_log.get(rec["id"])["status"] == "done"


# --- reconcile ---------------------------------------------------------------

def _age(job_log, job_id, seconds):
    """Backdate a Job's start so it is older than the reconcile floor."""
    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    jobs._append({"id": job_id, "started_at": old})


def test_a_recorded_pid_lets_reconcile_fail_a_dead_run_immediately(job_log):
    rec = job_log.start(trigger="manual", task_id="t1")
    job_log.record_pid(rec["id"], 999_999)   # no such process
    _age(job_log, rec["id"], 300)
    failed = job_log.reconcile_stuck()
    assert [j["id"] for j in failed] == [rec["id"]]
    assert job_log.get(rec["id"])["status"] == "failed"


def test_a_live_pid_is_left_alone(job_log):
    import os
    rec = job_log.start(trigger="manual", task_id="t1")
    job_log.record_pid(rec["id"], os.getpid())
    _age(job_log, rec["id"], 7200 * 2)       # far past the legacy age ceiling
    assert job_log.reconcile_stuck() == []
    assert job_log.get(rec["id"])["status"] == "running"


def test_without_a_pid_a_dead_run_waits_two_hours(job_log):
    """The behaviour the pid fix exists to avoid, pinned so the cost is visible."""
    rec = job_log.start(trigger="manual", task_id="t1")
    _age(job_log, rec["id"], 3600)           # 1h old, no pid
    assert job_log.reconcile_stuck() == []
    _age(job_log, rec["id"], 7300)           # past the 2h ceiling
    assert [j["id"] for j in job_log.reconcile_stuck()] == [rec["id"]]


async def _wait_for(done, timeout_s: float = 5.0) -> None:
    """Wait on a WALL CLOCK, not on a yield count.

    Both loop tests used to spin a fixed number of `await asyncio.sleep(0)`
    yields. `reconcile_loop` hands each pass to `asyncio.to_thread`, so a pass
    needs a real threadpool round-trip, and a yield count is not a duration: on a
    busy or slower machine the round-trips did not land inside the budget and the
    test failed with nothing wrong. Give it real time and a generous ceiling.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if done():
            return
        await asyncio.sleep(0.005)


def test_reconcile_loop_runs_reconcile_in_process(job_log, monkeypatch):
    """The whole point of item 4's second half: a caller that actually fires,
    with no cron and no dispatcher involved."""
    calls: list[int] = []
    monkeypatch.setattr(jobs, "reconcile_stuck", lambda: calls.append(1) or [])

    async def drive():
        task = asyncio.create_task(jobs.reconcile_loop(interval_s=0))
        await _wait_for(lambda: len(calls) >= 1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert calls, "reconcile_loop never called reconcile_stuck"


def test_reconcile_loop_survives_a_failing_pass(job_log, monkeypatch):
    """A reaper that dies on one bad pass is the same as no reaper."""
    calls: list[int] = []

    def boom():
        calls.append(1)
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(jobs, "reconcile_stuck", boom)

    async def drive():
        task = asyncio.create_task(jobs.reconcile_loop(interval_s=0))
        await _wait_for(lambda: len(calls) >= 2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert len(calls) >= 2, "loop stopped after the first exception"


# --- the readiness gate belongs to the launcher, not the route ---------------

def _broken_ticket():
    """No title -> ticket_readiness.blocking flags it."""
    return {"id": "t-broken", "title": "", "text": "", "status": "todo"}


def test_launch_refuses_a_broken_ticket_for_every_caller(monkeypatch, tmp_path):
    from routes import tasks_all
    from services import tasks

    monkeypatch.setattr(tasks_all, "_root_for_scope", lambda s: tmp_path)
    monkeypatch.setattr(tasks, "get_task", lambda root, tid: _broken_ticket())
    monkeypatch.setattr(tasks, "list_tasks", lambda root: [_broken_ticket()])
    launched: list = []
    monkeypatch.setattr(tasks_all.subprocess, "Popen",
                        lambda *a, **k: launched.append(a))

    with pytest.raises(HTTPException) as e:
        tasks_all.launch_task_run("workshop", "t-broken")
    assert e.value.status_code == 400
    assert not launched, "a broken ticket must not reach a worker"


def test_launch_404s_on_a_missing_ticket(monkeypatch, tmp_path):
    from routes import tasks_all
    from services import tasks

    monkeypatch.setattr(tasks_all, "_root_for_scope", lambda s: tmp_path)
    monkeypatch.setattr(tasks, "get_task", lambda root, tid: None)
    with pytest.raises(HTTPException) as e:
        tasks_all.launch_task_run("workshop", "nope")
    assert e.value.status_code == 404


def test_the_chat_path_and_the_route_share_one_gate():
    """chat.py calls launch_task_run directly; the gate has to live there or the
    two callers disagree. Pinned structurally so moving it back is caught."""
    import inspect
    from routes import tasks_all

    launcher = inspect.getsource(tasks_all.launch_task_run)
    assert "ticket_readiness.blocking" in launcher
    route = inspect.getsource(tasks_all.run_task)
    assert "ticket_readiness.blocking" not in route, \
        "the gate must not be duplicated in the route"


def test_a_good_ticket_launches_and_records_the_pid(monkeypatch, tmp_path, job_log):
    from routes import tasks_all
    from services import tasks

    good = {"id": "t-ok", "title": "Fix the picker", "text": "Fix the picker",
            "status": "todo"}
    monkeypatch.setattr(tasks_all, "_root_for_scope", lambda s: tmp_path)
    monkeypatch.setattr(tasks_all, "_sweep_dir_str", lambda s: None)
    monkeypatch.setattr(tasks, "get_task", lambda root, tid: good)
    monkeypatch.setattr(tasks, "list_tasks", lambda root: [good])

    class FakeProc:
        pid = 31337

    monkeypatch.setattr(tasks_all.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(tasks_all, "jobs", job_log)

    # `development` since ADR 0014: `workshop` is the root inbox and has no
    # working tree, so it is not a launch scope in any mode. The root is
    # monkeypatched to a tmp tree, so the dev-mode gate is not in play here.
    out = tasks_all.launch_task_run("development", "t-ok")
    assert out["ok"] is True
    rec = job_log.get(out["job_id"])
    assert rec["pid"] == 31337, "a launched run must carry its pid or it cannot be reaped"


# --- what actually picks work up (item 8) ------------------------------------

def test_pickup_separates_manual_from_automatic():
    from routes import tasks_all

    p = tasks_all.scope_pickup("agent:anything")
    assert p["runnable"] is True and p["automatic"] is False
    assert p["why"], "a scope nothing sweeps must say so"


def test_a_non_runnable_scope_is_neither():
    from routes import tasks_all

    p = tasks_all.scope_pickup("general")
    assert p["runnable"] is False and p["automatic"] is False


def _reg_with(tmp_path, monkeypatch, autonomy: str):
    from routes import tasks_all
    from services import autonomy_override

    monkeypatch.setattr(autonomy_override, "is_engaged", lambda: False)
    reg = tmp_path / "projects.json"
    reg.write_text('{"projects": {"demo": {"autonomy": %s}}}' % autonomy)
    monkeypatch.setattr(tasks_all, "_PROJECTS_REGISTRY", reg)
    return tasks_all


def test_an_on_demand_project_is_not_automatic(monkeypatch, tmp_path):
    """An on-demand project carries only a manual trigger, so `project:<slug>`
    work waits for a person. The predicate has to say that before the operator
    files."""
    tasks_all = _reg_with(
        tmp_path, monkeypatch,
        '{"preset": "on-demand", "triggers": [{"kind": "manual"}]}')

    p = tasks_all.scope_pickup("project:demo")
    assert p["runnable"] is True and p["automatic"] is False
    assert "demo" in p["why"]


def test_a_continuous_project_is_automatic(monkeypatch, tmp_path):
    tasks_all = _reg_with(
        tmp_path, monkeypatch,
        '{"preset": "continuous", "triggers": [{"kind": "loop"}]}')

    assert tasks_all.scope_pickup("project:demo")["automatic"] is True


def test_a_scheduled_project_is_automatic(monkeypatch, tmp_path):
    """The disagreement this predicate used to carry. A project put on a cron
    stored `autonomy.mode == "manual"` (the mode vocabulary had no word for
    `scheduled`), so this told the operator "set its autonomy to continuous or
    press Run" about a scope the dispatcher was already sweeping on a cadence.
    One encoding now: the stored trigger list."""
    tasks_all = _reg_with(
        tmp_path, monkeypatch,
        '{"preset": "scheduled",'
        ' "triggers": [{"kind": "cron", "cron_expression": "0 * * * *"}]}')

    p = tasks_all.scope_pickup("project:demo")
    assert p["automatic"] is True and p["why"] == ""


def test_an_unconfigured_project_is_not_automatic(monkeypatch, tmp_path):
    """No stored triggers means never configured, which is `off`. There is no
    second key to consult, so this must not read as automatic."""
    tasks_all = _reg_with(tmp_path, monkeypatch, "null")

    p = tasks_all.scope_pickup("project:demo")
    assert p["runnable"] is True and p["automatic"] is False


def test_the_autonomy_brake_makes_everything_manual(monkeypatch):
    from routes import tasks_all
    from services import autonomy_override

    monkeypatch.setattr(autonomy_override, "is_engaged", lambda: True)
    monkeypatch.setattr(autonomy_override, "read", lambda: {"reason": "migration"})
    # A scope that is runnable whatever the developer-mode flag says, so this
    # tests the brake and not the ADR 0014 gate.
    p = tasks_all.scope_pickup("project:demo")
    assert p["runnable"] is True and p["automatic"] is False
    assert "brake" in p["why"] and "migration" in p["why"]
