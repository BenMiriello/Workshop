"""A ticket without an executable finish line is not a ticket.

Measured 2026-09-01: 208 open tickets, `acceptance` null on every one, a field
that had existed since the store did. So every ticket's finish line was a
sentence somewhere in a 337-word body, and a worker handed one formed its own
view of "done" and reported success against that view.

These are the two directions the gate has to hold in: a filing with no runnable
check is refused with a message that says what would satisfy it, and a filing
with one goes through. The second half matters as much as the first -- a gate
nobody can pass is a gate that gets deleted.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import tasks_all
from services import task_dedup, tasks, ticket_format


@pytest.fixture
def roots(tmp_path):
    a = tmp_path / "workshop"
    a.mkdir()
    return {"workshop": a}


@pytest.fixture
def client(roots, monkeypatch):
    monkeypatch.setattr(tasks_all, "_root_for_scope", lambda s: roots.get(s))
    monkeypatch.setattr(tasks_all, "_scope_roots", lambda: list(roots.items()))
    monkeypatch.setattr(tasks_all, "scope_roots", lambda: list(roots.items()),
                        raising=False)
    app = FastAPI()
    app.include_router(tasks_all.router, prefix="/api")
    return TestClient(app)


# ---- the rule ---------------------------------------------------------------

@pytest.mark.parametrize("spec", [
    "npm run typecheck",
    "cd frontend && npm test",
    "pytest -q backend/tests/test_ticket_acceptance_gate.py",
    "bash scripts/check_design.sh",
    "scripts/check_design.sh",
    "WORKSHOP_ROOT=. python3 scripts/check_ticket_acceptance.py",
])
def test_a_command_is_executable(spec):
    assert ticket_format.is_executable_command(spec)


@pytest.mark.parametrize("prose", [
    "",
    "it works",
    "The endpoint returns X and a test covers it",
    "the page renders correctly on a phone",
    None,
])
def test_prose_is_not_a_command(prose):
    """The one thing that separates a check from a sentence is the shape of the
    first token. This is not a quality judgement and cannot be: whether the
    command is a GOOD check is the reviewer's job."""
    assert not ticket_format.is_executable_command(prose)


def test_a_reviewer_kind_alone_is_not_executable():
    """`reviewer` and `operator` are judgement, which is exactly what the gate
    exists to stop a ticket from leaning on. They stay legal values of the field
    (live records carry them) and neither satisfies the rule by itself."""
    assert not ticket_format.executable_acceptance({"kind": "reviewer", "spec": None})
    assert not ticket_format.executable_acceptance({"kind": "operator"})
    assert not ticket_format.executable_acceptance(None)


def test_criteria_satisfy_it_whatever_the_kind():
    """The assertions the gate actually runs (change_gates.CRITERIA_GATE). A
    criteria-carrying ticket stays reviewer-kind by convention, so the rule has
    to look past `kind`."""
    acc = {"kind": "reviewer", "spec": None,
           "criteria": [{"desc": "the suite passes", "cmd": "pytest -q",
                         "expect": "exit0"}]}
    assert ticket_format.executable_acceptance(acc)


def test_the_refusal_says_what_would_satisfy_it():
    """A refusal that does not name the fix costs a whole run to discover."""
    msg = ticket_format.acceptance_refusal(None)
    assert msg and ticket_format.ACCEPTANCE_HINT in msg
    assert ticket_format.acceptance_refusal(
        {"kind": "command", "spec": "npm run typecheck"}) is None


# ---- the filing path --------------------------------------------------------

def test_filing_without_a_check_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(task_dedup, "decide",
                        lambda text, open_tasks, **kw: {"decision": "distinct",
                                                        "matched_id": None,
                                                        "cosine": None})
    with pytest.raises(ValueError) as e:
        task_dedup.dedup_add(tmp_path, text="Make the thing better")
    assert "acceptance" in str(e.value)
    assert tasks.list_tasks(tmp_path) == []


def test_filing_with_a_check_goes_through(tmp_path, monkeypatch):
    monkeypatch.setattr(task_dedup, "decide",
                        lambda text, open_tasks, **kw: {"decision": "distinct",
                                                        "matched_id": None,
                                                        "cosine": None})
    out = task_dedup.dedup_add(
        tmp_path, text="Make the thing better",
        acceptance={"kind": "command", "spec": "npm run typecheck"})
    assert out["decision"] == "distinct"
    filed = tasks.get_task(tmp_path, out["task"]["id"])
    assert filed["acceptance"]["spec"] == "npm run typecheck"


def test_a_model_proposal_is_not_gated(tmp_path, monkeypatch):
    """`ticket_drafts` files MODEL PROPOSALS at `draft` for the operator to read.
    A draft is not yet in the queue (`next_ready` never picks one), and refusing
    the proposal would throw away the reading rather than the guessing."""
    monkeypatch.setattr(task_dedup, "decide",
                        lambda text, open_tasks, **kw: {"decision": "distinct",
                                                        "matched_id": None,
                                                        "cosine": None})
    out = task_dedup.dedup_add(tmp_path, text="A proposal nobody has read",
                               status="draft", require_acceptance=False)
    assert out["task"]["status"] == "draft"


# ---- the API door -----------------------------------------------------------

def test_the_create_route_refuses_and_says_why(client, roots):
    r = client.post("/api/tasks", json={"scope": "workshop",
                                        "title": "Something worth doing"})
    assert r.status_code == 400, r.text
    assert "acceptance" in r.text


def test_the_create_route_accepts_a_command(client, roots):
    r = client.post("/api/tasks", json={
        "scope": "workshop", "title": "Something worth doing",
        "acceptance": {"kind": "command", "spec": "npm run typecheck"}})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["acceptance"]["spec"] == "npm run typecheck"


def test_the_create_route_refuses_prose_dressed_as_a_command(client, roots):
    """The failure this is really guarding: an acceptance that restates the
    objective. It is the shape 195 tickets already had in their bodies."""
    r = client.post("/api/tasks", json={
        "scope": "workshop", "title": "Something worth doing",
        "acceptance": {"kind": "command",
                       "spec": "The endpoint returns X and a test covers it"}})
    assert r.status_code == 400, r.text
    assert "does not read as a command" in r.text


# ---- provenance is not intent ----------------------------------------------

def test_an_unrecorded_source_is_unknown_not_the_operator():
    """`operator` used to double as the value for "nobody wrote anything down",
    which put 34 tickets under a name that had filed maybe two of them. The
    absence is now reported as the absence."""
    assert tasks.source_kind({}) == "unknown"
    assert tasks.source_kind({"source": None}) == "unknown"
    assert tasks.source_kind({"source": {"kind": "nonsense"}}) == "unknown"
    assert tasks.source_kind({"source": {"kind": "operator"}}) == "operator"


def test_a_filing_that_says_nothing_records_nothing(tmp_path):
    """Both doors default to the same thing. The API door used to stamp
    `operator` and the CLI door `agent`, so one filing got two different
    provenances depending on which one it came through."""
    t = tasks.add(tmp_path, text="Filed by nobody in particular")
    assert t["source"]["kind"] == "unknown"


def test_sorting_by_source_groups_by_the_door_it_came_through():
    rows = [
        {"source": {"kind": "agent"}, "priority": 100, "created": "a"},
        {"source": {"kind": "operator"}, "priority": 0, "created": "b"},
        {"source": {"kind": "chat"}, "priority": 40, "created": "c"},
        {"source": {"kind": "operator"}, "priority": 80, "created": "d"},
        {"priority": 100, "created": "e"},
    ]
    got = [tasks.source_kind(t) for t in tasks.sort_tasks(rows, "source")]
    assert got == ["operator", "operator", "chat", "agent", "unknown"]
    # Inside one source the tie-break is the scheduler's order, priority first.
    assert tasks.sort_tasks(rows, "source")[0]["priority"] == 80


# ---- EVERY door, because a gate on one of two is worse than none -------------
#
# `check_pluggable_apps.sh` printed "clean" for five weeks while inspecting five
# files. A gate that covers one entry point reads as enforced and is not, so the
# doors are enumerated here rather than assumed.

def test_the_patch_route_cannot_walk_a_filing_back(client, roots):
    """The second door. A ticket filed WITH a check could be edited to have none
    one request later, which would make the create gate decorative."""
    r = client.post("/api/tasks", json={
        "scope": "workshop", "title": "Something worth doing",
        "acceptance": {"kind": "command", "spec": "npm run typecheck"}})
    tid = r.json()["task"]["id"]

    for bad in ({"kind": "reviewer", "spec": None},
                {"kind": "operator", "spec": "I will look at it"},
                {"kind": "command", "spec": "it works"}):
        got = client.patch(f"/api/tasks/workshop/{tid}", json={"acceptance": bad})
        assert got.status_code == 400, f"{bad} was accepted: {got.text}"

    still = client.get(f"/api/tasks/workshop/{tid}").json()
    assert still["acceptance"]["spec"] == "npm run typecheck"


def test_the_patch_route_keeps_the_criteria_it_was_sent(client, roots):
    """It used to rebuild the record as `{kind, spec}`, so saving a ticket from
    the UI DELETED its runnable assertions. That is the form most acceptance
    actually takes (`--criterion` files reviewer-kind with a null spec)."""
    r = client.post("/api/tasks", json={
        "scope": "workshop", "title": "Something worth doing",
        "acceptance": {"kind": "command", "spec": "npm run typecheck"}})
    tid = r.json()["task"]["id"]
    got = client.patch(f"/api/tasks/workshop/{tid}", json={"acceptance": {
        "kind": "reviewer", "spec": None,
        "criteria": [{"desc": "the suite passes", "cmd": "pytest -q",
                      "expect": "exit0"}]}})
    assert got.status_code == 200, got.text
    assert got.json()["acceptance"]["criteria"][0]["cmd"] == "pytest -q"


def test_the_chat_task_patch_block_is_gated_and_says_so(tmp_path, monkeypatch):
    """The chat door. A refusal here is REPORTED back into the conversation
    rather than swallowed, because the assistant is still there and can refile
    correctly in its next turn only if it is told why."""
    from services import task_scope
    monkeypatch.setattr(task_scope, "WORKSHOP_SCOPE_DIR", tmp_path)
    _real = task_scope.relational_scope_for_root
    monkeypatch.setattr(task_scope, "relational_scope_for_root",
                        lambda r: None if str(r) == str(tmp_path) else _real(r))

    bad = 'Sure.\n\n```task-patch\n{"add":[{"text":"No finish line"}]}\n```\n'
    _, notes = task_scope.apply_task_patch("general", bad, "t1", None)
    assert any("Not filed" in n and "acceptance" in n for n in notes), notes
    assert not tasks._load(tmp_path)

    good = ('Sure.\n\n```task-patch\n{"add":[{"text":"With a finish line",'
            '"acceptance":{"kind":"command","spec":"pytest -q"}}]}\n```\n')
    _, notes = task_scope.apply_task_patch("general", good, "t1", None)
    assert any("added 1 task" in n for n in notes), notes
    assert any("With a finish line" in (t.get("text") or "")
               for t in tasks._load(tmp_path))


def test_the_cli_door_refuses_and_names_the_flag(tmp_path):
    """`scripts/project_tasks.py add` is the surface an agent types at, so its
    refusal has to name the flags that would satisfy it on the command that was
    just refused."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    cli = Path(__file__).resolve().parents[2] / "scripts" / "project_tasks.py"
    def run(*args):
        return subprocess.run([sys.executable, str(cli), "add", str(tmp_path),
                               "A ticket", *args],
                              capture_output=True, text=True)

    refused = run()
    assert refused.returncode == 2, refused.stdout
    out = json.loads(refused.stdout)
    assert "acceptance" in out["error"]
    assert "--acceptance-cmd" in out["fix"] and "--criterion" in out["fix"]

    ok = run("--acceptance-cmd", "pytest -q")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert json.loads(ok.stdout)["acceptance"]["spec"] == "pytest -q"


def test_the_ratchet_counts_a_ticket_with_no_runnable_check(tmp_path):
    """The number has to mean what it says. A `reviewer` kind with a null spec
    and no criteria is NOT acceptance and is counted as debt; the same kind WITH
    criteria is, and is not."""
    unchecked = {"kind": "reviewer", "spec": None}
    checked = {"kind": "reviewer", "spec": None,
               "criteria": [{"desc": "d", "cmd": "pytest -q", "expect": "exit0"}]}
    assert not ticket_format.executable_acceptance(unchecked)
    assert ticket_format.executable_acceptance(checked)
