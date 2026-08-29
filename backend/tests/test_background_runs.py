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

    # Detached, and the runner carries the ids it needs.
    assert spawned["kw"].get("start_new_session") is True
    assert "services.background_runs" in spawned["cmd"] and "run" in spawned["cmd"]
    assert "--thread" in spawned["cmd"] and "t-123" in spawned["cmd"]
    assert "--job-id" in spawned["cmd"] and job_id in spawned["cmd"]
    # The runner is pinned to the chat store's runtime root, so its Job record
    # lands where the backend reads it (not a sandbox-local path that evaporates).
    assert spawned["kw"]["env"].get("WORKSHOP_RUNTIME") == str(ownership._RUNTIME)
    # The prompt is handed off in a file, not on the command line.
    pf_idx = spawned["cmd"].index("--prompt-file") + 1
    from pathlib import Path
    assert Path(spawned["cmd"][pf_idx]).read_text() == "Investigate the widget"


def test_the_runner_opens_the_job_record(owned, monkeypatch):
    # The runner, not launch, writes the Job (so it uses the corrected runtime).
    monkeypatch.setattr(background_runs, "_run_claude",
                        lambda *a, **k: (0, "the finding", {"output_tokens": 5}))
    delivered = {}
    monkeypatch.setattr(background_runs, "_deliver",
                        lambda **kw: delivered.update(kw))
    from pathlib import Path
    pf = Path(str(owned)) / "p.txt"
    pf.write_text("do the thing")
    background_runs.run(["--job-id", "job-run-1", "--thread", "t-9", "--owner", "6",
                         "--scope", "workshop", "--label", "probe", "--prompt-file", str(pf)])
    j = jobs._read_all().get("job-run-1")
    assert j, "runner did not record the job"
    assert j["kind"] == "background" and j["chat_id"] == "t-9"
    assert j["status"] == "done"  # finish line collapsed over the start
    assert delivered["status"] == "done" and delivered["text"] == "the finding"


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
    # NOT `autonomous`. A background agent's report asks nothing of
    # anybody; the flag it used to set meant "nobody was watching",
    # which was the producer guessing about the room rather than saying
    # what the message wanted. See services/run_report.py.
    assert msgs[0]["meta"]["asks"] == "notify"
    assert "autonomous" not in msgs[0]["meta"]
    # And the chat is asked to continue on its own — via thread state, not the queue.
    # The continuation file holds the wake signal; the operator's queue is empty.
    assert chat_queue.read(tid) == []
    entry = chat_queue.read_continuation(tid)
    assert entry is not None
    assert entry["client_id"] == "auto-job-9-" + tid


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
    # The wake signal is thread state, not a queue entry.
    assert chat_queue.read_continuation(tid) is not None


def test_a_run_the_clock_killed_says_so_and_keeps_what_it_had(owned):
    """The 2026-08-28 defect: an agent cut off at the wall-clock bound was
    delivered as "finished", with its partial output dropped, so the chat read a
    truncated job as a job that found nothing."""
    tid = chat_store.create_thread("general")["id"]
    background_runs._deliver(
        thread_id=tid, owner_id=6, job_id="job-t", label="audit",
        status="failed", text="half an answer", exit_code=background_runs.TIMED_OUT)
    msgs = [e for e in chat_log.read_events(tid)
            if e.get("kind") == "message" and e.get("role") == "assistant"]
    body = msgs[0]["content"]
    assert "ran out of time" in body.lower()
    assert "finished" not in body.lower()
    assert "half an answer" in body
    # And the continuation must not tell the next turn the agent finished.
    # The wake signal is thread state, not a queue entry.
    seed = chat_queue.read_continuation(tid)
    assert seed is not None
    assert "STOPPED" in seed["text"]
    assert "has finished" not in seed["text"]


def test_the_timeout_keeps_the_partial_stdout(owned, monkeypatch):
    """`capture_output` buffers, so a killed run's stdout lives on the exception.
    It used to be thrown away."""
    def fake_run(cmd, **kw):
        raise background_runs.subprocess.TimeoutExpired(
            cmd, kw.get("timeout", 1), output="partial text so far")

    monkeypatch.setattr(background_runs.subprocess, "run", fake_run)
    monkeypatch.setattr(background_runs, "_claude_bin", lambda: "/bin/true")
    from pathlib import Path
    code, text, usage = background_runs._run_claude(
        "prompt", Path(str(owned)), 6, "workshop")
    assert code == background_runs.TIMED_OUT
    assert text == "partial text so far"


def test_the_wall_clock_bound_is_an_hour_and_overridable(owned, monkeypatch):
    monkeypatch.delenv("WORKSHOP_BACKGROUND_TIMEOUT_S", raising=False)
    assert background_runs._timeout_s() == 3600
    monkeypatch.setenv("WORKSHOP_BACKGROUND_TIMEOUT_S", "120")
    assert background_runs._timeout_s() == 120
    monkeypatch.setenv("WORKSHOP_BACKGROUND_TIMEOUT_S", "nonsense")
    assert background_runs._timeout_s() == 3600


def test_the_runner_can_actually_write(owned, monkeypatch):
    """The 2026-08-28 defect: no --permission-mode, so a detached agent with
    nobody to ask ended every run by requesting approval to write its report."""
    seen = {}

    class Done:
        returncode = 0
        stdout = '{"result": "ok", "usage": {}}'
        stderr = ""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return Done()

    monkeypatch.setattr(background_runs.subprocess, "run", fake_run)
    monkeypatch.setattr(background_runs, "_claude_bin", lambda: "/bin/true")
    from pathlib import Path
    background_runs._run_claude("prompt", Path(str(owned)), 6, "workshop")
    cmd = seen["cmd"]
    i = cmd.index("--permission-mode")
    assert cmd[i + 1] == "bypassPermissions"
    # And the irreversible actions are still denied by name, in one group.
    assert cmd.count("--disallowedTools") == 1
    tail = cmd[cmd.index("--disallowedTools") + 1:]
    assert any("git push" in t for t in tail)
