"""The in-chat background agent: launch opens a Job and spawns a detached runner;
the runner's delivery posts a result and seeds a continuation.

The runner's claude -p leg is exercised live (a real trivial turn) during
development rather than here; these pin the parts that must hold without a model:
the Job shape, the spawn, and the delivery-to-chat seam.
"""
import pytest

from services import background_runs, chat_log, chat_queue, chat_store, jobs, ownership


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(6)
    try:
        yield tmp_path
    finally:
        ownership.clear_current_owner()


def test_launch_opens_a_background_job_and_spawns_a_detached_runner(owned, monkeypatch):
    spawned = {}

    class FakeProc:
        pid = 4321

    def fake_popen(cmd, **kw):
        spawned["cmd"] = cmd
        spawned["kw"] = kw
        return FakeProc()

    monkeypatch.setattr(background_runs.subprocess, "Popen", fake_popen)
    job_id = background_runs.launch(
        thread_id="t-123", owner_id=6, scope="workshop",
        prompt="Investigate the widget", label="widget probe")

    # The Job is a background run linked to the chat.
    j = jobs._read_all().get(job_id)
    assert j, "job was not recorded"
    assert j["kind"] == "background"
    assert j["chat_id"] == "t-123"
    # Detached, and the runner carries the ids it needs.
    assert spawned["kw"].get("start_new_session") is True
    assert "services.background_runs" in spawned["cmd"] and "run" in spawned["cmd"]
    assert "--thread" in spawned["cmd"] and "t-123" in spawned["cmd"]
    # The prompt is handed off in a file, not on the command line.
    pf_idx = spawned["cmd"].index("--prompt-file") + 1
    from pathlib import Path
    assert Path(spawned["cmd"][pf_idx]).read_text() == "Investigate the widget"


def test_launch_refuses_an_empty_prompt(owned):
    with pytest.raises(ValueError):
        background_runs.launch(thread_id="t", owner_id=6, scope="workshop", prompt="   ")


def test_delivery_posts_the_result_and_seeds_a_continuation(owned):
    tid = chat_store.create_thread("general")["id"]
    background_runs._deliver(
        thread_id=tid, owner_id=6, job_id="job-9", label="probe",
        status="done", text="the answer is 42")
    msgs = [e for e in chat_log.read_events(tid)
            if e.get("kind") == "message" and e.get("role") == "assistant"]
    assert len(msgs) == 1
    assert "the answer is 42" in msgs[0]["content"]
    assert msgs[0]["meta"]["kind"] == "background_result"
    assert msgs[0]["meta"]["autonomous"] is True
    # And the chat is asked to continue on its own.
    seeds = [e for e in chat_queue.read(tid) if e.get("auto")]
    assert len(seeds) == 1
    assert seeds[0]["client_id"] == "auto-job-9-" + tid


def test_a_failed_run_still_tells_the_chat(owned):
    tid = chat_store.create_thread("general")["id"]
    background_runs._deliver(
        thread_id=tid, owner_id=6, job_id="job-x", label="probe",
        status="failed", text="")
    msgs = [e for e in chat_log.read_events(tid)
            if e.get("kind") == "message" and e.get("role") == "assistant"]
    assert len(msgs) == 1
    assert "stopped" in msgs[0]["content"].lower()
    # A failure still seeds a continuation so the chat says what happened.
    assert any(e.get("auto") for e in chat_queue.read(tid))
