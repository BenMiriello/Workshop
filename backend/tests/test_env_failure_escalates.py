"""An environment failure must never burn an attempt, and must not retry forever.

The rc=5 leg of scripts/project_ralph.sh gives the attempt back, because an API
or auth failure is not the ticket's fault. Nothing counted how many times in a
row ONE task took that leg, so a task that could never start retried on every
poll indefinitely. Measured 2026-08-24: task fae54a3e took it six times in ten
minutes, always logging "attempt 1/3", while a second runner on the same tree
completed work normally. That is what proved it was that task or that runner,
not the API.

These are structural: they read the script rather than executing a sweep, which
is the same approach test_park_records_its_branch.py takes and for the same
reason (a real sweep spawns a model).
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RALPH = ROOT / "scripts" / "ralph.sh"
PROJECT_RALPH = ROOT / "scripts" / "project_ralph.sh"


@pytest.fixture(scope="module")
def project_ralph() -> str:
    return PROJECT_RALPH.read_text()


@pytest.fixture(scope="module")
def ralph() -> str:
    return RALPH.read_text()


def test_the_env_failure_leg_records_what_the_process_said(ralph: str):
    """Before this, the only evidence of the cause was discarded every time."""
    assert "ENV_FAIL_SAY=" in ralph
    assert 'head -c 800 "$ITER_OUT"' in ralph
    assert "said: ${ENV_FAIL_SAY:-<no output>}" in ralph


def test_the_captured_output_is_bounded(ralph: str):
    """A tight retry loop must not be able to flood the log with a stack trace."""
    assert "head -c 800" in ralph, "the capture must be byte-bounded"
    assert "ENV_FAIL_SAY=$(head" in ralph


def test_consecutive_env_failures_are_counted_per_task(project_ralph: str):
    assert "ENV_FAIL_DIR=" in project_ralph
    assert '.runtime/env-fails' in project_ralph
    assert 'ENV_FAIL_FILE="$ENV_FAIL_DIR/$TASK_ID"' in project_ralph, (
        "the count must be per TASK; a shared counter would let one stuck task "
        "park an unrelated one"
    )


def test_it_parks_rather_than_retrying_forever(project_ralph: str):
    assert "(( ENV_FAIL_N >= ENV_FAIL_MAX ))" in project_ralph
    assert 'ptasks_tx set-status "$DIR" "$TASK_ID" parked' in project_ralph


def test_parking_still_never_burns_an_attempt(project_ralph: str):
    """The whole point of rc=5 survives: the ticket is not blamed for the host."""
    idx = project_ralph.index("(( ENV_FAIL_N >= ENV_FAIL_MAX ))")
    # Slice to the break that ends the parking branch, not a fixed width: the
    # leg immediately below it is the RETRY leg, which legitimately does restore
    # an attempt, so an over-wide window fails on the wrong code.
    leg = project_ralph[idx : project_ralph.index("break", idx)]
    assert "--restore-attempt" not in leg, (
        "the parking leg must not restore an attempt it never took"
    )
    assert "never burned an attempt" in leg


def test_a_non_env_outcome_clears_the_count(project_ralph: str):
    """Otherwise a real outage would slowly park the whole backlog: every task
    would carry a stale count from the last outage and the next one would tip
    it over."""
    assert 'if [[ "$RC" != "5" ]]; then' in project_ralph
    reset = project_ralph.index('if [[ "$RC" != "5" ]]; then')
    case = project_ralph.index('case "$RC" in', reset)
    assert 'rm -f "$WORKSHOP/.runtime/env-fails/$TASK_ID"' in project_ralph[reset:case]


def test_the_threshold_is_overridable_but_defaulted(project_ralph: str):
    assert "ENV_FAIL_MAX=${ENV_FAIL_MAX:-3}" in project_ralph


def test_the_parked_note_says_it_is_not_the_tickets_fault(project_ralph: str):
    """The operator reads this on a phone weeks later. It must not read as the
    ticket being wrong, and it must say where to look."""
    idx = project_ralph.index("consecutive environment failures")
    note = project_ralph[idx : idx + 500]
    assert "not a fault in the ticket" in note
    assert "ralph.log" in note
    assert "Un-park" in note
