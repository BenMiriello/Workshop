"""The run's tree lease, from the side that actually holds trees: bash.

`services/run_worktree.py` is exercised in `test_run_worktree.py`. The whole
risk in THIS step is the other half: that the bash the sweep runs does something
different from the python it shells into. So these source
`scripts/tree_lease.sh` -- the exact file `project_ralph.sh` and `ralph.sh`
source -- and run its functions against a stub CLI, the same shape
`test_run_control.py` uses for `scripts/sweep_control.sh`.

What must be true:

  * taking a tree records the subject AND the generation, because the release is
    fenced on the generation and a release without one is a release that cannot
    be refused;
  * a tree somebody else holds propagates exit 1 and leaves the caller holding
    nothing -- never a second writer;
  * "there is no lease store" (exit 3) is NOT a refusal: a file-backed project
    tree with no Postgres near it sweeps as it always did;
  * release is idempotent, so it can sit on an EXIT trap AND on the one choke
    point every terminal leg goes through;
  * both callers actually wire it: the trap, the choke point, and the
    `--session-id` the confined spawn presents.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TREE_LEASE = REPO / "scripts" / "tree_lease.sh"


def _stub(tmp_path: Path, *, enter_exit: int = 0,
          enter_out: str = '{"subject_id": "SUBJ-1", "generation": 4}') -> Path:
    """A stand-in for `scripts/run_lease.py` that records how it was called."""
    p = tmp_path / "run_lease_stub.py"
    p.write_text(
        "import sys, os, time\n"
        f"log = open({str(tmp_path / 'calls.log')!r}, 'a')\n"
        "log.write(' '.join(sys.argv[1:]) + '\\n'); log.flush()\n"
        "verb = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "if verb == 'enter':\n"
        f"    print({enter_out!r})\n"
        f"    sys.exit({enter_exit})\n"
        "if verb == 'beat':\n"
        "    time.sleep(600)\n"
        "print('{}')\n"
    )
    return p


def _run(tmp_path: Path, body: str, stub: Path) -> subprocess.CompletedProcess:
    script = f'''
set -u
export WORKSHOP={REPO}
export RUN_LEASE_PY={stub}
source {TREE_LEASE}
{body}
'''
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=60)


def _calls(tmp_path: Path) -> list[str]:
    f = tmp_path / "calls.log"
    return f.read_text().splitlines() if f.exists() else []


class TestTakingATree:

    def test_it_records_the_subject_and_the_generation(self, tmp_path):
        p = _run(tmp_path, '''
tree_lease_take run-1 /some/tree
echo "SUBJECT=$TREE_SUBJECT"
echo "GENERATION=$TREE_GENERATION"
echo "BEATING=$([[ -n "$TREE_BEAT_PID" ]] && echo yes || echo no)"
tree_lease_release
''', _stub(tmp_path))
        assert p.returncode == 0, p.stderr
        assert "SUBJECT=SUBJ-1" in p.stdout
        assert "GENERATION=4" in p.stdout
        # THE RENEWAL HAS TO EXIST. Nothing in a sweep ticks during a
        # `claude -p` iteration bounded at 1500s against a 90s TTL.
        assert "BEATING=yes" in p.stdout

    def test_the_beater_is_told_who_to_watch(self, tmp_path):
        """It watches the caller's pid so a SIGKILLed sweep stops renewing a
        lease on a tree nobody is in."""
        # sleep, because the beater is a background child and the assertion is
        # about what it was TOLD, which it records when it starts.
        _run(tmp_path, 'tree_lease_take run-1 /some/tree\nsleep 1\ntree_lease_release',
             _stub(tmp_path))
        beat = [c for c in _calls(tmp_path) if c.startswith("beat")]
        assert beat, _calls(tmp_path)
        assert "--subject-id SUBJ-1" in beat[0]
        assert "--generation 4" in beat[0]
        assert "--parent-pid" in beat[0]

    def test_a_tree_somebody_else_holds_is_refused(self, tmp_path):
        p = _run(tmp_path, '''
tree_lease_take run-1 /some/tree; rc=$?
echo "RC=$rc"
echo "SUBJECT=[$TREE_SUBJECT]"
''', _stub(tmp_path, enter_exit=1, enter_out='{"blocked_by": {"holder_id": "run-0"}}'))
        assert "RC=1" in p.stdout
        # Holding nothing is the point: the caller must not write there.
        assert "SUBJECT=[]" in p.stdout
        assert not [c for c in _calls(tmp_path) if c.startswith("beat")]

    def test_no_lease_store_is_not_a_refusal(self, tmp_path):
        p = _run(tmp_path, '''
tree_lease_take run-1 /some/tree; rc=$?
echo "RC=$rc"
''', _stub(tmp_path, enter_exit=3, enter_out='{"error": "no lease store"}'))
        assert "RC=3" in p.stdout


class TestGivingItBack:

    def test_release_is_fenced_on_the_generation_it_took(self, tmp_path):
        _run(tmp_path, '''
tree_lease_take run-1 /some/tree
tree_lease_release
''', _stub(tmp_path))
        rel = [c for c in _calls(tmp_path) if c.startswith("release")]
        assert rel, _calls(tmp_path)
        assert "--subject-id SUBJ-1" in rel[0] and "--generation 4" in rel[0]

    def test_release_is_idempotent(self, tmp_path):
        """It sits on an EXIT trap and on every terminal leg, so it is called
        more than once by design."""
        p = _run(tmp_path, '''
tree_lease_take run-1 /some/tree
tree_lease_release
tree_lease_release
tree_lease_release
echo "OK"
''', _stub(tmp_path))
        assert "OK" in p.stdout
        assert len([c for c in _calls(tmp_path) if c.startswith("release")]) == 1

    def test_release_with_nothing_held_says_nothing_to_the_store(self, tmp_path):
        p = _run(tmp_path, 'tree_lease_release; echo OK', _stub(tmp_path))
        assert "OK" in p.stdout
        assert _calls(tmp_path) == []


class TestBothCallersAreWired:
    """A helper nothing sources is a helper that does not run."""

    def test_the_sweep_sources_it_and_releases_on_every_exit(self):
        src = (REPO / "scripts" / "project_ralph.sh").read_text()
        assert 'source "$WORKSHOP/scripts/tree_lease.sh"' in src
        assert "trap 'revoke_grant; release_presence; tree_lease_release' EXIT" in src
        # The choke point every terminal leg of a task goes through.
        jfc = src.split("job_finish_child() {", 1)[1].split("\n}", 1)[0]
        assert "tree_lease_release" in jfc

    def test_the_sweep_takes_the_tree_it_was_handed(self):
        src = (REPO / "scripts" / "project_ralph.sh").read_text()
        assert 'tree_lease_take "$CHILD_JOB" "$WT_DIR"' in src

    def test_the_worker_presents_its_session_id_to_the_confined_spawn(self):
        """Without it the turn carries no session identity and
        `tree_access.decide` correctly answers "this turn holds nothing" --
        which, with the flag on, is read-only inside the run's OWN worktree."""
        src = (REPO / "scripts" / "ralph.sh").read_text()
        assert '--session-id "$PRESENCE_ID"' in src
        assert 'source "$WORKSHOP/scripts/tree_lease.sh"' in src
        assert "trap 'release_presence; tree_lease_release' EXIT" in src

    def test_the_worktree_shells_use_the_backend_venv(self):
        """`services.worktrees` imports sqlalchemy, which the python3 a sweep
        runs with does not have. Measured: from 2026-08-25 every allocation
        died at import and every unattended run fell back to the SHARED tree."""
        src = (REPO / "scripts" / "project_ralph.sh").read_text()
        assert 'WT_PY="$WORKSHOP/backend/.venv/bin/python"' in src
        assert 'PYTHONPATH="$WORKSHOP/backend" python3 - "$WT_SLUG"' not in src
