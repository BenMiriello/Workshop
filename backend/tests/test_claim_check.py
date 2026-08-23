"""The claim-to-diff check: is the summary's story in the patch?

Step C6 of `docs/platform-redesign/changes-and-review-2026-08-22.md`, §5.2.
The claim under test is not "a regex matches" -- it is the honesty property the
step exists for: this check reports a FINDING a person can verify in ten
seconds, it says which side it doubts, and where it cannot judge it says so
rather than banking a `pass` it did not earn. A check that answers `pass` on a
change nobody actually checked is worse than no check, because every other
green gate on the surface is then worth less.

So the cases below are the four the design cares about, plus the two contracts
that let it be wired anywhere: the model layer degrades to the mechanical one
rather than raising, and a failure inside the check never reaches its caller.

Mostly database-free on purpose. The judgement is a pure function of a summary
and a diff, and the write path is exercised against the real
`services/changes.py` entry points with their store stubbed, so this suite says
something on any box. The one end-to-end case that needs a real change row is
guarded like the rest of the db suite.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _diff(*files: tuple[str, str, str]) -> dict:
    """`(path, status, patch)` triples in `worktrees.diff_branch`'s shape."""
    return {"summary": f"{len(files)} files changed",
            "files": [{"path": p, "status": s, "diff": d} for p, s, d in files]}


# A patch that adds a helper and a test for it. Purely additive, two files, and
# every claim made about it below is checkable against exactly this.
_ADDS_A_HELPER = _diff(
    ("backend/services/foo.py", "M",
     "@@ -1,2 +1,6 @@\n+def new_helper(x):\n+    return x + 1\n"),
    ("backend/tests/test_foo.py", "A",
     "@@ -0,0 +1,3 @@\n+def test_new_helper():\n+    assert new_helper(1) == 2\n"),
)


# ---------------------------------------------------------------------------
# the judgement itself
# ---------------------------------------------------------------------------

def test_a_diff_that_shows_what_the_summary_claims_passes():
    """The honest case. Every name in the summary is in the patch, so the check
    gets out of the reviewer's way and says what it checked."""
    from services.claim_check import judge

    result = judge("Added `new_helper` to backend/services/foo.py, with a test "
                   "for it.", _ADDS_A_HELPER)

    assert result.status == "pass"
    assert result.layer == "mechanical"
    assert result.findings == ()
    # A pass has to say what it checked, or "pass" and "there was nothing to
    # check" are indistinguishable -- and only one of them is a pass.
    assert "backend/services/foo.py" in result.claims
    assert "new_helper" in result.claims


def test_a_file_the_summary_names_and_the_diff_never_touches_is_flagged_by_name():
    """The measured number-one defect: a description asserting work that is not
    in the patch. The finding must NAME the file, because the whole value of the
    flag is that a person can check it against the file list without reading the
    diff."""
    from services.claim_check import judge

    result = judge("Added `new_helper` to backend/services/foo.py and wired it "
                   "into frontend/src/lib/scope.ts.", _ADDS_A_HELPER)

    assert result.status == "fail"
    assert "frontend/src/lib/scope.ts" in result.detail
    # It doubts the SUMMARY, not the diff, and says so in ordinary words.
    assert "the summary says" in result.detail
    # And it does not smear: the claim that IS supported is not in the findings.
    assert not any("foo.py" in f for f in result.findings)


def test_a_summary_that_names_nothing_checkable_is_unjudgeable_not_a_pass():
    """The sweep's own boilerplate, verbatim from `project_ralph.sh`.

    It asserts nothing a diff could confirm or contradict. Recording `pass`
    here would be the check spending the reviewer's trust on a comparison it
    never made, which §5's whole argument is that the system cannot afford.
    """
    from db.models import GATE_STATUSES
    from services.claim_check import judge

    result = judge("Opened by the unattended sweep on its success leg. The "
                   "summary is the author's claim; read the diff.",
                   _ADDS_A_HELPER)

    assert result.status == "skipped"
    assert result.status in GATE_STATUSES
    assert result.status != "pass"
    assert "cannot judge" in result.detail
    assert result.claims == ()


def test_a_claimed_test_with_no_test_file_in_the_diff_is_flagged():
    """One of the two cheap signals that need no name at all."""
    from services.claim_check import judge

    only_source = _diff(("backend/services/foo.py", "M",
                         "@@\n+def new_helper():\n+    return 1\n"))
    result = judge("Added a helper, with a test.", only_source)

    assert result.status == "fail"
    assert "no changed file is a test file" in result.detail


def test_a_claimed_removal_against_a_purely_additive_patch_is_flagged():
    """The other one. Nothing is deleted and nothing is even edited away, so a
    summary that says something was removed is describing a different patch."""
    from services.claim_check import judge

    result = judge("Removed the dead branch and simplified the helper.",
                   _ADDS_A_HELPER)

    assert result.status == "fail"
    assert "purely additive" in result.detail


def test_pointing_at_a_document_is_not_a_claim_to_have_changed_it():
    """Precision, which is the property that decides whether this gate gets
    read at all. A summary that cites a doc has not claimed to edit it, and a
    filename full of English words ("applying-CHANGES.md") must not be read as
    the verb that makes it a claim."""
    from services.claim_check import judge

    result = judge("Updated backend/services/foo.py. See "
                   "docs/applying-changes.md for the mechanics.",
                   _ADDS_A_HELPER)

    assert result.status == "pass", result.detail
    assert "applying-changes.md" not in result.detail


def test_a_branch_name_and_a_host_are_not_directories_in_this_repo():
    """Two things shaped like a path that are not one, both of which turn up in
    summaries this system writes itself. Flagging `wt/proj/thing` as an
    untouched directory is a finding nobody can act on, and those are the ones
    that teach a reviewer to skim the row."""
    from services.claim_check import judge

    assert judge("Merged wt/proj/fix-thing into main after updating the "
                 "helper.", _ADDS_A_HELPER).status == "skipped"
    assert judge("Ported the approach from github.com/acme/repo; updated "
                 "backend/services/foo.py.", _ADDS_A_HELPER).status == "pass"


def test_the_check_reads_a_change_and_answers_in_one_structured_result():
    """The published entry point takes the change and its diff, and everything
    a caller needs is on the result -- no second call to find out what happened.
    """
    from services import claim_check

    result = claim_check.check_change(
        {"id": "c1", "summary": "Added `new_helper` to backend/services/foo.py."},
        _ADDS_A_HELPER)

    assert (result.status, result.layer) == ("pass", "mechanical")
    gate = result.as_gate()
    assert gate["name"] == claim_check.GATE_NAME
    assert gate["status"] == "pass"
    # The layer is IN the detail: a reviewer must be able to tell a mechanical
    # fact from a model's judgement without going to look it up.
    assert "layer: mechanical" in gate["detail"]
    assert gate["at"]


def test_the_gate_it_writes_is_the_shape_the_column_validates():
    """`changes._validated_gates` is the one shape check for this column, and
    it is what a route would reject on. Run the real one."""
    from services import changes
    from services.claim_check import GATE_NAME, judge

    gate = judge("Added a helper.", _ADDS_A_HELPER).as_gate()
    validated = changes._validated_gates([gate])

    assert validated[0]["name"] == GATE_NAME
    assert set(validated[0]) == {"name", "status", "detail", "at"}


def test_this_gate_is_advertised_as_advisory():
    """C7's low-risk classifier requires every gate green before it merges
    without a person. A judgement that can be wrong must not be able to hold a
    change hostage, so the exclusion is published here by name rather than
    left for that step to remember."""
    from services import claim_check

    assert claim_check.GATE_NAME in claim_check.ADVISORY_GATES


# ---------------------------------------------------------------------------
# the optional model layer, and what happens when it is not there
# ---------------------------------------------------------------------------

def test_the_model_layer_degrades_to_the_mechanical_answer_when_unavailable(
        monkeypatch, capsys):
    """No CLI, no token, a timeout -- all the same outcome: the mechanical
    answer stands, `layer` still says mechanical, and nothing raises. This is
    the property that lets the check be wired into a route at all."""
    from services import claim_check, llm_router

    def _explode(*a, **kw):
        raise RuntimeError("claude -p is not on this box")

    monkeypatch.setattr(llm_router, "call_cloud", _explode)
    change = {"id": "c1",
              "summary": "Added `new_helper` to backend/services/foo.py."}

    result = claim_check.check_change(change, _ADDS_A_HELPER, use_model=True)

    assert result.status == "pass"
    assert result.layer == "mechanical"
    assert "second-model layer was unavailable" in result.detail
    assert "unavailable" in capsys.readouterr().err


def test_an_unjudgeable_change_stays_unjudgeable_when_the_model_is_absent(
        monkeypatch):
    """The dangerous degradation, ruled out: losing the model must never turn
    "I could not check this" into "I checked this and it was fine"."""
    from services import claim_check, llm_router

    monkeypatch.setattr(llm_router, "call_cloud",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("no cli")))
    result = claim_check.check_change(
        {"id": "c1", "summary": "Cleaned things up a bit."},
        _ADDS_A_HELPER, use_model=True)

    assert result.status == "skipped"
    assert result.status != "pass"


def test_a_model_doubt_is_recorded_as_a_model_judgement(monkeypatch):
    """What the model layer buys: a claim that names nothing, so no regex can
    check it. The answer must be labelled as a judgement rather than passed off
    as a mechanical fact."""
    from services import claim_check, llm_router

    monkeypatch.setattr(llm_router, "call_cloud", lambda *a, **kw: {
        "text": '{"verdict": "unsupported", "doubt": "the summary says it adds '
                'caching; nothing in the patch caches anything"}'})

    result = claim_check.check_change(
        {"id": "c1", "summary": "Sped the endpoint up by adding caching."},
        _ADDS_A_HELPER, use_model=True)

    assert result.status == "fail"
    assert result.layer == "model"
    assert "caches anything" in result.detail
    assert "judgement" in result.detail
    assert "layer: model" in result.as_gate()["detail"]


def test_a_model_that_cannot_tell_does_not_overrule_the_mechanics(monkeypatch):
    from services import claim_check, llm_router

    monkeypatch.setattr(llm_router, "call_cloud",
                        lambda *a, **kw: {"text": '{"verdict": "unsure"}'})
    result = claim_check.check_change(
        {"id": "c1", "summary": "Tidied the module."}, _ADDS_A_HELPER,
        use_model=True)

    assert result.status == "skipped"
    assert result.layer == "mechanical"


def test_an_unparseable_model_answer_is_a_degradation_not_a_verdict(monkeypatch):
    from services import claim_check, llm_router

    monkeypatch.setattr(llm_router, "call_cloud",
                        lambda *a, **kw: {"text": "I think it's fine, mostly."})
    result = claim_check.check_change(
        {"id": "c1", "summary": "Added `new_helper` to backend/services/foo.py."},
        _ADDS_A_HELPER, use_model=True)

    assert (result.status, result.layer) == ("pass", "mechanical")
    assert "unavailable" in result.detail


def test_a_mechanical_finding_never_pays_for_a_model_call(monkeypatch):
    """The cheap layer comes first because it costs nothing, and a check that
    costs two minutes on every change is a check that gets switched off. Once
    the mechanics have a concrete finding there is nothing left to buy."""
    from services import claim_check, llm_router

    def _must_not_run(*a, **kw):
        raise AssertionError("the model was called after a mechanical finding")

    monkeypatch.setattr(llm_router, "call_cloud", _must_not_run)
    result = claim_check.check_change(
        {"id": "c1", "summary": "Wired it into frontend/src/lib/scope.ts."},
        _ADDS_A_HELPER, use_model=True)

    assert (result.status, result.layer) == ("fail", "mechanical")


# ---------------------------------------------------------------------------
# the write, and the never-fail-the-caller contract
# ---------------------------------------------------------------------------

class _FakeStore:
    """`services/changes.py`'s three entry points, with the store taken out.

    Not a mock of the check: the module under test calls the real
    `services.changes` names, and this replaces only what needs Postgres, so
    the wiring being asserted on is the wiring that ships.
    """

    def __init__(self, change: dict, diff: dict):
        self.change = change
        self.diff = diff
        self.writes: list[dict] = []

    def install(self, monkeypatch):
        from services import changes
        monkeypatch.setattr(changes, "get_change", lambda cid: dict(self.change))
        monkeypatch.setattr(changes, "change_for_branch",
                            lambda p, b: dict(self.change))
        monkeypatch.setattr(changes, "diff_for", lambda cid: self.diff)
        monkeypatch.setattr(changes, "update_change", self._update)
        return self

    def _update(self, change_id, **fields):
        from services import changes as changes_mod
        gates = changes_mod._validated_gates(fields["gates"])
        self.writes.append({"id": change_id, "gates": gates})
        self.change = {**self.change, "gates": gates}
        return dict(self.change)


def test_the_answer_lands_on_the_changes_own_gates_and_supersedes_itself(
        monkeypatch):
    """One column, one name, one current answer. Re-running must replace this
    check's previous entry and leave every other producer's alone -- the review
    surface showing the same question answered twice is the failure C3 already
    ruled out for the reviewer's gates."""
    from services import claim_check

    change = {"id": "c1",
              "summary": "Wired it into frontend/src/lib/scope.ts.",
              "gates": [{"name": "reviewer", "status": "pass",
                         "detail": "approved", "at": "2026-08-23T00:00:00+00:00"}]}
    store = _FakeStore(change, _ADDS_A_HELPER).install(monkeypatch)

    first = claim_check.record_claim_check(change_id="c1")
    names = [g["name"] for g in first["gates"]]
    assert names.count(claim_check.GATE_NAME) == 1
    assert "reviewer" in names, "another producer's gate was collateral damage"
    gate = next(g for g in first["gates"] if g["name"] == claim_check.GATE_NAME)
    assert gate["status"] == "fail"
    assert "frontend/src/lib/scope.ts" in gate["detail"]

    second = claim_check.record_claim_check(change_id="c1")
    assert [g["name"] for g in second["gates"]].count(claim_check.GATE_NAME) == 1
    assert len(store.writes) == 2


def test_a_branch_with_no_open_change_is_reported_not_raised(monkeypatch, capsys):
    from services import changes, claim_check

    monkeypatch.setattr(changes, "change_for_branch", lambda p, b: None)
    assert claim_check.record_claim_check(project_slug="proj",
                                          branch_slug="nope") is None
    assert "no open change" in capsys.readouterr().err


def test_a_failure_inside_the_check_never_reaches_its_caller(monkeypatch, capsys):
    """The contract `change_gates` states and this module inherits: the change
    is open and its commits exist whatever this decides, so a broken check
    costs a log line and nothing else. Every way it can break, at once."""
    from services import changes, claim_check

    # 1. The store is unreachable.
    def _down(*a, **kw):
        raise changes.ChangesUnavailable("no relational store")

    monkeypatch.setattr(changes, "get_change", _down)
    assert claim_check.record_claim_check(change_id="c1") is None

    # 2. The check itself throws.
    store = _FakeStore({"id": "c1", "summary": "Added a helper."},
                       _ADDS_A_HELPER).install(monkeypatch)
    monkeypatch.setattr(claim_check, "check_change", _down)
    assert claim_check.record_claim_check(change_id="c1") is None
    assert store.writes == []

    # 3. Nothing was addressed at all.
    assert claim_check.record_claim_check() is None
    assert claim_check.record_claim_check(change_id="", branch_slug="x") is None

    # Each one said so rather than dying quietly.
    assert capsys.readouterr().err.count("[claim_check]") >= 4


def test_the_cli_exits_zero_whatever_happens(monkeypatch):
    """Its callers are the sweep and the reviewer leg, whose own results this
    must never be able to change."""
    from services import changes, claim_check

    monkeypatch.setattr(changes, "get_change", lambda cid: (_ for _ in ()).throw(
        RuntimeError("boom")))
    assert claim_check.main(["--change-id", "c1"]) == 0
    assert claim_check.main([]) == 0


# ---------------------------------------------------------------------------
# end to end, on a real change row
# ---------------------------------------------------------------------------

pytest.importorskip("sqlalchemy")

_needs_db = pytest.mark.skipif(
    not os.environ.get("WORKSHOP_DATABASE_ADMIN_URL"),
    reason="no WORKSHOP_DATABASE_ADMIN_URL; the fixture mints a throwaway org, "
           "which the application role deliberately cannot do",
)

# The real git project + throwaway org, shared with the C1 suite rather than
# re-declared.
from tests.test_changes import (_branch_with_commits, project,  # noqa: E402,F401
                                tenant)


@_needs_db
def test_opening_a_change_judges_its_claim_and_the_route_shows_it(
        project, tenant):
    """The wiring, from the outside: a change is opened through the route with
    a summary naming a file the branch never touched, and the reviewer asking
    for that change finds the flag on it."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import changes as change_routes
    from services import claim_check

    _branch_with_commits(project, "overclaims")

    app = FastAPI()
    app.include_router(change_routes.router)
    client = TestClient(app)
    res = client.post("/changes", json={
        "project_slug": "proj", "branch_slug": "overclaims",
        "title": "Overclaims",
        "summary": "Added file0.txt and updated backend/services/nowhere.py."})
    assert res.status_code == 200, res.text

    gates = {g["name"]: g for g in res.json()["gates"]}
    assert claim_check.GATE_NAME in gates
    assert gates[claim_check.GATE_NAME]["status"] == "fail"
    assert "nowhere.py" in gates[claim_check.GATE_NAME]["detail"]

    # And a refresh re-judges rather than stacking a second answer.
    again = client.post(f"/changes/{res.json()['id']}/refresh")
    assert again.status_code == 200, again.text
    names = [g["name"] for g in again.json()["gates"]]
    assert names.count(claim_check.GATE_NAME) == 1
