"""Answering a blocked ticket's question reaches the attempt it was meant to help.

The audit that opened this work found ZERO tickets carrying an `awaiting` record
across 25 live stores, and the reason was not that the schema was missing. It was
that answering did nothing useful: the reply was recorded as `evidence`, and a
worker is handed the ticket's TEXT and nothing else (scripts/project_ralph.sh
passes `$TASK_TEXT` to ralph.sh). The operator could answer a question and the
next attempt would ask it again, having never seen the reply.

These tests pin the round trip: a question is raised, an answer is given through
the review surface, the answer lands in the ticket BODY, and the ticket is
runnable again.
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
def scope_root(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_all, "_root_for_scope",
                        lambda scope: tmp_path if scope == "workshop" else None)
    return tmp_path


@pytest.fixture
def client(scope_root):
    app = FastAPI()
    app.include_router(tasks_all.router, prefix="/api")
    return TestClient(app)


def _blocked_task(root, question="Which store should the token live in?"):
    """A ticket stopped on a question, the way a strict-latitude worker leaves it
    (`project_tasks.py set-awaiting`)."""
    t = tasks.add(root, title="Bind a scope to a tracker",
                  body="Objective: one binding per scope.")
    tasks.update(root, t["id"], status="blocked",
                 blocked_reason="needs a decision",
                 awaiting={"question": question, "kind": "decision"})
    return tasks.get_task(root, t["id"])


def test_the_answer_lands_in_the_body_not_only_in_evidence(client, scope_root):
    t = _blocked_task(scope_root)
    assert t["awaiting"]["question"]

    r = client.post(f"/api/tasks/workshop/{t['id']}/review",
                    json={"action": "approve", "note": "secrets_store, per binding."})
    assert r.status_code == 200, r.text
    out = r.json()["task"]

    # The body is what the worker is handed, so that is where the answer must be.
    assert tasks.CLARIFICATION_HEADING in out["body"]
    assert "secrets_store, per binding." in out["body"]
    assert "Which store should the token live in?" in out["body"]
    # And it is really in the ticket TEXT the sweep passes to the worker.
    assert "secrets_store, per binding." in out["text"]


def test_answering_releases_the_ticket_and_clears_the_question(client, scope_root):
    t = _blocked_task(scope_root)
    r = client.post(f"/api/tasks/workshop/{t['id']}/review",
                    json={"action": "approve", "note": "Use the second option."})
    out = r.json()["task"]
    assert out["status"] == "todo"
    assert not out["awaiting"]
    assert not out["blocked_reason"]


def test_the_original_objective_survives_the_answer(client, scope_root):
    t = _blocked_task(scope_root)
    client.post(f"/api/tasks/workshop/{t['id']}/review",
                json={"action": "approve", "note": "secrets_store."})
    out = tasks.get_task(scope_root, t["id"])
    assert out["title"] == "Bind a scope to a tracker"
    assert "Objective: one binding per scope." in out["body"]


def test_two_answers_share_one_heading(client, scope_root):
    t = _blocked_task(scope_root)
    client.post(f"/api/tasks/workshop/{t['id']}/review",
                json={"action": "approve", "note": "First answer."})
    tasks.update(scope_root, t["id"], status="blocked",
                 awaiting={"question": "And the port?", "kind": "decision"})
    client.post(f"/api/tasks/workshop/{t['id']}/review",
                json={"action": "approve", "note": "Second answer."})

    body = tasks.get_task(scope_root, t["id"])["body"]
    assert body.count(tasks.CLARIFICATION_HEADING) == 1
    assert "First answer." in body and "Second answer." in body
    assert "And the port?" in body


def test_a_denied_ticket_is_cancelled_and_not_clarified(client, scope_root):
    """Deny is not an answer to the question; it ends the work. Writing a
    clarification into a cancelled ticket would suggest a next attempt exists."""
    t = _blocked_task(scope_root)
    r = client.post(f"/api/tasks/workshop/{t['id']}/review",
                    json={"action": "deny", "note": "Not doing this."})
    out = r.json()["task"]
    assert out["status"] == "cancelled"
    assert tasks.CLARIFICATION_HEADING not in out["body"]


def test_dismiss_leaves_the_block_in_place(client, scope_root):
    """Dismiss takes a question off the review queue WITHOUT answering it, so the
    ticket stays blocked and the body gains nothing."""
    t = _blocked_task(scope_root)
    r = client.post(f"/api/tasks/workshop/{t['id']}/review",
                    json={"action": "dismiss"})
    out = r.json()["task"]
    assert out["status"] == "blocked"
    assert out["awaiting"]["dismissed"] is True
    assert tasks.CLARIFICATION_HEADING not in out["body"]


def test_an_empty_answer_writes_nothing(scope_root):
    t = _blocked_task(scope_root)
    assert tasks.append_clarification(scope_root, t["id"],
                                      question="Which store?", answer="   ") is None
    assert tasks.CLARIFICATION_HEADING not in tasks.get_task(scope_root, t["id"])["body"]


# ---- the question reaches the chat that filed the ticket --------------------
# The queue is the right surface for someone already watching the queue, and the
# wrong one for the person who asked in a conversation. Both, or the question is
# effectively unasked.


@pytest.fixture
def asked(monkeypatch):
    """Record every question delivery instead of posting one."""
    from services import question_report
    calls: list[dict] = []
    monkeypatch.setattr(question_report, "post_for_task",
                        lambda task, request, scope=None: calls.append(
                            {"task": task["id"], "ask": request.get("question"),
                             "scope": scope}) or True)
    return calls


def test_a_question_on_a_chat_filed_ticket_reaches_that_chat(scope_root, asked):
    t = tasks.add(scope_root, title="Bind a scope to a tracker",
                  source={"kind": "chat", "ref": "thread-abc"})
    tasks.update(scope_root, t["id"], status="blocked",
                 awaiting={"question": "Which store?"})
    assert [c["ask"] for c in asked] == ["Which store?"]


def test_the_question_is_delivered_once_not_on_every_later_write(scope_root, asked):
    """The choke point fires on the TRANSITION into asking. A heartbeat, a note or
    a priority bump on an already-blocked ticket must not re-ask."""
    t = tasks.add(scope_root, title="A ticket",
                  source={"kind": "chat", "ref": "thread-abc"})
    tasks.update(scope_root, t["id"], status="blocked",
                 awaiting={"question": "Which store?"})
    tasks.update(scope_root, t["id"], priority=5)
    tasks.update(scope_root, t["id"], evidence={"kind": "note", "ref": "still stuck"})
    assert len(asked) == 1


def test_answering_then_asking_again_delivers_the_second_question(scope_root, asked):
    t = tasks.add(scope_root, title="A ticket",
                  source={"kind": "chat", "ref": "thread-abc"})
    tasks.update(scope_root, t["id"], status="blocked", awaiting={"question": "First?"})
    tasks.update(scope_root, t["id"], status="todo")      # answered: clears awaiting
    tasks.update(scope_root, t["id"], status="blocked", awaiting={"question": "Second?"})
    assert [c["ask"] for c in asked] == ["First?", "Second?"]


def test_a_ticket_with_no_chat_asks_nobody(scope_root):
    """Delivery is decided by the ticket's own source, inside post_for_task, so
    the real function is exercised here rather than the spy."""
    from services import question_report
    t = tasks.add(scope_root, title="Filed by the operator, not from a chat")
    assert question_report.post_for_task(
        t, {"question": "Which store?"}, scope="workshop") is False


def test_a_question_with_no_text_interrupts_nobody(scope_root):
    from services import question_report
    t = tasks.add(scope_root, title="A ticket",
                  source={"kind": "chat", "ref": "thread-abc"})
    assert question_report.post_for_task(t, {"question": "  "}, scope="workshop") is False


def test_the_composed_question_leads_with_the_ask(scope_root):
    from services import question_report
    t = tasks.add(scope_root, title="Bind a scope to a tracker")
    msg = question_report._compose(
        t, {"ask": "Which store should the token live in?",
            "why": "Two stores both look right.",
            "options": ["secrets_store", "the binding record"],
            "default": "secrets_store",
            "unblocks_if": "you name one"}, "workshop")
    assert "Bind a scope to a tracker" in msg
    assert "> Which store should the token live in?" in msg
    assert "- secrets_store" in msg and "- the binding record" in msg
    assert "Two stores both look right." in msg
    assert f"/tickets/workshop/{t['id']}" in msg


def test_the_question_really_lands_in_the_thread(scope_root, tmp_path, monkeypatch):
    """End to end against the real chat store, not the spy.

    Everything above proves the trigger and the wording. This proves the delivery:
    a question raised on a ticket filed from a conversation is readable in that
    conversation's transcript afterwards.
    """
    from services import chat_store, ownership, question_report, run_report

    runtime = tmp_path / "runtime"
    monkeypatch.setattr(ownership, "_RUNTIME", runtime)
    monkeypatch.setattr(run_report, "_RUNTIME", runtime)
    ownership.set_current_owner(6)
    try:
        thread = chat_store.create_thread(title="Where the work was asked for")
        t = tasks.add(scope_root, title="Bind a scope to a tracker",
                      source={"kind": "chat", "ref": thread["id"]})

        assert question_report.post_for_task(
            t, {"ask": "Which store should the token live in?",
                "options": ["secrets_store", "the binding record"]},
            scope="workshop") is True

        msgs = chat_store.get_thread(thread["id"])["messages"]
        assert len(msgs) == 1
        assert msgs[0]["role"] == "assistant"
        assert "Which store should the token live in?" in msgs[0]["content"]
        assert msgs[0]["meta"]["kind"] == "ticket_question"
        assert msgs[0]["meta"]["ticket"] == t["id"]
    finally:
        ownership.clear_current_owner()


def test_a_clarification_on_a_ticket_with_no_body(scope_root):
    t = tasks.add(scope_root, title="A ticket with no description")
    tasks.append_clarification(scope_root, t["id"], question="Why?", answer="Because.")
    out = tasks.get_task(scope_root, t["id"])
    assert out["title"] == "A ticket with no description"
    assert out["body"].startswith(tasks.CLARIFICATION_HEADING)
    assert "Because." in out["body"]


# --- who spent the attempt, and who did not ---------------------------------
# `POST /review` refunds an attempt on release, on the sound reasoning that an
# ANSWERED question is not a failed attempt. That reasoning assumes a run was
# claimed. The queue triager breaks the assumption: it raises `underspecified`
# from a review pass with no run and no attempt spent, so an unconditional refund
# CREDITS an attempt the ticket never had. It floors at zero, so nothing breaks
# visibly — a ticket triaged and answered twice just quietly earns back two real
# attempts against its park cap, and the cap stops meaning what it says.

def _blocked_by(root, raised_by, attempts=2):
    t = tasks.add(root, title="Bind a scope to a tracker",
                  body="Objective: one binding per scope.")
    for _ in range(attempts):
        tasks.update(root, t["id"], bump_attempt=True)
    tasks.update(root, t["id"], status="blocked", blocked_reason="needs a decision",
                 awaiting={"ask": "What does done look like?", "kind": "missing-info",
                           "raised_by": raised_by})
    return tasks.get_task(root, t["id"])


def test_answering_a_worker_raised_block_still_refunds(client, scope_root):
    """The existing behaviour, pinned so the fix below cannot overreach."""
    t = _blocked_by(scope_root, "worker")
    assert t["attempts"] == 2
    r = client.post(f"/api/tasks/workshop/{t['id']}/review",
                    json={"action": "approve", "note": "Done means the route returns 200."})
    assert r.status_code == 200
    assert tasks.get_task(scope_root, t["id"])["attempts"] == 1


def test_answering_a_triager_raised_block_does_not_refund(client, scope_root):
    """No run was claimed, so there is no attempt to give back."""
    t = _blocked_by(scope_root, "agent:queue-triager")
    assert t["attempts"] == 2
    r = client.post(f"/api/tasks/workshop/{t['id']}/review",
                    json={"action": "approve", "note": "Done means the route returns 200."})
    assert r.status_code == 200
    assert tasks.get_task(scope_root, t["id"])["attempts"] == 2


def test_a_block_with_no_raiser_still_refunds(client, scope_root):
    """Legacy records carry no `raised_by`. They predate the triager, so they are
    worker-raised by construction and must keep the old behaviour."""
    t = _blocked_by(scope_root, "")
    r = client.post(f"/api/tasks/workshop/{t['id']}/review",
                    json={"action": "approve", "note": "An answer."})
    assert r.status_code == 200
    assert tasks.get_task(scope_root, t["id"])["attempts"] == 1


def test_the_ticket_is_released_either_way(client, scope_root):
    for raiser in ("worker", "agent:queue-triager"):
        t = _blocked_by(scope_root, raiser)
        client.post(f"/api/tasks/workshop/{t['id']}/review",
                    json={"action": "approve", "note": "An answer."})
        out = tasks.get_task(scope_root, t["id"])
        assert out["status"] == "todo", raiser
        assert out["awaiting"] is None, raiser
