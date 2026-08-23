"""Answering a waiting ticket reaches the work, not just the row on screen.

The row IS the ticket now: `POST /inbox/{id}/resolve` is handed the derived id
`task:<scope>:<id>` (`services/chat_work.needs_you_id`), reads the ticket back
out of it, and answers it through `POST /tasks/{scope}/{id}/review`. Nothing is
stored, so nothing can go stale between the answer and the next read.

Two defects are pinned here, both from the era when the row was a copy:

  * resolving wrote `state` and `answer` into `inbox/items.json` and stopped, so
    the task kept its `awaiting` record, stayed blocked, and the same question
    was re-raised on the next pass twenty seconds later;
  * "Approve" on a block proposing a permission granted nothing at all.
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
from services import chat_work, inbox, ownership, tasks  # noqa: E402


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


def _row_id(task) -> str:
    """The id the client actually holds: the one the work payload derived."""
    return chat_work.needs_you_id("workshop", task["id"])


def test_answering_releases_the_task_and_writes_the_answer_into_it(client, scope_root):
    t = _blocked(scope_root)

    r = client.post(f"/api/inbox/{_row_id(t)}/resolve",
                    json={"answer": "secrets_store, per binding.", "dismissed": False})
    assert r.status_code == 200, r.text

    after = tasks.get_task(scope_root, t["id"])
    assert after["status"] == "todo"
    assert not after.get("awaiting")
    # An answer a worker cannot read is not an answer: it goes in the body.
    assert "secrets_store, per binding." in after["text"]


def test_approve_on_a_proposal_actually_mints_the_grant(client, scope_root):
    t = _blocked(scope_root, proposal={"uri": "store://budget/plan", "verb": "write"})

    r = client.post(f"/api/inbox/{_row_id(t)}/resolve",
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

    r = client.post(f"/api/inbox/{_row_id(t)}/resolve",
                    json={"answer": "Decline", "dismissed": False})
    assert r.status_code == 200, r.text
    assert "grant" not in r.json()["review"]

    after = tasks.get_task(scope_root, t["id"])
    assert after["status"] == "todo"
    assert "Decline" in after["text"]


def test_accepting_finished_work_closes_the_ticket(client, scope_root):
    """The verb comes from the ticket's STATUS. `approve` on a finished ticket
    used to mean "release the block", which un-accepted committed work."""
    t = tasks.add(scope_root, title="Move the lock off /tmp", body="")
    tasks.update(scope_root, t["id"], status="in_review")

    r = client.post(f"/api/inbox/{chat_work.needs_you_id('workshop', t['id'])}/resolve",
                    json={"answer": "Accept", "dismissed": False})
    assert r.status_code == 200, r.text

    after = tasks.get_task(scope_root, t["id"])
    assert after["status"] == "done"
    # A tapped button is the decision, not an instruction to a next attempt.
    assert "Accept" not in after["text"]


def test_the_same_question_does_not_come_straight_back(client, scope_root):
    t = _blocked(scope_root)
    client.post(f"/api/inbox/{_row_id(t)}/resolve",
                json={"answer": "secrets_store.", "dismissed": False})

    # Nothing was stored to go stale, and the ticket no longer asks.
    assert inbox.list_items(state="open") == []
    assert not tasks.get_task(scope_root, t["id"]).get("awaiting")


def test_dismissing_leaves_the_block_in_place_and_stops_it_asking(client, scope_root):
    t = _blocked(scope_root)

    r = client.post(f"/api/inbox/{_row_id(t)}/resolve",
                    json={"answer": None, "dismissed": True})
    assert r.status_code == 200, r.text

    after = tasks.get_task(scope_root, t["id"])
    # Dismiss is not an answer: the task is still blocked, it just stops asking.
    assert after["status"] == "blocked"
    assert after["awaiting"]["dismissed"] is True


def test_a_stale_row_can_still_be_dismissed(client, scope_root):
    t = _blocked(scope_root)
    # Answered somewhere else in the meantime (the Review tab, a worker retry).
    tasks.update(scope_root, t["id"], status="todo", blocked_reason="", awaiting=None)

    r = client.post(f"/api/inbox/{_row_id(t)}/resolve",
                    json={"answer": None, "dismissed": True})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "dismissed"


def test_a_half_formed_proposal_cannot_mint_a_grant(client, scope_root):
    """`tasks._normalize_awaiting` drops a proposal with no uri, so this ticket is
    not offering a grant and `Approve` is read as the plain answer it is.

    The stored copy used to carry `options=["Approve", "Decline"]` whatever the
    record said, and the route trusted the copy: a surface asserting something
    the ticket never said, which is the whole class of defect that goes away when
    there is only one place the fact lives."""
    t = _blocked(scope_root, proposal={"verb": "write"})

    r = client.post(f"/api/inbox/{_row_id(t)}/resolve",
                    json={"answer": "Approve", "dismissed": False})
    assert r.status_code == 200, r.text
    assert "grant" not in r.json()["review"]
    assert tasks.get_task(scope_root, t["id"])["status"] == "todo"


def test_answering_a_ticket_that_is_gone_says_so(client, scope_root):
    """A derived row can name a ticket that has since been deleted. Saying so
    beats closing quietly, and clearing it off the surface still works."""
    t = _blocked(scope_root)
    row = _row_id(t)
    assert tasks.remove(scope_root, t["id"])

    r = client.post(f"/api/inbox/{row}/resolve",
                    json={"answer": "secrets_store.", "dismissed": False})
    assert r.status_code == 404

    assert client.post(f"/api/inbox/{row}/resolve",
                       json={"answer": None, "dismissed": True}).status_code == 200


def test_a_row_that_names_no_ticket_is_closed_and_nothing_else(client, scope_root):
    item = inbox.raise_item(kind="notify", agent="agent:keeper", scope="workshop",
                            title="Ran the nightly sweep", dedupe_key="sweep:2026-08-09")
    r = client.post(f"/api/inbox/{item['id']}/resolve",
                    json={"answer": "noted", "dismissed": False})
    assert r.status_code == 200
    assert "review" not in r.json()
    assert inbox.get_item(item["id"])["state"] == "answered"
