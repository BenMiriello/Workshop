"""A killed worker's committed work is graded as a failed attempt instead of
going to review — fixed.

Ticket: "A killed worker's committed work is graded as a failed attempt instead
of going to review." Measured 2026-09-16/17 on f8c7d271: `timeout` killed three
worker turns, two of them left real commits (366 and 358 insertions, two new
test files) on their task branch, and `project_ralph.sh`'s `1|2` leg burned all
three attempts anyway because it computes COMMITTED (from `git rev-list
BASELINE..HEAD`) but never consulted it on that leg. The ticket ended up
`blocked` on "incomplete after 3 attempts", which was false, and the two
branches carrying real work sat unreferenced by any ticket.

Two independent defects, two independent fixes, covered here:

1. `ralph.sh`: on a `timeout` kill (exit 124) the run used to fall straight
   into the SAME reviewer call every other iteration gets, grading a severed
   turn's tree as if the worker had finished and stopped. Now a killed
   iteration commits whatever landed inside the task's lane and moves straight
   to the next iteration, never handing the tree to the reviewer.

2. `project_ralph.sh`: the `1|2` leg (iteration cap / wall cap) now checks
   COMMITTED before deciding whether the run failed. No commits is unchanged —
   a real failure, attempt burned. Real commits are parked with the attempt
   restored and a Change opened, same shape as the rc=8 budget-cap pause
   (70860d58) reuses.

THESE RUN THE REAL SHELL, same approach as test_budget_cap_pause.py and
test_a_displaced_worker_stops.py: a Python restatement of this behaviour could
stay green while the scripts drifted, which is the failure mode this file
exists to catch. No test here drives a real `claude` process — the severed-run
DECISION, the commit, and the caller's routing are all exercised against the
real shell; whether `--session-id` actually makes a killed CLI session
resumable is a property of the CLI itself, tested here only as "the flag is
passed", the same "best effort" boundary test_budget_cap_pause.py draws around
`claude --resume`.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
RALPH = SCRIPTS / "ralph.sh"
SWEEP = SCRIPTS / "project_ralph.sh"

TIMEOUT_RC = 124


def _extract_fn(script: Path, name: str) -> str:
    """One shell FUNCTION's source, by name — anchored at the start of a line
    so `commit_lane() {` cannot match inside a longer name first."""
    src = script.read_text()
    start = src.index(f"\n{name}() {{") + 1
    end = src.index("\n}\n", start) + len("\n}\n")
    return src[start:end]


def _severed_run_block() -> str:
    """The exact `if (( CLAUDE_EXIT == 124 )); then ... fi` ralph.sh runs right
    after billing a timeout-killed iteration, before classifier detection, the
    sentinel check, or the reviewer call. Anchored on the bare condition (no
    `&&`), which is unique — the nominal-billing check just above it reads
    `CLAUDE_EXIT == 124 && ITER_TOKENS == 0 && ITER_DUR > 60` and would match a
    substring search for the plain form too if it came first."""
    src = RALPH.read_text()
    start = src.index("if (( CLAUDE_EXIT == 124 )); then")
    end = src.index("\n  fi\n", start) + len("\n  fi\n")
    return src[start:end]


def _committed_leg() -> str:
    """project_ralph.sh's `1|2)` case leg, the `if (( COMMITTED == 1 )); then
    ... fi` body (the trailing ` ;;` stripped so it runs as a bare statement).

    `if (( COMMITTED == 1 )); then` alone is NOT unique — two earlier one-liners
    (the rc=6 thrash-park and rc=8 budget-cap-pause legs) share the exact same
    condition text to pick a log phrase. Anchored on `CHANGE_ID=""`, the first
    line of this leg's body, which only this leg has."""
    src = SWEEP.read_text()
    marker = 'CHANGE_ID=""'
    marker_at = src.index(marker)
    start = src.rindex("if (( COMMITTED == 1 )); then", 0, marker_at)
    end = src.index("\n      fi ;;\n", marker_at) + len("\n      fi ;;\n")
    return src[start:end].rsplit(";;", 1)[0]


def _bash(prog: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", prog], capture_output=True, text=True,
                          timeout=120, cwd=str(cwd) if cwd else None)


def _git_repo(tmp_path: Path) -> Path:
    d = tmp_path / "tree"
    d.mkdir()
    for cmd in (["git", "init", "-q", "-b", "main"],
                ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=d, check=True, capture_output=True)
    (d / "README.md").write_text("base\n")
    subprocess.run(["git", "add", "README.md"], cwd=d, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=d, check=True,
                   capture_output=True)
    return d


class TestTheSeveredIterationNeverReachesTheReviewer:
    """The control-flow half of fix 1: a 124 must commit, must skip the
    reviewer, and must let the loop carry on to the next iteration (the wall
    and iteration caps above it still bound how many times it can retry)."""

    def _drive(self, exits: list[int]) -> subprocess.CompletedProcess:
        block = _severed_run_block()
        exits_bash = " ".join(str(e) for e in exits)
        prog = (
            "set -u\n"
            "commit_calls=0; accum_calls=0; reviewer_calls=0\n"
            "commit_lane() { commit_calls=$((commit_calls+1)); return 0; }\n"
            "accum_worker_commits() { accum_calls=$((accum_calls+1)); }\n"
            "log() { :; }\n"
            "TASK='do the ticket'\n"
            f"EXITS=({exits_bash})\n"
            "iter=0\n"
            "while (( iter < ${#EXITS[@]} )); do\n"
            "  iter=$((iter+1))\n"
            "  CLAUDE_EXIT=${EXITS[$((iter-1))]}\n"
            f"{block}\n"
            # Reached only when the block above did NOT `continue` — i.e. only
            # on a non-124 exit. Stands in for "the reviewer ran".
            "  reviewer_calls=$((reviewer_calls+1))\n"
            "done\n"
            'echo "iter=$iter commit_calls=$commit_calls accum_calls=$accum_calls reviewer_calls=$reviewer_calls"\n'
        )
        return _bash(prog)

    def _parse(self, r: subprocess.CompletedProcess) -> dict:
        assert r.returncode == 0, r.stderr
        return dict(kv.split("=") for kv in r.stdout.split())

    def test_a_kill_commits_and_skips_the_reviewer(self):
        out = self._parse(self._drive([TIMEOUT_RC]))
        assert out["commit_calls"] == "1"
        assert out["accum_calls"] == "1"
        assert out["reviewer_calls"] == "0", (
            "the reviewer ran on a severed iteration with no result envelope")

    def test_a_kill_does_not_stop_the_run__the_loop_carries_on(self):
        """Three kills in a row must still reach iteration 3, not `break` out
        after the first — the wall/iteration caps above this block are what's
        supposed to end a run that keeps getting killed, not this branch."""
        out = self._parse(self._drive([TIMEOUT_RC, TIMEOUT_RC, TIMEOUT_RC]))
        assert out["iter"] == "3"
        assert out["commit_calls"] == "3"
        assert out["accum_calls"] == "3"
        assert out["reviewer_calls"] == "0"

    def test_an_ordinary_exit_is_unaffected_and_still_reaches_the_reviewer(self):
        out = self._parse(self._drive([0]))
        assert out["commit_calls"] == "0"
        assert out["accum_calls"] == "0"
        assert out["reviewer_calls"] == "1"

    def test_a_kill_then_a_clean_iteration__only_the_kill_skips_review(self):
        out = self._parse(self._drive([TIMEOUT_RC, 0]))
        assert out["iter"] == "2"
        assert out["commit_calls"] == "1"
        assert out["reviewer_calls"] == "1", (
            "the second, non-killed iteration must still reach the reviewer")


class TestTheSeveredIterationReallyCommits:
    """Same lane discipline as the budget-cap pause: `commit_lane` is REUSED,
    not reimplemented, so this drives the real function against a real repo
    rather than a stub — proving the severed run's file actually lands."""

    def _run(self, tmp_path: Path, *, dirty: str | None = "worker_output.py",
             touches: str = "*.py") -> tuple[subprocess.CompletedProcess, Path]:
        tree = _git_repo(tmp_path)
        fake_ptasks = tmp_path / "fake_ptasks.py"
        fake_ptasks.write_text(
            "import json, sys\n"
            "a = sys.argv[1:]\n"
            "if a and a[0] == 'list-json':\n"
            f"    print(json.dumps([{{'id': 't1', 'touches': {touches!r}.split(',')}}]))\n")
        if dirty:
            (tree / dirty).write_text("print('the work the killed turn did')\n")
        prog = (
            "set -u\n"
            f"DIR={str(tree)!r}\n"
            "BASELINE=$(cd \"$DIR\" && git rev-parse HEAD)\n"
            "PRERUN_DIRTY=\"\"\n"
            "BOOKKEEPING_RE='^(\\.tasks\\.json|\\.runtime/)'\n"
            "TASK='do the ticket'\nTASK_ID=t1\nSTORE_DIR=/tmp\nLOG=/dev/null\n"
            f"PTASKS={str(fake_ptasks)!r}\n"
            "log() { echo \"[log] $*\"; }\n"
            "accum_worker_commits() { :; }\n"
            "CLAUDE_EXIT=124\n"
            f"{_extract_fn(RALPH, 'commit_lane')}\n"
            # A REAL one-pass loop, not a bare `continue`: outside any loop
            # bash treats `continue` as a non-fatal warning and keeps going,
            # which would make the DID_NOT_CONTINUE marker meaningless. Inside
            # this loop, `continue` skips straight to the (false) loop
            # condition and the marker below never prints.
            "n=0\n"
            "while (( n < 1 )); do\n"
            "  n=$((n+1)); iter=$n\n"
            f"{_severed_run_block()}\n"
            "  echo DID_NOT_CONTINUE\n"
            "done\n"
        )
        return _bash(prog), tree

    def test_it_commits_the_killed_turns_work(self, tmp_path):
        r, tree = self._run(tmp_path)
        # `continue` inside a bare (non-loop) shell -- outside any loop -- is a
        # runtime error in bash, which is exactly the signal we want: it proves
        # the block actually reached `continue` rather than falling through.
        assert "DID_NOT_CONTINUE" not in r.stdout
        log = subprocess.run(["git", "log", "--oneline"], cwd=tree,
                             capture_output=True, text=True).stdout
        assert "timeout kill at iter 1" in log
        show = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"],
                              cwd=tree, capture_output=True, text=True).stdout
        assert "worker_output.py" in show
        status = subprocess.run(["git", "status", "--porcelain"], cwd=tree,
                                capture_output=True, text=True).stdout
        assert status.strip() == "", f"the killed turn's work was left uncommitted: {status!r}"

    def test_a_kill_with_nothing_to_commit_still_skips_the_reviewer(self, tmp_path):
        r, tree = self._run(tmp_path, dirty=None)
        assert "DID_NOT_CONTINUE" not in r.stdout
        status = subprocess.run(["git", "status", "--porcelain"], cwd=tree,
                                capture_output=True, text=True).stdout
        assert status.strip() == ""


class TestSessionIdIsMintedUpFront:
    """`--session-id` reserves the uuid BEFORE the first spawn, so a kill that
    happens before any result envelope is written still leaves a known,
    resumable session id — the previous design only ever learned the id by
    reading it back out of that envelope, which a kill never produces."""

    def _mint(self, tmp_path: Path, *, resume_session: str = "") -> dict:
        src = RALPH.read_text()
        start = src.index('if [[ -n "$RESUME_SESSION" ]]; then')
        end = src.index('\n    fi\n', start) + len('\n    fi\n')
        block = src[start:end]
        prog = (
            "set -u\n"
            f"RESUME_SESSION={resume_session!r}\nRESUME_ITER=2\n"
            "TASK='do the ticket'\nRALPH_INSTRUCTIONS='instructions'\n"
            "SESSION_ID=''\nRESUME_FLAG=()\nlog() { :; }\n"
            f"{block}\n"
            'printf "SESSION_ID=%s\\n" "$SESSION_ID"\n'
            'printf "RESUME_FLAG=%s\\n" "${RESUME_FLAG[*]:-}"\n'
        )
        r = _bash(prog)
        assert r.returncode == 0, r.stderr
        out = {}
        for line in r.stdout.splitlines():
            k, _, v = line.partition("=")
            out[k] = v
        return out

    def test_a_fresh_run_mints_a_session_id_and_passes_it_up_front(self, tmp_path):
        out = self._mint(tmp_path)
        assert out["SESSION_ID"], "no session id was minted for a fresh run"
        # A real uuid4, not a placeholder — four hyphen-separated groups.
        assert len(out["SESSION_ID"].split("-")) == 5
        assert out["RESUME_FLAG"] == f"--session-id {out['SESSION_ID']}"

    def test_a_budget_cap_resume_is_unaffected(self, tmp_path):
        """The pre-existing --resume-session path (70860d58) must not be
        disturbed by minting: RESUME_SESSION wins outright."""
        out = self._mint(tmp_path, resume_session="paused-sess-abc")
        assert out["SESSION_ID"] == "paused-sess-abc"
        assert out["RESUME_FLAG"] == "--resume paused-sess-abc"

    def test_the_cli_invocation_spreads_resume_flag(self):
        """Wiring check: the actual claude spawn must use the array this block
        fills in, not a separate/parallel flag construction."""
        src = RALPH.read_text()
        i = src.index('SCOPED_EXEC=(')
        invocation = src[i:src.index('CLAUDE_EXIT=$?', i)]
        assert '"${RESUME_FLAG[@]}"' in invocation


class TestTheCallerRoutesCommittedWorkToReview:
    """project_ralph.sh's `1|2` leg: COMMITTED decides between "burn the
    attempt" and "park it, restore the attempt, open a Change for review" —
    the exact distinction f8c7d271 shows was missing."""

    def _drive(self, tmp_path: Path, *, committed: int, change_id: str = "chg-1",
              wt_mode: int = 1) -> tuple[dict, list[str], list[dict]]:
        calls_file = tmp_path / "calls.log"
        changes_file = tmp_path / "changes.jsonl"
        fake_ptasks = tmp_path / "fake_ptasks.py"
        fake_ptasks.write_text(
            "import sys\n"
            f"open({str(calls_file)!r}, 'a').write('PTASKS_NOTE ' + ' '.join(sys.argv[1:]) + chr(10))\n"
            "print('{}')\n")
        # Records its call and, if given a non-empty id, appends one JSON line
        # to $CHANGES via a small python helper — avoids hand-quoting JSON
        # inside a bash printf format string.
        open_change_fn = "\n".join([
            "open_change_for_task() {",
            "  printf 'open_change_for_task %s %s %s\\n' \"$1\" \"$2\" \"$3\" >> \"$CALLS\"",
            "  if [[ -n \"" + change_id + "\" ]]; then",
            "    python3 -c \"import json,sys; open(sys.argv[1],'a').write(json.dumps({'branch':sys.argv[2],'task':sys.argv[3],'title':sys.argv[4]})+chr(10))\" "
            "\"$CHANGES\" \"$1\" \"$2\" \"$3\"",
            "  fi",
            "  echo \"" + change_id + "\"",
            "}",
        ])
        prog = (
            "set -u\n"
            f"CALLS={str(calls_file)!r}\n"
            f"CHANGES={str(changes_file)!r}\n"
            f"PTASKS={str(fake_ptasks)!r}\n"
            f"COMMITTED={committed}\nWT_MODE={wt_mode}\nWT_BRANCH_SLUG=feature-x\n"
            "WT_SLUG=proj\nTASK_ID=t1\nPR_TITLE='fix the thing'\nRC=1\n"
            "CHILD_JOB=job-9\nEVIDENCE='{}'\nDIR=/tmp\nLOG=/dev/null\n"
            "CLAIM_TOKEN=tok\nATTEMPTS=3\nMAX_ATTEMPTS=3\n"
            "PARKED=0\nFAILED=0\nBLOCKED=0\n"
            "log() { :; }\n"
            f"{open_change_fn}\n"
            "record_change_gates() { echo \"record_change_gates $1\" >> \"$CALLS\"; }\n"
            "record_claim_check() { echo \"record_claim_check $1\" >> \"$CALLS\"; }\n"
            "note_unmerged_branch() { echo 'note_unmerged_branch CALLED' >> \"$CALLS\"; }\n"
            "release_or_block() { echo 'release_or_block CALLED' >> \"$CALLS\"; echo retry; }\n"
            "job_finish_child() { echo \"job_finish_child $*\" >> \"$CALLS\"; }\n"
            "ptasks_tx() { echo \"ptasks_tx $*\" >> \"$CALLS\"; return 0; }\n"
            f"{_committed_leg()}\n"
            'echo "PARKED=$PARKED FAILED=$FAILED BLOCKED=$BLOCKED"\n'
        )
        r = _bash(prog)
        assert r.returncode == 0, r.stderr
        counters = dict(kv.split("=") for kv in r.stdout.strip().splitlines()[-1].split())
        calls = calls_file.read_text().splitlines() if calls_file.exists() else []
        changes = ([json.loads(l) for l in changes_file.read_text().splitlines()]
                  if changes_file.exists() else [])
        return counters, calls, changes

    def test_committed_work_is_parked_with_the_attempt_restored(self, tmp_path):
        counters, calls, _ = self._drive(tmp_path, committed=1)
        assert counters["PARKED"] == "1"
        assert counters["FAILED"] == "0", (
            "a run that committed real work must not count as a failed attempt")
        assert counters["BLOCKED"] == "0"
        assert any("release_or_block CALLED" not in c for c in calls)
        assert not any("release_or_block" in c for c in calls), (
            "COMMITTED work must never go through the attempt-burning path")
        assert any("ptasks_tx set-status" in c and "parked" in c and "--restore-attempt" in c
                   for c in calls), calls
        assert any("job_finish_child job-9 cancelled" in c for c in calls), calls

    def test_committed_work_opens_a_change(self, tmp_path):
        """The ticket's own acceptance line: "...a Change opened, not burned."""
        counters, calls, changes = self._drive(tmp_path, committed=1)
        assert len(changes) == 1, changes
        assert changes[0]["branch"] == "feature-x"
        assert changes[0]["task"] == "t1"
        assert any("record_change_gates chg-1" in c for c in calls)
        assert any("record_claim_check chg-1" in c for c in calls)
        assert not any("note_unmerged_branch" in c for c in calls), (
            "once a change is opened, the stale 'did not reach the publish path' "
            "note must not also be written")

    def test_uncommitted_work_is_unaffected__still_a_real_failure(self, tmp_path):
        """COMMITTED == 0 must be byte-identical to the pre-fix behaviour: this
        is a run that genuinely produced nothing, and the fix must not soften
        that case."""
        counters, calls, changes = self._drive(tmp_path, committed=0)
        assert counters["FAILED"] == "1"
        assert counters["PARKED"] == "0"
        assert changes == []
        assert any("release_or_block CALLED" in c for c in calls)
        assert any("job_finish_child job-9 failed" in c for c in calls)
        assert not any("ptasks_tx" in c for c in calls), (
            "the uncommitted path has no Change to open and must not call ptasks_tx")

    def test_a_failed_change_open_falls_back_to_the_branch_note(self, tmp_path):
        """open_change_for_task is best-effort (not in worktree mode, or the
        DB write failed): the work is still parked and restored, just without
        a Change — never silently dropped."""
        counters, calls, changes = self._drive(tmp_path, committed=1, change_id="")
        assert counters["PARKED"] == "1"
        assert counters["FAILED"] == "0"
        assert changes == []
        assert any("note_unmerged_branch CALLED" in c for c in calls)
        assert any("ptasks_tx set-status" in c and "parked" in c for c in calls)


class TestTheContractIsDocumented:
    def test_the_severed_run_shape_is_explained_near_the_exit_codes(self):
        header = RALPH.read_text()[:6000]
        assert "never reaches the reviewer" in header
        assert "COMMITTED" in header

    def test_the_committed_leg_names_the_ticket_it_fixes(self):
        src = SWEEP.read_text()
        i = src.index("\n    1|2)\n")
        leg = src[i:src.index("\n    *)\n", i)]
        assert "f8c7d271" in leg
        assert "open_change_for_task" in leg
        assert "--restore-attempt" in leg
