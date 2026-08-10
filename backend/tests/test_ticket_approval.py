"""The system may propose work. Only the operator may start it.

The managed model the operator asked for: the assistant drafts and suggests, and
nothing executes until a person agrees. `draft` is the whole mechanism —
`next_ready` never picks one, so a suggestion can sit in the queue indefinitely
without a worker touching it, and `launch_task_run` refuses one so the explicit
"run now" button cannot bypass what the sweep respects.

These tests pin both doors, because a gate that holds for autonomous work and
quietly does not hold for a button is not a gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import tasks_all  # noqa: E402
from services import tasks  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_all, "_root_for_scope",
                        lambda scope: tmp_path if scope == "workshop" else None)
    from services import jobs as jobs_svc
    monkeypatch.setattr(jobs_svc, "JOBS_LOG", tmp_path / "jobs.jsonl")

    class _FakeProc:
        pid = 424242

    monkeypatch.setattr(tasks_all.subprocess, "Popen", lambda *a, **k: _FakeProc())
    app = FastAPI()
    app.include_router(tasks_all.router, prefix="/api")
    return TestClient(app), tmp_path


def _draft(root, **kw):
    return tasks.add(root, title="Bind a scope to a tracker", status="draft",
                     scoped_by="agent:chat", **kw)


# ---- the two doors ----------------------------------------------------------


def test_the_sweep_never_picks_a_draft(client):
    _, root = client
    _draft(root)
    assert tasks.next_ready(root) is None


def test_run_now_refuses_a_draft(client):
    c, root = client
    t = _draft(root)
    r = c.post(f"/api/tasks/workshop/{t['id']}/run")
    assert r.status_code == 409
    assert "approve it" in r.text


def test_an_approved_ticket_runs(client):
    c, root = client
    t = _draft(root)
    assert c.post(f"/api/tasks/workshop/{t['id']}/approve").status_code == 200
    r = c.post(f"/api/tasks/workshop/{t['id']}/run")
    assert r.status_code == 200, r.text


# ---- approving --------------------------------------------------------------


def test_approving_moves_a_draft_into_the_queue(client):
    c, root = client
    t = _draft(root)
    out = c.post(f"/api/tasks/workshop/{t['id']}/approve").json()
    assert out["task"]["status"] == "todo"
    assert tasks.next_ready(root)["id"] == t["id"]


def test_approval_is_recorded_on_the_ticket(client):
    c, root = client
    t = _draft(root)
    c.post(f"/api/tasks/workshop/{t['id']}/approve")
    kinds = [e.get("kind") for e in tasks.get_task(root, t["id"])["evidence"]]
    assert "approval" in kinds


def test_an_underspecified_draft_can_still_be_approved(client):
    """The operator is the authority. The checklist advises; refusing their own
    approval would make it an obstacle rather than advice."""
    c, root = client
    t = _draft(root)
    out = c.post(f"/api/tasks/workshop/{t['id']}/approve").json()
    assert out["task"]["status"] == "todo"
    assert out["readiness"]["ok"] is False
    assert out["readiness"]["missing"]


def test_what_the_ticket_did_not_answer_is_recorded_with_the_approval(client):
    c, root = client
    t = _draft(root)
    c.post(f"/api/tasks/workshop/{t['id']}/approve")
    ev = [e for e in tasks.get_task(root, t["id"])["evidence"]
          if e.get("kind") == "approval"]
    assert "not ready" in ev[0]["ref"]


def test_approve_and_run_in_one_call(client):
    c, root = client
    t = _draft(root)
    out = c.post(f"/api/tasks/workshop/{t['id']}/approve", json={"run": True}).json()
    assert out["task"]["status"] == "todo"
    assert out["run"]["ok"] is True


def test_approving_a_finished_ticket_is_refused(client):
    c, root = client
    t = _draft(root)
    tasks.update(root, t["id"], status="done")
    r = c.post(f"/api/tasks/workshop/{t['id']}/approve")
    assert r.status_code == 409


def test_approving_an_already_queued_ticket_is_a_no_op(client):
    """Idempotent: a double click must not stack two approvals."""
    c, root = client
    t = tasks.add(root, title="Already mine", status="todo")
    r = c.post(f"/api/tasks/workshop/{t['id']}/approve")
    assert r.status_code == 200
    assert r.json()["task"]["status"] == "todo"
    assert [e for e in tasks.get_task(root, t["id"])["evidence"]
            if e.get("kind") == "approval"] == []


# ---- where drafts come from -------------------------------------------------


def test_a_suggested_ticket_starts_as_a_draft(tmp_path, monkeypatch):
    """The assistant filing a ticket for itself proposes; it does not queue."""
    from services import task_dedup
    monkeypatch.setattr(task_dedup, "decide",
                        lambda text, open_tasks: {"decision": "distinct",
                                                  "matched_id": None, "cosine": None})
    res = task_dedup.dedup_add(tmp_path, text="Suggested by the assistant",
                               source={"kind": "chat", "ref": "t1"},
                               scoped_by="agent:chat", status="draft")
    assert res["task"]["status"] == "draft"


def test_filing_defaults_to_the_queue_for_callers_that_do_not_ask(tmp_path, monkeypatch):
    """`status` defaults to todo, so the operator's own filing paths (the Work UI,
    the backlog endpoint) are unchanged by the approval model."""
    from services import task_dedup
    monkeypatch.setattr(task_dedup, "decide",
                        lambda text, open_tasks: {"decision": "distinct",
                                                  "matched_id": None, "cosine": None})
    res = task_dedup.dedup_add(tmp_path, text="Filed by the operator",
                               source={"kind": "operator", "ref": "work-ui"})
    assert res["task"]["status"] == "todo"
