"""Machine-gate results, attached to the change they judged.

Step C3 of `docs/platform-redesign/changes-and-review-2026-08-22.md`, whose
§5.3 rule is machine gates first and human attention last. The claim under test
is not "a dict round-trips" -- `test_changes.py` already pins that -- it is that
a REAL `reviewer.sh` run, driven the way the sweep drives it, ends up on the
change with its per-check outcomes legible to a person.

So the reviewer is actually executed here rather than faked. A hand-written
payload would pass forever while the script renamed a key underneath it, and
the whole value of this step is that the names and statuses on the change are
the ones the checkers really produced. The two gates asserted on are chosen to
be deterministic on any tree: a committed stub marker always fails
`no_new_stubs`, and an executed acceptance criterion that passes always passes
`acceptance_criteria`. Nothing here depends on whether the workshop tree itself
is currently green.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[2]
REVIEWER = REPO / "scripts" / "reviewer.sh"

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.test",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.test",
}

# A criterion the sweep already executed, in the shape project_ralph.sh writes
# to REVIEWER_CRITERIA_FILE. `true` so the gate it produces is a PASS wherever
# this suite runs.
_PASSING_CRITERION = [{"criterion": "the change carries its gates",
                       "cmd": "true", "expect": "exit0", "pass": True,
                       "evidence": "exit=0"}]

# A canonical stub marker. reviewer.sh fails `new_stubs` on it, unconditionally
# and on every tree, which is what makes the FAILING gate below deterministic.
#
# ASSEMBLED, not written out, for the same reason `scripts/done_gate.py` spells
# its own pattern table in halves: the stub scan reads the whole working-tree
# text of every CHANGED file, so a literal here made this test module
# unmaintainable -- any edit to it failed the gate on a marker the file exists
# to fabricate. Hit on 2026-09-04. The assembled value is byte-identical, which
# `test_the_fixture_is_still_a_real_stub_marker` below asserts.
_STUBBED_SOURCE = "def unfinished():\n    # " + "TODO" + ": finish this\n    return None\n"


def test_the_fixture_is_still_a_real_stub_marker():
    """The fixture is assembled rather than written out, so assert it still IS
    what it claims: `reviewer.sh` must fail `new_stubs` on this exact text, and
    a clever refactor of the concatenation that changed a byte would make every
    gate test below pass for the wrong reason."""
    assert _STUBBED_SOURCE == "def unfinished():\n    # TO" "DO: finish this\n    return None\n"
    import re
    assert re.search(r"\bTO" "DO:", _STUBBED_SOURCE), "no longer a canonical stub marker"


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(cwd), *args], check=True,
                         capture_output=True, text=True,
                         env={**os.environ, **_GIT_ENV})
    return res.stdout.strip()


def _run_reviewer(tree: Path, baseline: str = "main", **env: str) -> dict:
    """`reviewer.sh` over `tree`, exactly as the sweep invokes it.

    Returns the parsed JSON and asserts the script's own contract on the way
    through: it exits 0 and its stdout is one JSON object.
    """
    crit = tree / "criteria-results.json"
    crit.write_text(json.dumps(_PASSING_CRITERION))
    proc = subprocess.run(
        ["bash", str(REVIEWER), baseline], capture_output=True, text=True,
        env={**os.environ, **_GIT_ENV, "WORKSHOP": str(REPO),
             "REVIEWER_ROOT": str(tree),
             "REVIEWER_CRITERIA_FILE": str(crit), **env})
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _scratch_repo(path: Path) -> Path:
    """A throwaway git repo carrying one committed stub, so a reviewer run over
    it has something real to reject."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True,
                   capture_output=True, env={**os.environ, **_GIT_ENV})
    (path / "README.md").write_text("base\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-qm", "init")
    (path / "unfinished.py").write_text(_STUBBED_SOURCE)
    return path


# ---------------------------------------------------------------------------
# the translation: every check recorded, and a skip says why
# ---------------------------------------------------------------------------

def test_every_check_the_reviewer_ran_becomes_a_gate_that_says_what_it_found(
        tmp_path):
    """The reviewer's real output, mapped. No database needed for this half.

    The point of asserting on a LIVE run rather than a fixture: if a check is
    renamed in `reviewer.sh` and not here, the gate for it turns into "the
    reviewer reported no outcome", and that shows up as a failure right here
    instead of as a silently missing gate on the review surface.
    """
    from services.change_gates import GATE_CHECKS, gates_from_reviewer

    payload = _run_reviewer(_scratch_repo(tmp_path / "scratch"))
    gates = gates_from_reviewer(payload)
    by_name = {g["name"]: g for g in gates}

    # Every declared check is present, plus the reviewer's own verdict, the
    # stub scan and the executed criteria.
    for check in GATE_CHECKS:
        assert check.gate in by_name, f"{check.gate} was not recorded"
    assert {"reviewer", "no_new_stubs", "acceptance_criteria"} <= set(by_name)

    # The deterministic pair: one real failure, one real pass.
    assert by_name["no_new_stubs"]["status"] == "fail"
    assert "unfinished.py" in by_name["no_new_stubs"]["detail"]
    assert by_name["acceptance_criteria"]["status"] == "pass"
    assert by_name["reviewer"]["status"] == "fail"

    # A SKIP IS NOT AN ABSENCE, and it carries the precondition it did not
    # meet. This is the failure mode the step exists to avoid: a gate that is
    # missing and a gate that did not apply read identically to a person.
    skipped = [g for g in gates if g["status"] == "skipped"]
    assert skipped, "a scratch tree should skip most of the path-gated checks"
    # FOUR honest reasons a gate is quiet, and "retired" is the fourth, added
    # 2026-09-04. It differs from the other three in tense: they are facts about
    # THIS diff and may not hold next time, while a retirement is a fact about
    # the check and is permanent. Both still have to say so -- the failure this
    # loop exists to prevent is a gate that is quiet without a reason, and a
    # check deleted from the reviewer without a word here reads exactly like one
    # that ran and found nothing.
    for gate in skipped:
        assert gate["detail"], f"{gate['name']} skipped without saying why"
        assert ("did not apply" in gate["detail"]
                or "could not run" in gate["detail"]
                or "declared no machine-checkable" in gate["detail"]
                or gate["detail"].startswith("retired")), \
            gate["detail"]

    # Every status is one the column accepts, and every gate is stamped with
    # the reviewer's own run time rather than an invented one.
    from db.models import GATE_STATUSES
    for gate in gates:
        assert gate["status"] in GATE_STATUSES
        assert gate["at"] == payload["ts"]
        assert datetime.fromisoformat(gate["at"])


def test_a_check_that_could_not_run_is_not_a_check_that_did_not_apply(tmp_path):
    """`reviewer.sh`'s own distinction, preserved.

    "No .py file changed" is a non-question. "Python changed and there is no
    typechecker on this box" is the reviewer's honest "I don't know", and it is
    the thing a strict latitude blocks on. Recording both as a bare `skipped`
    would throw that away at exactly the surface where it matters.
    """
    from services.change_gates import gates_from_reviewer

    payload = _run_reviewer(_scratch_repo(tmp_path / "scratch"))
    payload["typecheck"]["py"] = "skipped"
    payload["ambiguous"] = ["py_typecheck_unavailable"]

    by_name = {g["name"]: g for g in gates_from_reviewer(payload)}
    assert by_name["typecheck:python"]["status"] == "skipped"
    assert "could not run (py_typecheck_unavailable)" in \
        by_name["typecheck:python"]["detail"]
    # ...while a genuinely inapplicable check still reads as inapplicable.
    assert "did not apply" in by_name["design_system"]["detail"]


def test_a_gate_write_cannot_fail_the_sweep(tmp_path):
    """The reviewer's verdict is already made; recording it is bookkeeping.

    Driven end to end through the script, because the contract being tested is
    the SCRIPT's: pointed at a branch that has no change open (and, on a box
    with no database, at no store at all), it must still exit 0 with the same
    verdict it would have produced with the gate block switched off.
    """
    tree = _scratch_repo(tmp_path / "scratch")
    plain = _run_reviewer(tree)
    routed = _run_reviewer(tree,
                           REVIEWER_GATE_PROJECT="no-such-project",
                           REVIEWER_GATE_BRANCH="no-such-branch")
    assert routed["verdict"] == plain["verdict"] == "reject"
    assert routed["complete"] is plain["complete"] is False
    assert routed["reasons"] == plain["reasons"]

    # Same for an id that resolves to nothing at all.
    byid = _run_reviewer(tree, REVIEWER_CHANGE_ID="00000000-0000-0000-0000-000000000000")
    assert byid["verdict"] == "reject"


def test_a_malformed_payload_is_reported_not_raised():
    from services import change_gates

    assert change_gates.record_reviewer_run("not a payload") is None
    assert change_gates.record_reviewer_run({}, change_id="") is None


# ---------------------------------------------------------------------------
# the write: onto a real change, back out through the route
# ---------------------------------------------------------------------------

pytest.importorskip("sqlalchemy")

_needs_db = pytest.mark.skipif(
    not os.environ.get("WORKSHOP_DATABASE_ADMIN_URL"),
    reason="no WORKSHOP_DATABASE_ADMIN_URL; the fixture mints a throwaway org, "
           "which the application role deliberately cannot do",
)

# The real git project + throwaway org, shared with the C1 suite rather than
# re-declared: two copies of a fixture that mints organizations is two things to
# keep in step, and the second one is always the one that rots.
from tests.test_changes import (_branch_with_commits, project,  # noqa: E402,F401
                                tenant)


@_needs_db
def test_a_sweep_run_lands_its_gates_on_the_change_and_they_come_back(
        project, tenant):
    """The whole step, in the order the sweep does it.

    A branch earns a change, the reviewer runs against that branch's tree, and
    `services/change_gates.py` -- reached through the same CLI entry point
    `project_ralph.sh` uses, receipt wrapper and all -- puts the outcomes on the
    change. Then a reviewer asks the API for the change and finds them, names,
    statuses and timestamps intact.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import changes as change_routes
    from services import change_gates, changes

    wt_dir = _branch_with_commits(project, "gets-gated")
    (wt_dir / "unfinished.py").write_text(_STUBBED_SOURCE)
    _git(wt_dir, "add", "unfinished.py")
    _git(wt_dir, "commit", "-qm", "add an unfinished helper")
    change = changes.open_change("proj", "gets-gated", title="Gets gated")
    assert change["gates"] == []

    payload = _run_reviewer(wt_dir)
    # The sweep hands over ralph's RECEIPT, which carries the reviewer verdict
    # verbatim under `.reviewer`; the entry point unwraps it.
    receipt = wt_dir / "receipt.json"
    receipt.write_text(json.dumps({"exit_code": 1, "reviewer": payload}))
    assert change_gates.main(["--change-id", change["id"],
                             "--input", str(receipt)]) == 0

    app = FastAPI()
    app.include_router(change_routes.router)
    client = TestClient(app)
    res = client.get(f"/changes/{change['id']}")
    assert res.status_code == 200, res.text
    gates = res.json()["gates"]
    by_name = {g["name"]: g for g in gates}

    # A FAILING gate and a PASSING gate, both from the real run, both here.
    assert by_name["no_new_stubs"]["status"] == "fail"
    assert "unfinished.py" in by_name["no_new_stubs"]["detail"]
    assert by_name["acceptance_criteria"]["status"] == "pass"
    assert by_name["reviewer"]["status"] == "fail"

    # Names, statuses and timestamps survive the column and the response model.
    assert len(by_name) == len(gates), "gate names collided"
    for gate in gates:
        assert gate["name"] and gate["status"]
        assert gate["at"] == payload["ts"]
        assert datetime.fromisoformat(gate["at"])

    # And a skipped check is still on the record, with its reason, rather than
    # quietly missing -- which is the whole point of recording skips.
    assert any(g["status"] == "skipped" and g["detail"] for g in gates)


@_needs_db
def test_a_second_run_answers_the_same_questions_rather_than_repeating_them(
        project, tenant, monkeypatch):
    """A re-run supersedes its own earlier results, name by name.

    The column is a list and stays one, but a check that ran again is the same
    question answered later. A surface showing `typecheck:python` twenty times,
    nineteen of them stale, is a surface a reviewer stops reading. Entries this
    run did not produce -- another producer's -- are left alone.
    """
    from services import change_gates, changes

    wt_dir = _branch_with_commits(project, "runs-twice")
    change = changes.open_change("proj", "runs-twice")
    changes.update_change(change["id"], gates=[
        {"name": "preview_deploy", "status": "pass", "detail": "someone else's"}])

    payload = _run_reviewer(wt_dir)
    assert change_gates.record_reviewer_run(
        payload, change_id=change["id"]) is not None
    # The second run comes in over STDIN, which is how reviewer.sh itself pipes
    # its verdict in -- the other half of the entry point the sweep uses.
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert change_gates.main(["--change-id", change["id"]]) == 0

    gates = changes.get_change(change["id"])["gates"]
    names = [g["name"] for g in gates]
    assert len(names) == len(set(names)), names
    assert names[0] == "preview_deploy", "another producer's gate was clobbered"
    assert "reviewer" in names


@_needs_db
def test_a_branch_with_no_open_change_is_logged_not_raised(project, tenant):
    """The sweep runs the reviewer long before a change exists, and on trees
    that will never have one. That is not an error and must not read as one."""
    from services import change_gates, changes, worktrees

    worktrees.create_branch("proj", "never-opened")
    payload = {"verdict": "approve", "complete": True, "ts": "2026-08-23T00:00:00+00:00"}
    assert change_gates.record_reviewer_run(
        payload, project_slug="proj", branch_slug="never-opened") is None

    # ...and once one IS open, the same call finds it.
    _branch_with_commits(project, "opened-later")
    opened = changes.open_change("proj", "opened-later")
    written = change_gates.record_reviewer_run(
        payload, project_slug="proj", branch_slug="opened-later")
    assert written is not None and written["id"] == opened["id"]
    assert [g["name"] for g in written["gates"]][0] == "reviewer"


# --- C7: the gate write is where auto-merge gets considered -------------------

def test_recording_gates_asks_whether_the_change_may_now_merge(monkeypatch):
    """"Merge when the checks pass" has to be asked the moment they pass.

    The gate write is that moment and the only one at which the record and the
    checks are known to describe the same commit, so `record_reviewer_run` is
    the primary trigger. Whether anything happens is auto_merge's decision, and
    by default it is no.
    """
    from services import auto_merge, change_gates, claim_check

    asked: list[tuple] = []
    monkeypatch.setattr(auto_merge, "consider",
                        lambda cid, required, advisory, **kw:
                        asked.append((cid, required, advisory, kw)) or {})

    change_gates._consider_auto_merge("abc-123")

    assert asked and asked[0][0] == "abc-123"
    assert asked[0][1] == change_gates.GATE_NAMES, (
        "the side that knows what a complete run produces is the side that "
        "says so; auto_merge does not import this module")
    assert asked[0][2] == claim_check.ADVISORY_GATES, (
        "and the advisory list is C6's published one, not a name repeated here")
    assert "trigger" in asked[0][3], "the record says what asked for the merge"


def test_a_broken_auto_merge_cannot_fail_the_gate_write(monkeypatch, capsys):
    """`record_reviewer_run` raises nothing, which is what lets reviewer.sh call
    it without guarding the result. C7 hangs a merge off that call and must not
    take the contract away: the reviewer's verdict is already made."""
    from services import auto_merge, change_gates

    def _boom(change_id, required, advisory, **kw):
        raise RuntimeError("auto-merge fell over")
    monkeypatch.setattr(auto_merge, "consider", _boom)

    change_gates._consider_auto_merge("abc-123")  # must not raise

    assert "auto-merge not considered" in capsys.readouterr().err
