"""What a ticket has to say before a worker is asked to act on it.

A worker never sees the conversation that produced its ticket, so anything the
ticket leaves out is guessed at or asked about, and both cost a run. These tests
pin the check and, just as importantly, pin how much of it is ENFORCED: measured
against the live stores when this shipped, 0 of 40 open tickets passed every
check, so a full gate would have stopped all work. Only a BROKEN ticket is
refused a run; the rest is reported.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import tasks_all  # noqa: E402
from services import tasks, ticket_readiness as tr  # noqa: E402

WELL_SPECIFIED = {
    "title": "Bind a scope to an external tracker",
    "body": ("Objective: one binding per scope, chosen in Settings.\n"
             "Output: a binding record exists and `GET /api/trackers` returns it.\n"
             "Start from: backend/services/tracker_bindings.py and routes/trackers.py"),
    "touches": ["backend/services/tracker_bindings.py"],
}


def test_a_well_specified_ticket_is_ready():
    assert tr.check(WELL_SPECIFIED)["ok"] is True


def test_a_bare_title_is_not_ready():
    r = tr.check({"title": "Fix the thing", "body": ""})
    assert r["ok"] is False
    assert {m["check"] for m in r["missing"]} >= {"objective", "acceptance", "boundaries"}


@pytest.mark.parametrize("drop,expected", [
    ("body", "objective"),
    ("touches", "boundaries"),
])
def test_each_missing_piece_is_named(drop, expected):
    t = dict(WELL_SPECIFIED)
    t[drop] = "" if drop == "body" else []
    assert expected in {m["check"] for m in tr.check(t)["missing"]}


def test_acceptance_can_come_from_the_field_instead_of_the_prose():
    t = dict(WELL_SPECIFIED)
    t["body"] = ("Objective: one binding per scope.\n"
                 "Start from: backend/services/tracker_bindings.py")
    assert "acceptance" in {m["check"] for m in tr.check(t)["missing"]}
    t["acceptance"] = {"kind": "reviewer"}
    assert "acceptance" not in {m["check"] for m in tr.check(t)["missing"]}


def test_a_quick_ticket_need_not_name_entry_points():
    t = {"title": "Bump the poll interval to 60s",
         "body": "Objective: stop hammering the API.\nOutput: the interval is 60s.",
         "touches": ["backend/services/tracker_sync.py"], "effort": "quick"}
    assert "entry_points" not in {m["check"] for m in tr.check(t)["missing"]}


def test_open_latitude_is_an_explicit_boundary():
    t = dict(WELL_SPECIFIED)
    t["touches"] = []
    assert "boundaries" in {m["check"] for m in tr.check(t)["missing"]}
    t["latitude"] = "open"
    assert "boundaries" not in {m["check"] for m in tr.check(t)["missing"]}


def test_an_unresolvable_dependency_is_named_only_when_ids_are_known():
    t = dict(WELL_SPECIFIED, depends_on=["nope"])
    # No id set supplied: the check cannot know, so it does not claim to.
    assert "dependencies" not in {m["check"] for m in tr.check(t)["missing"]}
    r = tr.check(t, known_ids={"other"})
    assert "dependencies" in {m["check"] for m in r["missing"]}


def test_only_a_broken_ticket_blocks():
    """The distinction the whole gate rests on: underspecified is advice, broken
    is a refusal."""
    underspecified = tr.check({"title": "Fix the thing", "body": ""})
    assert underspecified["ok"] is False
    assert tr.blocking(underspecified) == []

    broken = tr.check({"title": "", "body": "Objective: something."})
    assert tr.blocking(broken)


def test_an_elided_title_is_a_warning():
    r = tr.check(dict(WELL_SPECIFIED, title="Do the thing…"))
    assert any("elided" in w for w in r["warnings"])


# ---- the run gate -----------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_all, "_root_for_scope",
                        lambda scope: tmp_path if scope == "workshop" else None)
    # Stub the PROCESS LAUNCH, not `launch_task_run`. The readiness gate lives
    # inside `launch_task_run` (so the chat's "file and run" path cannot skip
    # it), and stubbing the whole function would stub the gate along with it and
    # leave this file asserting against its own mock.
    from services import jobs as jobs_svc

    monkeypatch.setattr(jobs_svc, "JOBS_LOG", tmp_path / "jobs.jsonl")

    class _FakeProc:
        pid = 424242

    monkeypatch.setattr(tasks_all.subprocess, "Popen", lambda *a, **k: _FakeProc())
    app = FastAPI()
    app.include_router(tasks_all.router, prefix="/api")
    return TestClient(app), tmp_path


def test_an_underspecified_ticket_still_runs(client):
    """0 of 40 live tickets passed every check. Refusing them would have stopped
    the queue, not improved it."""
    c, root = client
    t = tasks.add(root, title="Fix the thing")
    r = c.post(f"/api/tasks/workshop/{t['id']}/run")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_a_ticket_with_a_dangling_dependency_is_refused(client):
    c, root = client
    t = tasks.add(root, title="Bind a scope", depends_on=["no-such-ticket"])
    r = c.post(f"/api/tasks/workshop/{t['id']}/run")
    assert r.status_code == 400
    assert "does not exist" in r.text


def test_the_detail_response_reports_readiness(client):
    c, root = client
    t = tasks.add(root, title="Fix the thing")
    got = c.get(f"/api/tasks/workshop/{t['id']}").json()
    assert got["readiness"]["ok"] is False
    assert {m["check"] for m in got["readiness"]["missing"]} >= {"objective", "acceptance"}
