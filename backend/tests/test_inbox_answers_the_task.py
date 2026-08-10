"""Answering an agent in the inbox reaches the work, not just the row.

The inbox item is a PROJECTION of a block that lives on a task. Resolving it used
to write `state` and `answer` into `inbox/items.json` and stop there, so:

  * the task kept its `awaiting` record and stayed blocked;
  * run_inbox re-raised the identical question on its next pass (dedupe only
    matches OPEN items, and the answered one no longer is), so the thing the
    operator had just dealt with came back within twenty seconds;
  * "Approve" on a block proposing a permission granted nothing at all.

The real unblock path is `POST /tasks/{scope}/{id}/review`, and these tests pin
that the inbox now goes through it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import inbox as inbox_routes  # noqa: E402
from routes import tasks_all  # noqa: E402
from services import inbox, ownership, run_inbox, tasks  # noqa: E402


@pytest.fixture
def scope_root(tmp_path, monkeypatch):
    root = tmp_path / "workshop"
    root.mkdir()
    monkeypatch.setattr(tasks_all, "_root_for_scope",
                        lambda scope: root if scope == "workshop" else None)
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    ownership.set_current_owner(1)
    try:
        yield root
    finally:
        ownership.clear_current_owner()


@pytest.fixture
def client(scope_root):
    app = FastAPI()
    app.include_router(inbox_routes.router, prefix="/api")
    return TestClient(app)


def _blocked(root, *, proposal: dict | None = None, question="Which store holds the token?"):
    t = tasks.add(root, title="Bind a scope to a tracker", body="Objective: one binding.")
    awaiting = {"question": question, "kind": "decision"}
    if proposal:
        awaiting["proposal"] = proposal
    tasks.update(root, t["id"], status="blocked", blocked_reason="needs a decision",
                 awaiting=awaiting)
    return tasks.get_task(root, t["id"])


def _raise_for(task, *, options=None):
    return inbox.raise_item(
        kind="question", agent="agent:worker", scope="workshop",
        title=task["text"].split("\n")[0], body="Which store holds the token?",
        options=options or [], ticket_id=task["id"],
        dedupe_key=f"task:workshop:{task['id']}",
    )


def test_answering_releases_the_task_and_writes_the_answer_into_it(client, scope_root):
    t = _blocked(scope_root)
    item = _raise_for(t)

    r = client.post(f"/api/inbox/{item['id']}/resolve",
                    json={"answer": "secrets_store, per binding.", "dismissed": False})
    assert r.status_code == 200, r.text

    after = tasks.get_task(scope_root, t["id"])
    assert after["status"] == "todo"
    assert not after.get("awaiting")
    # An answer a worker cannot read is not an answer: it goes in the body.
    assert "secrets_store, per binding." in after["text"]


def test_approve_on_a_proposal_actually_mints_the_grant(client, scope_root):
    t = _blocked(scope_root, proposal={"uri": "store://budget/plan", "verb": "write"})
    item = _raise_for(t, options=["Approve", "Decline"])

    r = client.post(f"/api/inbox/{item['id']}/resolve",
                    json={"answer": "Approve", "dismissed": False})
    assert r.status_code == 200, r.text
    grant = r.json()["review"]["grant"]
    assert grant["store_uri"] == "store://budget/plan"
    assert "write" in grant["verbs"]

    after = tasks.get_task(scope_root, t["id"])
    assert after["status"] == "todo"
    assert any("grant" == e.get("kind") for e in after.get("evidence") or [])


def test_declining_answers_the_question_rather_than_granting(client, scope_root):
    t = _blocked(scope_root, proposal={"uri": "store://budget/plan", "verb": "write"})
    item = _raise_for(t, options=["Approve", "Decline"])

    r = client.post(f"/api/inbox/{item['id']}/resolve",
                    json={"answer": "Decline", "dismissed": False})
    assert r.status_code == 200, r.text
    assert "grant" not in r.json()["review"]

    after = tasks.get_task(scope_root, t["id"])
    assert after["status"] == "todo"
    assert "Decline" in after["text"]


def test_the_same_question_does_not_come_straight_back(client, scope_root):
    t = _blocked(scope_root)
    item = _raise_for(t)
    client.post(f"/api/inbox/{item['id']}/resolve",
                json={"answer": "secrets_store.", "dismissed": False})

    # The next reconcile pass is what used to re-raise it twenty seconds later.
    run_inbox.sync([("workshop", scope_root)])
    assert inbox.list_items(state="open") == []


def test_dismissing_leaves_the_block_in_place_and_stops_it_asking(client, scope_root):
    t = _blocked(scope_root)
    item = _raise_for(t)

    r = client.post(f"/api/inbox/{item['id']}/resolve", json={"answer": None, "dismissed": True})
    assert r.status_code == 200, r.text

    after = tasks.get_task(scope_root, t["id"])
    # Dismiss is not an answer: the task is still blocked, it just stops asking.
    assert after["status"] == "blocked"
    assert after["awaiting"]["dismissed"] is True

    run_inbox.sync([("workshop", scope_root)])
    assert inbox.list_items(state="open") == []


def test_a_stale_row_can_still_be_dismissed(client, scope_root):
    t = _blocked(scope_root)
    item = _raise_for(t)
    # Answered somewhere else in the meantime (the Review tab, a worker retry).
    tasks.update(scope_root, t["id"], status="todo", blocked_reason="", awaiting=None)

    r = client.post(f"/api/inbox/{item['id']}/resolve", json={"answer": None, "dismissed": True})
    assert r.status_code == 200, r.text
    assert inbox.get_item(item["id"])["state"] == "dismissed"


def test_a_refused_review_leaves_the_row_open(client, scope_root):
    t = _blocked(scope_root, proposal={"verb": "write"})  # a proposal with no uri
    item = _raise_for(t, options=["Approve", "Decline"])

    r = client.post(f"/api/inbox/{item['id']}/resolve",
                    json={"answer": "Approve", "dismissed": False})
    assert r.status_code == 400
    # The write did not happen, so the row must not have closed behind it.
    assert inbox.get_item(item["id"])["state"] == "open"


def test_an_item_that_is_not_about_a_task_is_untouched(client, scope_root):
    item = inbox.raise_item(kind="notify", agent="agent:keeper", scope="workshop",
                            title="Ran the nightly sweep", dedupe_key="sweep:2026-08-09")
    r = client.post(f"/api/inbox/{item['id']}/resolve",
                    json={"answer": "noted", "dismissed": False})
    assert r.status_code == 200
    assert "review" not in r.json()
    assert inbox.get_item(item["id"])["state"] == "answered"
