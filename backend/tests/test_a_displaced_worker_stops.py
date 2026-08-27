"""A worker that loses its claim mid-run must stop, not continue best-effort.

`tasks.heartbeat` returns None when the fencing generation does not match, so an
operator edit, a reclaim or a preempt tells the worker it no longer holds the
ticket. The CLI prints `{}` and exits 0, and `ralph.sh`'s entire response used to
be one log line saying it was "continuing best-effort". The run then kept going:
up to another hour of wall clock and tokens spent on a ticket it did not own,
still writing into a worktree a new claimant may already hold, and it only found
out when its final status write was fenced and refused.

The read-only tree mount that would stop the tree half of this is built but its
flag `WORKSHOP_TREE_LEASE_MOUNT` is off, so ending the run is the only thing
standing between a displacement and two workers writing the same directory.

The behaviour lives in shell, so these run the REAL shell rather than a Python
restatement of it: a Python copy could stay green while the script drifted,
which is the failure mode this is here to catch. Same approach as
test_sweep_billing_scope.py.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
RALPH = SCRIPTS / "ralph.sh"
SWEEP = SCRIPTS / "project_ralph.sh"

DISPLACED_RC = 9


def _extract(script: Path, name: str) -> str:
    src = script.read_text()
    start = src.index(f"{name}() {{")
    end = src.index("\n}\n", start) + len("\n}\n")
    return src[start:end]


def _run_heartbeat(cli_stdout: str, tmp_path: Path) -> int:
    """Run ralph.sh's own heartbeat_task against a stubbed ticket CLI."""
    fake_cli = tmp_path / "fake_ptasks.py"
    fake_cli.write_text(f"import sys\nprint({cli_stdout!r})\n")
    fn = _extract(RALPH, "heartbeat_task")
    prog = (
        "beat_presence() { :; }\n"
        "log() { echo \"$*\"; }\n"
        f"PTASKS={str(fake_cli)!r}\n"
        "TASK_ID=t1\nCLAIM_TOKEN=3\nSTORE_DIR=/tmp\nLOG=/dev/null\n"
        f"{fn}\n"
        "heartbeat_task\n"
    )
    return subprocess.run(["bash", "-c", prog], capture_output=True,
                          text=True, timeout=60).returncode


def test_a_live_heartbeat_lets_the_run_continue(tmp_path):
    assert _run_heartbeat('{"id": "t1", "status": "doing"}', tmp_path) == 0


def test_a_fenced_heartbeat_reports_failure_so_the_run_can_end(tmp_path):
    """`{}` is what the CLI prints when tasks.heartbeat returns None."""
    assert _run_heartbeat("{}", tmp_path) != 0


def test_the_loop_ends_the_run_on_a_fenced_heartbeat():
    """The call site must ACT on it, and this RUNS the call site rather than
    reading it: a heartbeat that returns non-zero into a bare call would be the
    old behaviour wearing a new return code, and a text match cannot tell the
    difference between wiring that works and wiring that is merely present.

    The three lines are lifted verbatim out of ralph.sh's loop and driven with a
    stub heartbeat, once failing and once succeeding.
    """
    src = RALPH.read_text()
    assert "if ! heartbeat_task; then" in src, (
        "heartbeat_task's result is not checked at the call site")
    start = src.index("if ! heartbeat_task; then")
    call_site = src[start:src.index("\n  fi\n", start) + len("\n  fi\n")]

    def drive(heartbeat_rc: int) -> tuple[int, int]:
        prog = (
            f"heartbeat_task() {{ return {heartbeat_rc}; }}\n"
            "exit_code=1\niter=0\n"
            "while (( iter < 3 )); do\n"
            "  iter=$((iter + 1))\n"
            f"{call_site}"
            "done\n"
            'echo "$exit_code $iter"\n'
        )
        out = subprocess.run(["bash", "-c", prog], capture_output=True,
                             text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        code, iters = out.stdout.split()
        return int(code), int(iters)

    assert drive(1) == (DISPLACED_RC, 1), "a fenced heartbeat did not end the run"
    assert drive(0)[1] == 3, "a live heartbeat must not end the run"


def _code_only(text: str) -> str:
    """Executable lines only. These assertions are about what the shell DOES, and
    the comments beside it quote the old behaviour verbatim to explain why it
    changed, so matching raw text would assert against the explanation."""
    return "\n".join(l for l in text.splitlines()
                     if not l.lstrip().startswith("#"))


def test_the_worker_no_longer_claims_it_is_continuing():
    assert "continuing best-effort" not in _code_only(RALPH.read_text())


def test_the_exit_code_is_documented_in_the_contract():
    """The contract block is the only place a caller can learn what 9 means."""
    header = RALPH.read_text()[:6000]
    assert f"#   {DISPLACED_RC}  " in header
    assert "DISPLACED" in header


def test_the_displaced_code_does_not_collide_with_the_budget_pause():
    """Ticket 70860d58 independently claims 8 for the budget-cap pause. Two
    meanings on one code is a silent misroute, so 8 is reserved by name here and
    displaced took 9."""
    header = RALPH.read_text()[:6000]
    assert "#   8  " in header and "RESERVED" in header
    assert "70860d58" in header


def test_the_sweep_has_its_own_leg_for_displacement():
    """`Not inferred` is the requirement: without an explicit leg, rc=9 falls
    through to `*)`, which calls release_or_block and therefore consumes an
    attempt and writes a status onto a ticket somebody else now owns."""
    src = SWEEP.read_text()
    assert "\n    9)\n" in src, "no rc=9 leg in the sweep's case statement"
    leg = _code_only(src[src.index("\n    9)\n"):src.index("\n    1|2)\n")])
    assert "release_or_block" not in leg, "a displacement must not consume an attempt"
    assert "set-status" not in leg and "ptasks_tx" not in leg, (
        "a displacement must not write any status")
    assert "job_finish_child" in leg and "cancelled" in leg
    assert "note_unmerged_branch" not in leg, (
        "that writes to a ticket this run no longer owns")


def test_a_displacement_is_not_counted_as_a_task_failure():
    """The circuit breaker keys on tasks_failed; cooling a scope off because the
    operator took a ticket back would punish the scope for being used."""
    src = SWEEP.read_text()
    leg = _code_only(src[src.index("\n    9)\n"):src.index("\n    1|2)\n")])
    assert "FAILED=$((FAILED+1))" not in leg
    assert "BLOCKED=$((BLOCKED+1))" not in leg


@pytest.mark.parametrize("script", [RALPH, SWEEP])
def test_the_scripts_still_parse(script):
    assert subprocess.run(["bash", "-n", str(script)],
                          capture_output=True).returncode == 0
