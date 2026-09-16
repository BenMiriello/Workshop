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


def test_nonzero_exit_is_distinguishable_from_success(owned, monkeypatch, tmp_path):
    """A run that exits non-zero writes outcome=failed in status.json; zero writes done.
    The two files are distinct, so a finished run can never look like a dead one."""
    import json as _json
    monkeypatch.setattr(background_runs, "_deliver", lambda **kw: None)
    monkeypatch.setattr(background_runs, "_check_tree_dirty", lambda _cwd: False)
    monkeypatch.setattr(background_runs, "_reap_old_job_dirs", lambda: 0)
    pf = tmp_path / "p.txt"
    pf.write_text("do the thing")

    outcomes = {}
    for exit_code, expected_outcome in [(0, "done"), (1, "failed")]:
        jid = f"status-distinguish-{exit_code}"
        jdir = tmp_path / jid
        jdir.mkdir()
        monkeypatch.setattr(background_runs, "_job_dir", lambda _jid, _d=jdir: _d)
        monkeypatch.setattr(background_runs, "_run_claude",
                            lambda *a, _ec=exit_code, **k: (_ec, "output text", {}))
        background_runs.run(["--job-id", jid, "--thread", "t-9", "--owner", "6",
                             "--scope", "workshop", "--label", "probe",
                             "--prompt-file", str(pf)])
        st = _json.loads((jdir / "status.json").read_text())
        outcomes[exit_code] = st["outcome"]
        assert st["outcome"] == expected_outcome, (
            f"exit {exit_code}: expected outcome={expected_outcome!r}, got {st['outcome']!r}"
        )
        assert st["exit_code"] == exit_code
        assert "timestamp" in st
        assert "tree_dirty" in st

    assert outcomes[0] != outcomes[1], "success and failure produced the same outcome field"


# ---------------------------------------------------------------------------
# Confinement. The `spawn_background` MCP tool is reachable from any build
# provider, and this runner spawns `claude -p --permission-mode
# bypassPermissions`. Un-sandboxed, that is a writable agent with the host's
# filesystem view; with the old `_scope_cwd` fallback it was a writable agent
# pointed at the platform's own checkout. Both halves are pinned here.
# ---------------------------------------------------------------------------

def test_the_spawn_goes_through_scoped_exec(owned, monkeypatch, tmp_path):
    """The argv actually spawned is a bwrap invocation confining the agent to its
    scope at /workspace -- not the bare `claude` argv this used to run."""
    from services import scoped_exec
    if not scoped_exec.functional():
        pytest.skip("bwrap cannot namespace on this host")
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    monkeypatch.delenv("WORKSHOP_TURN_USER", raising=False)
    scope_root = tmp_path / "scope"
    scope_root.mkdir()

    seen = {}

    class Done:
        returncode = 0
        stdout = '{"result": "ok", "usage": {}}'
        stderr = ""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["kw"] = kw
        return Done()

    monkeypatch.setattr(background_runs.subprocess, "run", fake_run)
    monkeypatch.setattr(background_runs, "_claude_bin", lambda: "/bin/true")
    background_runs._run_claude("prompt", scope_root, 6, "workshop")

    cmd = seen["cmd"]
    assert cmd[0].endswith("bwrap"), f"the agent was spawned unconfined: {cmd[:3]}"
    # Confined TO ITS SCOPE, mounted at the fixed managed mountpoint, and the
    # child starts there rather than wherever bwrap was invoked.
    import os as _os
    real = _os.path.realpath(str(scope_root))
    assert any(cmd[k - 2] in ("--bind", "--ro-bind") and cmd[k - 1] == real
               for k, v in enumerate(cmd) if v == scoped_exec.MOUNTPOINT and k >= 2), \
        f"the scope root was not the thing mounted at {scoped_exec.MOUNTPOINT}"
    assert cmd[cmd.index("--chdir") + 1] == scoped_exec.MOUNTPOINT
    # bwrap itself starts at the real path; only the child is inside the view.
    assert seen["kw"]["cwd"] == str(scope_root)
    # The env went through turn_env too, so paths the child reads point at what
    # is actually mounted.
    assert "env" in seen["kw"]


def test_a_run_that_cannot_be_confined_is_refused_not_run(owned, monkeypatch, tmp_path):
    """`spawn_argv` fails CLOSED and `_run_claude` must not swallow that. The
    walls dropping is a refusal, never a degraded mode."""
    from services import scoped_exec
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "0")  # forced off == cannot confine
    monkeypatch.delenv("WORKSHOP_TURN_USER", raising=False)

    def unreachable(*a, **k):  # pragma: no cover - the point is it is never hit
        raise AssertionError("an unconfinable agent was spawned")

    monkeypatch.setattr(background_runs.subprocess, "run", unreachable)
    monkeypatch.setattr(background_runs, "_claude_bin", lambda: "/bin/true")
    with pytest.raises(scoped_exec.ConfinementUnavailable):
        background_runs._run_claude("prompt", tmp_path, 6, "workshop")


def test_an_unresolvable_scope_does_not_fall_back_to_the_platform_tree(monkeypatch):
    """The defect: `_scope_cwd` returned the Workshop's own checkout whenever the
    scope did not resolve -- which is the ordinary case for a project whose tree
    does not exist yet, and for any scope the dev-mode gate refuses. That handed
    a chat a bypassPermissions agent inside the platform source."""
    from services import task_scope
    monkeypatch.setattr(task_scope, "root_for_context", lambda _k: None)
    assert background_runs._scope_cwd("project:not-a-thing") is None

    # An exception on the way to the answer is not a fallback either.
    def boom(_k):
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr(task_scope, "root_for_context", boom)
    assert background_runs._scope_cwd("project:not-a-thing") is None

    # A root that resolves to a path that is not there is unresolvable too:
    # there is nothing for bwrap to bind.
    monkeypatch.setattr(task_scope, "root_for_context",
                        lambda _k: background_runs.Path("/no/such/tree"))
    assert background_runs._scope_cwd("project:not-a-thing") is None


def test_an_unresolvable_scope_finishes_the_job_as_failed_in_the_chat(owned, monkeypatch, tmp_path):
    """A refusal is an outcome, not a disappearance: the Job closes as failed and
    the chat is told why, through the same seam every other outcome uses."""
    from services import task_scope
    monkeypatch.setattr(task_scope, "root_for_context", lambda _k: None)

    def unreachable(*a, **k):  # pragma: no cover - the point is it is never hit
        raise AssertionError("an agent ran for an unresolvable scope")

    monkeypatch.setattr(background_runs, "_run_claude", unreachable)
    jdir = tmp_path / "jd"
    jdir.mkdir()
    monkeypatch.setattr(background_runs, "_job_dir", lambda _jid, _d=jdir: _d)

    tid = chat_store.create_thread("general")["id"]
    pf = tmp_path / "p.txt"
    pf.write_text("do the thing")
    background_runs.run(["--job-id", "job-refused", "--thread", tid, "--owner", "6",
                         "--scope", "project:not-a-thing", "--label", "probe",
                         "--prompt-file", str(pf)])

    j = jobs._read_all().get("job-refused")
    assert j and j["status"] == "failed"
    assert j["exit_code"] == background_runs.REFUSED

    import json as _json
    st = _json.loads((jdir / "status.json").read_text())
    assert st["outcome"] == "failed" and st["exit_code"] == background_runs.REFUSED

    msgs = [e for e in chat_log.read_events(tid)
            if e.get("kind") == "message" and e.get("role") == "assistant"]
    assert len(msgs) == 1
    body = msgs[0]["content"]
    assert "refused" in body.lower()
    assert "project:not-a-thing" in body
    # And the chat is woken to say so, rather than waiting on a job that ended.
    assert chat_queue.read_continuation(tid) is not None
