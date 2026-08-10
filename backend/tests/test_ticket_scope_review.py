"""Is the ticket the right SIZE, and what happens when it is not.

`ticket_readiness` is a regex pass: it cannot tell a well-written ticket from a
well-written ticket that is really four tickets. This is the judgement pass, run
on a draft before approval, and it only ever PROPOSES — applying a split creates
child drafts that each need approving, and the review never edits the ticket's own
text. A review that silently rewrote scope would be the system deciding scope,
which is the thing the operator asked to be able to trust.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import tasks_all  # noqa: E402
from services import tasks, ticket_scope_review as sr  # noqa: E402

SPLIT_REPLY = (
    '{"verdict":"split","reason":"This names three independent deliverables.",'
    '"subtickets":['
    '{"title":"Add the binding record","objective":"Store one binding per scope.",'
    '"output":"A binding round-trips.","entry_points":"backend/services/tracker_bindings.py",'
    '"boundaries":"No UI."},'
    '{"title":"Add the settings form","objective":"Bind a scope from Settings.",'
    '"output":"The form creates a binding.","entry_points":"frontend/src/pages/settings",'
    '"boundaries":"No sync engine."}],'
    '"blockers":["Nobody has registered the GitHub App yet."],'
    '"stages":[]}'
)


def _stub(monkeypatch, raw: str):
    from services import llm_router
    monkeypatch.setattr(llm_router, "call_cloud", lambda *a, **k: {"text": raw})


# ---- parsing the verdict ----------------------------------------------------


def test_a_split_is_parsed_with_composed_bodies(monkeypatch):
    _stub(monkeypatch, SPLIT_REPLY)
    out = asyncio.run(sr.review({"title": "Do the tracker work", "body": "All of it."}))
    assert out["verdict"] == "split"
    assert len(out["subtickets"]) == 2
    # Sub-ticket bodies go through the shared formatter, so a child is born ready.
    assert out["subtickets"][0]["body"].startswith("Objective: Store one binding per scope.")
    assert out["blockers"] == ["Nobody has registered the GitHub App yet."]


def test_a_split_with_one_piece_is_not_a_split(monkeypatch):
    """A model that says "split" and then names one deliverable has contradicted
    itself. The list is the evidence; the label is the cheaper half to discard."""
    _stub(monkeypatch, '{"verdict":"split","reason":"hm","subtickets":'
                       '[{"title":"Just the one","objective":"x","output":"y"}]}')
    out = asyncio.run(sr.review({"title": "A ticket", "body": ""}))
    assert out["verdict"] == "underspecified"
    assert out["subtickets"] == []


def test_a_split_with_no_pieces_is_ok(monkeypatch):
    _stub(monkeypatch, '{"verdict":"split","reason":"hm","subtickets":[]}')
    out = asyncio.run(sr.review({"title": "A ticket", "body": ""}))
    assert out["verdict"] == "ok"


def test_an_unknown_verdict_falls_back_to_ok(monkeypatch):
    _stub(monkeypatch, '{"verdict":"banana","reason":"?"}')
    assert asyncio.run(sr.review({"title": "A ticket"}))["verdict"] == "ok"


def test_a_fenced_reply_is_still_read(monkeypatch):
    _stub(monkeypatch, "Sure!\n```json\n" + SPLIT_REPLY + "\n```")
    assert asyncio.run(sr.review({"title": "A ticket"}))["verdict"] == "split"


def test_an_unusable_reply_raises(monkeypatch):
    _stub(monkeypatch, "I could not do that.")
    with pytest.raises(ValueError):
        asyncio.run(sr.review({"title": "A ticket"}))


def test_a_subticket_cannot_be_split_again(monkeypatch):
    """Depth is capped at two levels, and the reason is stated rather than
    swallowed, so the operator learns why the split is not offered."""
    _stub(monkeypatch, SPLIT_REPLY)
    out = asyncio.run(sr.review({"title": "A child", "parent": "abc"}))
    assert out["verdict"] == "underspecified"
    assert out["subtickets"] == []
    assert "depth is capped" in out["reason"]


# ---- applying a split -------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_all, "_root_for_scope",
                        lambda scope: tmp_path if scope == "workshop" else None)
    app = FastAPI()
    app.include_router(tasks_all.router, prefix="/api")
    return TestClient(app), tmp_path


TWO = [{"title": "Add the binding record", "body": "Objective: store it."},
       {"title": "Add the settings form", "body": "Objective: bind from Settings."}]


def test_splitting_creates_child_drafts(client):
    c, root = client
    parent = tasks.add(root, title="Do the tracker work", status="draft")
    r = c.post(f"/api/tasks/workshop/{parent['id']}/split", json={"subtickets": TWO})
    assert r.status_code == 200, r.text
    kids = r.json()["subtickets"]
    assert len(kids) == 2
    # Children need approving like any other proposal.
    assert all(k["status"] == "draft" for k in kids)
    assert all(k["parent"] == parent["id"] for k in kids)


def test_the_parent_keeps_its_text_and_is_not_cancelled(client):
    """The original wording is the record of what was asked for."""
    c, root = client
    parent = tasks.add(root, title="Do the tracker work",
                       body="Objective: all of it.", status="draft")
    c.post(f"/api/tasks/workshop/{parent['id']}/split", json={"subtickets": TWO})
    got = tasks.get_task(root, parent["id"])
    assert got["status"] == "draft"
    assert got["title"] == "Do the tracker work"
    assert "Objective: all of it." in got["body"]


def test_the_split_is_recorded_on_the_parent(client):
    c, root = client
    parent = tasks.add(root, title="Do the tracker work", status="draft")
    c.post(f"/api/tasks/workshop/{parent['id']}/split", json={"subtickets": TWO})
    kinds = [e.get("kind") for e in tasks.get_task(root, parent["id"])["evidence"]]
    assert "split" in kinds


def test_neither_the_parent_nor_the_children_are_runnable_yet(client):
    c, root = client
    parent = tasks.add(root, title="Do the tracker work", status="draft")
    c.post(f"/api/tasks/workshop/{parent['id']}/split", json={"subtickets": TWO})
    assert tasks.next_ready(root) is None


def test_a_split_needs_at_least_two_pieces(client):
    c, root = client
    parent = tasks.add(root, title="A ticket", status="draft")
    r = c.post(f"/api/tasks/workshop/{parent['id']}/split",
               json={"subtickets": [TWO[0]]})
    assert r.status_code == 400


def test_a_subticket_cannot_be_split(client):
    c, root = client
    parent = tasks.add(root, title="A ticket", status="draft")
    c.post(f"/api/tasks/workshop/{parent['id']}/split", json={"subtickets": TWO})
    child = [t for t in tasks.list_tasks(root) if t.get("parent")][0]
    r = c.post(f"/api/tasks/workshop/{child['id']}/split", json={"subtickets": TWO})
    assert r.status_code == 409
    assert "depth" in r.text


def test_a_child_is_attributed_to_the_review(client):
    c, root = client
    parent = tasks.add(root, title="A ticket", status="draft",
                       source={"kind": "chat", "ref": "t1"})
    c.post(f"/api/tasks/workshop/{parent['id']}/split", json={"subtickets": TWO})
    child = [t for t in tasks.list_tasks(root) if t.get("parent")][0]
    # The request still traces to the chat; the spec was written by the review.
    assert child["source"] == {"kind": "chat", "ref": "t1"}
    assert child["scoped_by"] == "agent:scope-review"
