"""A run that commits and then parks must say WHERE its commits are.

`publish_or_park` is the only place project_ralph.sh records a task branch, and
it is reached exclusively from the rc=0 success legs. Every other terminal leg
-- the touches gate, the classifier block, the rc=6 thrash park, the rc=7
strict block, the iteration/wall caps and the catch-all -- returned without it.

On 2026-08-23 that left eleven `wt/development/*` branches, each one commit
ahead of main, locked, and named by no ticket: the only way to find a parked
run's output was to read `git worktree list` and diff every branch by hand. The
rc=6 note made it worse by asserting "nothing committed" on five tickets whose
branch held a commit -- a false statement in the permanent record.

Two invariants, tested two ways because they fail two ways:

1. STRUCTURAL: every terminal leg that does not publish calls
   `note_unmerged_branch`. This is the one that regressed, and it regresses by
   someone adding a new `case` arm, so it is checked against the script text.
2. BEHAVIOURAL: the function itself is a no-op outside worktree mode and with
   no commit, and otherwise writes ONE ticket note naming the branch and head.
   Driven for real, with a stubbed `python3` capturing the argv.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from wsplatform import paths

RALPH = paths.scripts("project_ralph.sh")

pytestmark = pytest.mark.skipif(not RALPH.exists(), reason="needs project_ralph.sh")


def _source() -> str:
    return RALPH.read_text()


def _extract_function(name: str) -> str:
    """Pull one shell function definition out of the script by brace balance."""
    src = _source()
    start = src.index(f"\n  {name}() {{")
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


# --- 1. structural -----------------------------------------------------------

def test_publish_or_park_is_only_reached_from_the_success_legs():
    """The premise of this test file. If publish_or_park ever starts running on
    the park/block legs too, the structural check below is measuring nothing."""
    src = _source()
    assert src.count("publish_or_park \"$WT_BRANCH_SLUG\"") == 3, (
        "publish_or_park call sites moved; re-derive which legs record a branch"
    )


@pytest.mark.parametrize("leg", [
    "enforce_touches already recorded the evidence line",   # touches gate
    "classifier block",                                     # rc=3
    "no progress within the run",                           # rc=6 thrash park
    "latitude-strict:",                                     # rc=7 strict block
    "incomplete (rc=$RC)",                                  # rc=1 / rc=2 caps
    "failed (rc=$RC)",                                      # catch-all
])
def test_every_non_publishing_terminal_leg_records_the_branch(leg):
    src = _source()
    at = src.index(leg)
    # The call sits within the same leg: look back to the previous `case` arm
    # boundary and forward to the leg's own `;;`.
    window = src[max(0, at - 2500):src.index(";;", at) + 2]
    assert "note_unmerged_branch" in window, (
        f"terminal leg {leg!r} ends without recording its task branch"
    )


def test_the_thrash_park_no_longer_asserts_nothing_committed():
    src = _source()
    assert 'parked mid-run, nothing committed' not in src, (
        "the rc=6 note must not claim 'nothing committed' unconditionally"
    )
    assert 'parked mid-run, $PARK_WORK' in src


# --- 2. behavioural ----------------------------------------------------------

@pytest.fixture()
def harness(tmp_path):
    """Run `note_unmerged_branch` for real with a stubbed python3 that records
    the argv it was handed."""
    calls = tmp_path / "calls.txt"
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "python3").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n'
    )
    (stub / "python3").chmod(0o755)

    body = _extract_function("note_unmerged_branch")

    def run(*, wt_mode, committed, slug, head="abcdef0123456789"):
        calls.write_text("")
        script = "\n".join([
            "set -u",
            f'WT_MODE={wt_mode}', f'COMMITTED={committed}',
            f'WT_BRANCH_SLUG="{slug}"', 'WT_SLUG="development"',
            f'HEAD="{head}"', 'PTASKS="/ptasks.py"', 'DIR="/scope"',
            'TASK_ID="t1"', 'LOG="/dev/null"',
            body,
            "note_unmerged_branch",
        ])
        env = {"PATH": f"{stub}:{shutil.os.environ['PATH']}", "CALLS": str(calls)}
        proc = subprocess.run(["bash", "-c", script], env=env,
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return [ln for ln in calls.read_text().splitlines() if ln.strip()]

    return run


def test_it_is_a_no_op_outside_worktree_mode(harness):
    assert harness(wt_mode=0, committed=1, slug="t-abc") == []


def test_it_is_a_no_op_when_the_run_committed_nothing(harness):
    assert harness(wt_mode=1, committed=0, slug="t-abc") == []


def test_it_is_a_no_op_with_no_branch_slug(harness):
    assert harness(wt_mode=1, committed=1, slug="") == []


def test_a_committed_worktree_run_names_its_branch_and_head(harness):
    calls = harness(wt_mode=1, committed=1, slug="t-abc", head="0123456789abcdef")
    assert len(calls) == 1, calls
    argv = calls[0]
    assert "note /scope t1" in argv
    assert "wt/development/t-abc" in argv
    assert "0123456789ab" in argv            # short head, not the full sha
    assert "0123456789abcdef" not in argv
    assert "UNMERGED" in argv
