"""Answering a FINISHED ticket accepts it, and never quietly un-accepts it.

THE REGRESSION THIS PINS. On 2026-08-22 the operator answered "Approve" to a
ticket whose work was finished and committed, from the chat's Tickets panel. The
word `approve` named two opposite outcomes in two routers: `routes/jobs` mapped
it to `in_review -> done`, and `routes/tasks_all.review_task` mapped it to
"release the block, the task is `todo` and runs again". The inbox posted to the
second one regardless of what the ticket was waiting for, so the answer put
committed work back in the queue, refunded an attempt, and wrote the bare word
"Approve" into the ticket body as an instruction to the next attempt.

`entity-model-2026-07-18.md` had already ratified `in_review -> done` on
approval, so this was a divergence from the model of record.

Design: docs/platform-redesign/deciding-on-work-from-a-chat-2026-08-22.md
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import tasks_all  # noqa: E402
from services import task_decision, tasks  # noqa: E402

# `apply()` LIVES IN `tasks`, NOT `task_decision`. It moved there on 2026-08-22
# (`41119015`, "break SCC cycle by moving apply() into tasks.py") and these
# tests were not moved with it, so all eight of them raised AttributeError and
# stayed red for a week. `task_decision` still owns the VERBS and the status
# mapping, which is what it is imported for here.


@pytest.fixture
def scope_root(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_all, "_root_for_scope",
                        lambda scope: tmp_path if scope == "workshop" else None)
    return tmp_path


@pytest.fixture
def client(scope_root):
    app = FastAPI()
    app.include_router(tasks_all.router, prefix="/api")
    return TestClient(app)


def _finished_task(root):
    """A ticket whose worker finished and whose work is committed: the state a
    supervised sweep lands in, and the one the operator is asked to decide on."""
    t = tasks.add(root, title="Add a contract test",
                  body="Objective: the seam is covered.")
    tasks.update(root, t["id"], status="in_review",
                 awaiting={"question": "The work on this ticket is finished and "
                                       "waiting on you."})
    return tasks.get_task(root, t["id"])


def _blocked_task(root):
    t = tasks.add(root, title="Bind a scope to a tracker",
                  body="Objective: one binding per scope.")
    tasks.update(root, t["id"], status="blocked", blocked_reason="needs a decision",
                 awaiting={"question": "Which store?", "kind": "decision"})
    return tasks.get_task(root, t["id"])


# --- the verb, at the service layer -----------------------------------------

def test_accepting_finished_work_marks_it_done(scope_root):
    t = _finished_task(scope_root)
    out = tasks.apply_decision(scope_root, t["id"], task_decision.ACCEPT)
    assert out["status"] == "done"


def test_accepting_writes_no_clarification_into_the_body(scope_root):
    """An acceptance is a fact about the past. The body is read by the NEXT
    attempt, so an answer with no question in it is pure noise there."""
    t = _finished_task(scope_root)
    tasks.apply_decision(scope_root, t["id"], task_decision.ACCEPT, note="Approve")
    body = tasks.get_task(scope_root, t["id"])["body"]
    assert "Clarifications" not in body
    assert "Approve" not in body


def test_accepting_records_who_decided(scope_root):
    t = _finished_task(scope_root)
    tasks.apply_decision(scope_root, t["id"], task_decision.ACCEPT)
    refs = [e.get("ref") for e in tasks.get_task(scope_root, t["id"])["evidence"]]
    assert any("accepted by the operator" in str(r) for r in refs)


def test_sending_back_returns_it_to_the_queue_with_the_note_in_the_body(scope_root):
    t = _finished_task(scope_root)
    out = tasks.apply_decision(scope_root, t["id"], task_decision.SEND_BACK,
                              note="the test does not fail when the shape changes")
    assert out["status"] == "todo"
    assert "the test does not fail when the shape changes" in out["body"]


def test_sending_back_does_not_refund_the_attempt(scope_root):
    """A refund is for a ticket that STOPPED to ask. One that finished and was
    sent back spent a real attempt producing work that needs redoing, and
    refunding it makes the park cap stop meaning what it says."""
    t = _finished_task(scope_root)
    tasks.update(scope_root, t["id"], bump_attempt=True)
    before = tasks.get_task(scope_root, t["id"])["attempts"]
    tasks.apply_decision(scope_root, t["id"], task_decision.SEND_BACK,
                        note="try again", restore_attempt=True)
    assert tasks.get_task(scope_root, t["id"])["attempts"] == before


def test_answering_a_stuck_ticket_may_refund_the_attempt(scope_root):
    t = _blocked_task(scope_root)
    tasks.update(scope_root, t["id"], bump_attempt=True)
    before = tasks.get_task(scope_root, t["id"])["attempts"]
    out = tasks.apply_decision(scope_root, t["id"], task_decision.ANSWER,
                              note="the binding record", restore_attempt=True)
    assert out["status"] == "todo"
    assert tasks.get_task(scope_root, t["id"])["attempts"] == before - 1


def test_accept_refuses_a_ticket_that_is_not_finished(scope_root):
    t = _blocked_task(scope_root)
    with pytest.raises(tasks.DecisionRefused):
        tasks.apply_decision(scope_root, t["id"], task_decision.ACCEPT)


def test_accepting_an_already_done_ticket_is_a_no_op_not_an_error(scope_root):
    """A stale surface must not get stuck: the operator's intent is already
    realized, so saying so beats refusing them."""
    t = _finished_task(scope_root)
    tasks.apply_decision(scope_root, t["id"], task_decision.ACCEPT)
    again = tasks.apply_decision(scope_root, t["id"], task_decision.ACCEPT)
    assert again["status"] == "done"


# --- the words ---------------------------------------------------------------

@pytest.mark.parametrize("said", ["Approve", "approved", "accept", "LGTM", "yes", "ok."])
def test_these_answers_read_as_yes(said):
    assert task_decision.reads_as_approval(said)


@pytest.mark.parametrize("said", ["send back", "no", "the naming is wrong", ""])
def test_these_answers_do_not(said):
    assert not task_decision.reads_as_approval(said)


@pytest.mark.parametrize("said", ["Accept", "Send back", "reject"])
def test_a_tapped_button_is_a_bare_verb(said):
    assert task_decision.is_bare_verb(said)


def test_a_real_note_is_not_a_bare_verb():
    assert not task_decision.is_bare_verb("send it back, the copy is wrong")


# --- the route ---------------------------------------------------------------

def test_review_refuses_approve_on_a_finished_ticket(client, scope_root):
    """The exact call the chat panel used to make. It must now say which of the
    two it meant rather than silently picking the destructive reading."""
    t = _finished_task(scope_root)
    r = client.post(f"/api/tasks/workshop/{t['id']}/review", json={"action": "approve"})
    assert r.status_code == 409
    assert "accept" in r.json()["detail"] and "send-back" in r.json()["detail"]
    assert tasks.get_task(scope_root, t["id"])["status"] == "in_review"


def test_review_accept_marks_it_done(client, scope_root):
    t = _finished_task(scope_root)
    r = client.post(f"/api/tasks/workshop/{t['id']}/review", json={"action": "accept"})
    assert r.status_code == 200
    assert r.json()["task"]["status"] == "done"


def test_review_send_back_carries_the_note_to_the_next_attempt(client, scope_root):
    t = _finished_task(scope_root)
    r = client.post(f"/api/tasks/workshop/{t['id']}/review",
                    json={"action": "send-back", "note": "cover the empty case"})
    assert r.status_code == 200
    assert r.json()["task"]["status"] == "todo"
    assert "cover the empty case" in tasks.get_task(scope_root, t["id"])["body"]


def test_review_approve_still_releases_a_stuck_ticket(client, scope_root):
    """The two callers that send `approve` are correct and must keep working:
    TaskPanel shows it only for a blocked ticket (labelled "Release"), and
    TicketDetail only renders its answer box when the ticket is blocked."""
    t = _blocked_task(scope_root)
    r = client.post(f"/api/tasks/workshop/{t['id']}/review",
                    json={"action": "approve", "note": "the binding record"})
    assert r.status_code == 200
    assert r.json()["task"]["status"] == "todo"
    assert "the binding record" in tasks.get_task(scope_root, t["id"])["body"]
