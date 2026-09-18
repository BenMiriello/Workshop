"""A run stops AT the usage cap and resumes without redoing the work.

Ticket 70860d58, priority 100. The acceptance sentence is: "with a cap set low
enough to bite mid-run, a task commits and pauses instead of being parked with
its work discarded, and a later sweep resumes and finishes it without repeating
the committed work."

THESE RUN THE REAL SHELL. The behaviour lives in `scripts/ralph.sh` and
`scripts/project_ralph.sh`, and a Python restatement of it could stay green
while the scripts drifted — which is the failure mode this file exists to catch.
Same approach, and the same `_extract` helper, as
`test_a_displaced_worker_stops.py` and `test_sweep_billing_scope.py`.

What is NOT covered here, stated rather than implied: no test drives a real
`claude` process. The pause DECISION, the commit, the bookmark and the resume
wiring are all exercised against the real shell; whether a paused model session
is still resumable hours later is a property of the CLI, not of this code, and
the design says so — resume is best effort with the committed branch as the
fallback.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
RALPH = SCRIPTS / "ralph.sh"
SWEEP = SCRIPTS / "project_ralph.sh"

PAUSE_RC = 8


def _extract(script: Path, name: str) -> str:
    """One shell function's source, by name.

    ANCHORED AT THE START OF A LINE, unlike the copy in
    test_a_displaced_worker_stops.py. A bare `src.index("pause_at_cap() {")`
    matches `should_pause_at_cap() {` first, which is a real function in this
    file, and the test then runs the wrong body and fails on an unrelated unbound
    variable. Cost twenty minutes; anchoring is one character."""
    src = script.read_text()
    start = src.index(f"\n{name}() {{") + 1
    end = src.index("\n}\n", start) + len("\n}\n")
    return src[start:end]


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


def _fake_remaining_cli(tmp_path: Path, payload: str) -> Path:
    """Stand in for `python3 -m services.usage_ledger remaining`.

    The real module is exercised by test_usage_ledger_components.py; what is
    under test HERE is the shell's decision given an answer."""
    p = tmp_path / "fake_ledger.py"
    p.write_text("import sys\nprint(%r)\n" % payload)
    return p


def _should_pause(tmp_path: Path, *, remaining: str, iter_n: int = 2,
                  iter_tokens: int = 100_000, run_tokens: int = 500_000,
                  bill_project: str = "wstest-scope") -> int:
    """Run ralph.sh's own should_pause_at_cap with a stubbed ledger."""
    fake = _fake_remaining_cli(tmp_path, remaining)
    fn = _extract(RALPH, "should_pause_at_cap")
    prog = (
        "set -u\n"
        f"WORKSHOP={str(tmp_path)!r}\n"
        f"BILL_PROJECT={bill_project!r}\n"
        f"iter={iter_n}\nITER_TOKENS={iter_tokens}\nRUN_TOKENS={run_tokens}\n"
        # Shadow the module invocation with the stub. The function shells out as
        # `python3 -m services.usage_ledger remaining ...`; this replaces the
        # interpreter for that one call.
        f"python3() {{ if [[ \"${{1:-}}\" == -m ]]; then command python3 {str(fake)!r}; "
        "else command python3 \"$@\"; fi; }\n"
        f"{fn}\n"
        "if should_pause_at_cap; then echo \"PAUSE $BUDGET_LEFT\"; exit 0; "
        "else echo \"GO $BUDGET_LEFT\"; exit 1; fi\n"
    )
    return _bash(prog)


class TestTheDecision:
    def test_it_pauses_when_the_next_step_would_not_fit(self, tmp_path):
        r = _should_pause(tmp_path, remaining='{"remaining": 40000}',
                          iter_tokens=100_000)
        assert r.returncode == 0 and r.stdout.startswith("PAUSE 40000")

    def test_it_carries_on_when_the_next_step_fits(self, tmp_path):
        r = _should_pause(tmp_path, remaining='{"remaining": 400000}',
                          iter_tokens=100_000)
        assert r.returncode == 1 and r.stdout.startswith("GO")

    def test_it_never_pauses_on_iteration_one(self, tmp_path):
        """There is no previous iteration to predict from, and admission has
        already gated the start. Pausing here would refuse a run the gate just
        admitted, which is a deadlock, not a cap."""
        r = _should_pause(tmp_path, remaining='{"remaining": 1}', iter_n=1)
        assert r.returncode == 1

    def test_an_unreadable_answer_does_not_stop_the_queue(self, tmp_path):
        """A metering failure that silently parked every ticket would be far
        worse than an overshoot bounded by one iteration."""
        for junk in ("not json", "", "null", "[]"):
            r = _should_pause(tmp_path, remaining=junk)
            assert r.returncode == 1, f"paused on unreadable answer {junk!r}"

    def test_an_uncapped_config_does_not_pause(self, tmp_path):
        r = _should_pause(tmp_path,
                          remaining='{"uncapped": true, "remaining": null}')
        assert r.returncode == 1

    def test_a_manual_ralph_with_no_billing_scope_has_no_cap(self, tmp_path):
        r = _should_pause(tmp_path, remaining='{"remaining": 1}',
                          bill_project="")
        assert r.returncode == 1

    def test_the_boundary_is_would_not_fit_not_is_empty(self, tmp_path):
        """Equal remaining and step cost must NOT pause: the step exactly fits,
        and stopping there wastes the last of the budget."""
        assert _should_pause(tmp_path, remaining='{"remaining": 100000}',
                             iter_tokens=100_000).returncode == 1
        assert _should_pause(tmp_path, remaining='{"remaining": 99999}',
                             iter_tokens=100_000).returncode == 0


class TestThePause:
    def _run_pause(self, tmp_path: Path, *, dirty: str | None = "new_file.py",
                   touches: str = "*.py", foreign: str | None = None
                   ) -> tuple[subprocess.CompletedProcess, Path, Path]:
        tree = _git_repo(tmp_path)
        notes = tmp_path / "notes.jsonl"
        fake_ptasks = tmp_path / "fake_ptasks.py"
        fake_ptasks.write_text(
            "import json, sys\n"
            "a = sys.argv[1:]\n"
            "if a and a[0] == 'list-json':\n"
            f"    print(json.dumps([{{'id': 't1', 'touches': {touches!r}.split(',')}}]))\n"
            "elif a and a[0] == 'note':\n"
            "    kind = a[a.index('--kind') + 1] if '--kind' in a else 'note'\n"
            "    ref = a[a.index('--ref') + 1] if '--ref' in a else ''\n"
            f"    open({str(notes)!r}, 'a').write("
            "        json.dumps({'kind': kind, 'ref': ref, 'text': a[3]}) + chr(10))\n"
            "    print('{}')\n")
        if dirty:
            (tree / dirty).write_text("print('work the worker did')\n")
        if foreign:
            (tree / foreign).write_text("someone else's edit, mid-sweep\n")
        prog = (
            "set -u\n"
            f"DIR={str(tree)!r}\n"
            "BASELINE=$(cd \"$DIR\" && git rev-parse HEAD)\n"
            "PRERUN_DIRTY=\"\"\n"
            "BOOKKEEPING_RE='^(\\.tasks\\.json|\\.runtime/)'\n"
            "TASK='make the thing'\nTASK_ID=t1\nSTORE_DIR=/tmp\n"
            "SESSION_ID=sess-abc123\nBILL_PROJECT=wstest-scope\nRUN_ID=job-9\n"
            "iter=4\nRUN_TOKENS=900000\nLOG=/dev/null\n"
            f"PTASKS={str(fake_ptasks)!r}\n"
            "log() { echo \"[log] $*\"; }\n"
            "accum_worker_commits() { :; }\n"
            f"{_extract(RALPH, 'commit_lane')}\n"
            f"{_extract(RALPH, 'pause_at_cap')}\n"
            "pause_at_cap 40000\n"
            "echo \"PAUSED=$PAUSED BRANCH=$PAUSE_BRANCH ITER=$PAUSE_ITER\"\n"
        )
        return _bash(prog), tree, notes

    def test_it_commits_the_work_inside_the_declared_lane(self, tmp_path):
        r, tree, _ = self._run_pause(tmp_path)
        assert r.returncode == 0, r.stderr
        log = subprocess.run(["git", "log", "--oneline"], cwd=tree,
                             capture_output=True, text=True).stdout
        assert "paused at the usage cap" in log
        # The file the worker wrote is IN the commit, not left dirty.
        show = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"],
                              cwd=tree, capture_output=True, text=True).stdout
        assert "new_file.py" in show
        status = subprocess.run(["git", "status", "--porcelain"], cwd=tree,
                                capture_output=True, text=True).stdout
        assert status.strip() == "", f"work left uncommitted: {status!r}"

    def test_it_stays_inside_the_lane_and_leaves_other_files_alone(self, tmp_path):
        """maybe_autocommit's lane discipline is REUSED, and its approval guard
        is not. A shared tree makes 'newly dirty' insufficient evidence that
        this run wrote a file: an operator or a concurrent session editing
        alongside the sweep shows up as newly dirty too, and committing their
        work under the worker's name happened once already, on 2026-07-18.

        The lane here is `*.py`; the foreign edit is a `.md`."""
        r, tree, _ = self._run_pause(tmp_path, dirty="new_file.py",
                                     touches="*.py",
                                     foreign="not_mine.md")
        assert r.returncode == 0, r.stderr
        show = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"],
                              cwd=tree, capture_output=True, text=True).stdout
        assert "new_file.py" in show
        assert "not_mine.md" not in show, (
            "the pause committed a file outside the task's declared lane")
        status = subprocess.run(["git", "status", "--porcelain"], cwd=tree,
                                capture_output=True, text=True).stdout
        assert "not_mine.md" in status, "the foreign edit was not left in place"

    def test_it_records_the_session_the_branch_and_the_iteration(self, tmp_path):
        """The three fields the ticket names, as a machine-readable bookmark."""
        r, _, notes = self._run_pause(tmp_path)
        assert r.returncode == 0, r.stderr
        entries = [json.loads(x) for x in notes.read_text().splitlines() if x]
        resume = [e for e in entries if e["kind"] == "resume"]
        assert len(resume) == 1, entries
        mark = json.loads(resume[0]["ref"])
        assert mark["session"] == "sess-abc123"
        assert mark["branch"] == "main"
        # `iter` was already incremented for the iteration that will NOT run, so
        # the iteration REACHED is one less. Overstating it would make a resumed
        # run look further along than it is.
        assert mark["iteration"] == 3
        assert mark["scope"] == "wstest-scope"
        assert mark["run"] == "job-9"
        assert mark["run_tokens"] == 900_000
        assert mark["remaining_at_pause"] == 40_000
        assert mark["head"], "the bookmark does not name the commit it left"

    def test_a_pause_with_nothing_to_commit_still_writes_the_bookmark(self, tmp_path):
        """Otherwise a run that paused between commits looks like a thrash-park
        and its live session is thrown away."""
        r, _, notes = self._run_pause(tmp_path, dirty=None)
        assert r.returncode == 0, r.stderr
        entries = [json.loads(x) for x in notes.read_text().splitlines() if x]
        assert any(e["kind"] == "resume" for e in entries)


class TestTheResumeSelection:
    def _candidate(self, tmp_path: Path, tasks: list) -> str:
        fake = tmp_path / "fake_ptasks.py"
        fake.write_text("import json, sys\nprint(json.dumps(%r))\n" % (tasks,))
        prog = (
            "set -u\n"
            f"DIR=/tmp\nLOG=/dev/null\nPTASKS={str(fake)!r}\n"
            f"{_extract(SWEEP, 'resume_candidate')}\n"
            "resume_candidate\n"
        )
        return _bash(prog).stdout.strip()

    def _mark(self, **kw):
        base = {"session": "s1", "branch": "wt/p/feature", "iteration": 3,
                "head": "abc"}
        base.update(kw)
        return json.dumps(base)

    def test_a_parked_ticket_with_a_bookmark_is_picked_up(self, tmp_path):
        out = self._candidate(tmp_path, [
            {"id": "t1", "status": "parked",
             "evidence": [{"kind": "resume", "ts": "2026-09-04T01:00:00+00:00",
                           "ref": self._mark()}]},
        ])
        got = json.loads(out)
        assert got["id"] == "t1" and got["session"] == "s1"
        assert got["branch"] == "wt/p/feature" and got["iteration"] == 3

    def test_a_thrash_park_is_not_mistaken_for_a_pause(self, tmp_path):
        """This is the distinction the whole 'no ninth status token' decision
        rests on: `parked` means both things, and the bookmark is the only thing
        that tells them apart."""
        assert self._candidate(tmp_path, [
            {"id": "t1", "status": "parked",
             "evidence": [{"kind": "acceptance", "ts": "2026-09-04T01:00:00+00:00",
                           "ref": "no progress within the run"}]},
        ]) == ""

    def test_a_bookmark_that_has_been_worked_since_is_spent(self, tmp_path):
        """Evidence written AFTER the resume entry means the ticket was worked
        again, so resuming that session would replay a stale run."""
        assert self._candidate(tmp_path, [
            {"id": "t1", "status": "parked", "evidence": [
                {"kind": "resume", "ts": "2026-09-04T01:00:00+00:00",
                 "ref": self._mark()},
                {"kind": "receipt", "ts": "2026-09-04T02:00:00+00:00",
                 "ref": "job-2"},
            ]},
        ]) == ""

    def test_a_ticket_that_is_not_parked_is_not_resumed(self, tmp_path):
        assert self._candidate(tmp_path, [
            {"id": "t1", "status": "doing",
             "evidence": [{"kind": "resume", "ts": "2026-09-04T01:00:00+00:00",
                           "ref": self._mark()}]},
        ]) == ""

    def test_the_oldest_pause_goes_first(self, tmp_path):
        """A paused session decays: the longer it waits the likelier it is to be
        unresumable, and the session still being there is the whole value."""
        got = json.loads(self._candidate(tmp_path, [
            {"id": "new", "status": "parked",
             "evidence": [{"kind": "resume", "ts": "2026-09-04T05:00:00+00:00",
                           "ref": self._mark(session="s-new")}]},
            {"id": "old", "status": "parked",
             "evidence": [{"kind": "resume", "ts": "2026-09-04T01:00:00+00:00",
                           "ref": self._mark(session="s-old")}]},
        ]))
        assert got["id"] == "old"

    def test_junk_in_the_bookmark_does_not_crash_the_sweep(self, tmp_path):
        out = self._candidate(tmp_path, [
            {"id": "t1", "status": "parked",
             "evidence": [{"kind": "resume", "ts": "2026-09-04T01:00:00+00:00",
                           "ref": "not json at all"}]},
        ])
        # It still selects (the ticket IS paused); the fields are just empty, so
        # the run reattaches by branch or starts clean rather than blowing up.
        assert json.loads(out)["id"] == "t1"


class TestTheResumedRunContinues:
    def test_iteration_one_resumes_the_recorded_session(self):
        """A resumed run must not restart from the baseline. The proof that it
        does not is `--resume <id>` on iteration 1 plus a prompt that tells the
        worker its previous work is committed."""
        src = RALPH.read_text()
        block = src[src.index("if (( iter == 1 )); then"):
                    src.index("# Capture stdout for sentinel")]
        assert 'RESUME_FLAG=(--resume "$RESUME_SESSION")' in block
        assert "PAUSED at the usage budget cap" in block
        assert "continue from there rather than starting over" in block

    def test_the_sweep_passes_the_bookmark_into_ralph(self):
        src = SWEEP.read_text()
        assert '--resume-session "$RESUME_SESSION"' in src
        assert '"${RESUME_FLAGS[@]}"' in src

    def test_the_sweep_reattaches_the_paused_branch_instead_of_re_branching(self):
        """`branch_slug_for_work` appends a suffix on collision, so
        re-allocating for the same ticket mints `<name>-2` off the base branch
        and strands the paused commits on a branch nothing references — the
        eleven-unfindable-branches failure of 2026-08-23."""
        src = SWEEP.read_text()
        i = src.index('if [[ "$WT_ENABLED" == "1" ]]; then')
        alloc = src.index('[[ -z "$ALLOC" ]] && ALLOC=$(alloc_task_worktree', i)
        # THE ORDER IS THE PROPERTY, not a byte offset. This read a fixed
        # 2000-character window and asserted the allocation line was inside it,
        # which broke on 2026-09-18 when a SECOND reattach path (the ordinary
        # retry, off the ticket's own worktree evidence) was added ahead of it
        # and pushed the line to offset 2386. The behaviour was not merely
        # intact, it was stronger -- so the test failed for growing in the
        # direction it exists to encourage. Widening the window would just
        # reset that trap for the next reattach path.
        reattach = src[i:alloc]
        assert "RESUME_BRANCH" in reattach
        assert "branch_dir_for" in reattach
        # Allocation is the FALLBACK: every reattach attempt precedes it, and
        # it runs only when none of them produced a tree.
        assert 'ALLOC=""' in reattach

    def test_a_resumed_run_recaptures_the_session_id(self):
        """`claude --resume` may answer with a different id. Keeping the seeded
        one would write a stale id into the NEXT pause bookmark, and the resume
        after that would target a session this run never used."""
        src = RALPH.read_text()
        assert 'if [[ -z "$SESSION_ID" ]] || (( iter == 1 )); then' in src
        assert 'SESSION_ID="$CAPTURED_SESSION"' in src


class TestTheCallerHandlesExitEight:
    def test_the_sweep_has_an_explicit_leg_for_8(self):
        """Without one the `*)` default release-or-blocks an unknown code as a
        plain failure, so an unwired 8 fails SILENTLY rather than loudly."""
        src = SWEEP.read_text()
        i = src.index("\n    8)\n")
        leg = src[i:src.index("\n    9)\n", i)]
        # Not a failure: the attempt comes back and the job is cancelled, not
        # failed, so the circuit breaker does not cool a scope off for running
        # out of budget — the one thing guaranteed to happen again.
        assert "--restore-attempt" in leg
        assert 'job_finish_child "$CHILD_JOB" cancelled' in leg
        assert "FAILED=$((FAILED+1))" not in leg
        assert 'set-status "$DIR" "$TASK_ID" parked' in leg
        # And it stops the sweep: the cap is scope-wide, so the next ticket
        # would meet the same wall.
        assert "HALT_REASON=" in leg and "break ;;" in leg

    def test_exit_eight_is_documented_as_the_pause_not_as_reserved(self):
        src = RALPH.read_text()
        header = src[:src.index("set -u")]
        assert "BUDGET-CAP PAUSE" in header
        assert "RESERVED for the budget-cap pause" not in header, (
            "the contract block still says 8 is reserved rather than used")

    def test_no_ninth_status_token_was_added(self):
        """The ticket makes this decision explicitly: the eight statuses live in
        five places and adding one for a single bit of information is a
        separate, larger change."""
        from services import tasks
        assert len(tasks.STATUSES) == 8, tasks.STATUSES
        assert "paused" not in tasks.STATUSES
