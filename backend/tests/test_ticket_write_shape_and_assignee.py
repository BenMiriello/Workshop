"""Two defects a review found in the ticket write path, pinned together because
they are both about a write returning the wrong thing.

1. A mutating route returned an UNPROJECTED record. `patch_one`, `/approve`,
   `/split` and `/duplicate-of` each hand-built `dict(task) + scope + latitude`
   and stopped, so the record they returned had no `state_type`, `ready`,
   `blocked_by`, `links`, `readiness` or `origin` — all of which the detail GET
   puts there. `TicketDetail.saveEdits` does `setTask(await r.json())`, so the
   resolved dependency list and the readiness report visibly emptied on save and
   refilled on the next poll. Every one of these tests compares the mutating
   route's record against the detail GET's, key for key, so a future route that
   forgets `_detail` fails here rather than on screen.

2. The assignee could not be written AT ALL. `tasks.update` used its `assignee`
   argument both as the value to write and as the owner filter used to FIND the
   record, so naming any assignee the ticket did not already have matched
   nothing, returned None, and the route 404'd "task not found" while silently
   dropping the rest of the save (title, body, status and all). The filter is
   not removable — an agent must not be able to mutate another delegate's task —
   so the two meanings are now two parameters, and both halves are pinned below.
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
def roots(tmp_path):
    a = tmp_path / "workshop"
    b = tmp_path / "other"
    a.mkdir()
    b.mkdir()
    return {"workshop": a, "project:other": b}


@pytest.fixture
def client(roots, monkeypatch):
    monkeypatch.setattr(tasks_all, "_root_for_scope", lambda s: roots.get(s))
    monkeypatch.setattr(tasks_all, "_scope_roots", lambda: list(roots.items()))
    monkeypatch.setattr(tasks_all, "scope_roots", lambda: list(roots.items()),
                        raising=False)
    app = FastAPI()
    app.include_router(tasks_all.router, prefix="/api")
    return TestClient(app)


# The keys the detail GET promises. A single-task response missing any of them is
# a response the ticket page renders as "this ticket has none of that".
_DETAIL_KEYS = {"scope", "origin", "readiness", "links", "state_type", "ready",
                "blocked_by", "effective_latitude"}


def _assert_is_a_detail_record(rec: dict, client, scope: str) -> None:
    """The record must carry every projected key AND agree with the detail GET on
    each one. Presence alone would pass on a key stamped with a placeholder."""
    assert _DETAIL_KEYS <= set(rec), sorted(_DETAIL_KEYS - set(rec))
    canonical = client.get(
        f"/api/tasks/{scope}/{rec['id']}").json()
    for key in _DETAIL_KEYS:
        assert rec[key] == canonical[key], key


# ---- Fix 2: every single-task route returns the detail shape ----------------

def test_patch_returns_the_same_shape_as_the_detail_get(client, roots):
    dep = tasks.add(roots["workshop"], title="upstream")
    t = tasks.add(roots["workshop"], title="downstream", depends_on=[dep["id"]])
    got = client.patch(f"/api/tasks/workshop/{t['id']}",
                       json={"title": "downstream, renamed"})
    assert got.status_code == 200, got.text
    rec = got.json()
    assert rec["title"] == "downstream, renamed"
    _assert_is_a_detail_record(rec, client, "workshop")


def test_a_saved_edit_keeps_its_resolved_dependencies(client, roots):
    """The reported symptom: the resolved dependency key under the edge inputs
    blanked out right after a save until the next poll refilled it."""
    dep = tasks.add(roots["workshop"], title="upstream")
    t = tasks.add(roots["workshop"], title="downstream", depends_on=[dep["id"]])
    rec = client.patch(f"/api/tasks/workshop/{t['id']}",
                       json={"body": "more detail"}).json()
    assert [d["id"] for d in rec["links"]["depends_on"]] == [dep["id"]]
    assert [d["id"] for d in rec["blocked_by"]] == [dep["id"]]
    assert rec["ready"] is False
    assert rec["state_type"] == "unstarted"


def test_approve_returns_the_detail_shape(client, roots):
    t = tasks.add(roots["workshop"], title="Approve me", status="draft")
    out = client.post(f"/api/tasks/workshop/{t['id']}/approve", json={"run": False})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["task"]["status"] == "todo"
    assert body["task"]["state_type"] == "unstarted"
    # The route's own top-level readiness report is still there.
    assert "readiness" in body
    _assert_is_a_detail_record(body["task"], client, "workshop")


def test_split_returns_the_detail_shape_for_the_parent(client, roots):
    t = tasks.add(roots["workshop"], title="Too big")
    out = client.post(f"/api/tasks/workshop/{t['id']}/split", json={"subtickets": [
        {"title": "Part one"}, {"title": "Part two"}]})
    assert out.status_code == 200, out.text
    body = out.json()
    assert len(body["subtickets"]) == 2
    parent = body["parent"]
    assert parent["state_type"] == "backlog"
    # The children exist NOW, so a parent record built from the pre-split store
    # would report none of them.
    assert sorted(c["title"] for c in parent["links"]["children"]) == \
        ["Part one", "Part two"]
    _assert_is_a_detail_record(parent, client, "workshop")


def test_duplicate_of_returns_the_detail_shape(client, roots):
    canon = tasks.add(roots["workshop"], title="The real one")
    dup = tasks.add(roots["workshop"], title="Same thing again")
    out = client.post(f"/api/tasks/workshop/{dup['id']}/duplicate-of",
                      json={"target_id": canon["id"]})
    assert out.status_code == 200, out.text
    rec = out.json()["task"]
    assert rec["status"] == "cancelled"
    assert rec["state_type"] == "canceled"
    assert [r["id"] for r in rec["links"]["relates_to"]] == [canon["id"]]
    _assert_is_a_detail_record(rec, client, "workshop")


def test_review_returns_the_detail_shape(client, roots):
    t = tasks.add(roots["workshop"], title="Stuck")
    tasks.update(roots["workshop"], t["id"], status="blocked",
                 awaiting={"question": "which one?"})
    out = client.post(f"/api/tasks/workshop/{t['id']}/review",
                      json={"action": "approve", "note": "the first one"})
    assert out.status_code == 200, out.text
    rec = out.json()["task"]
    assert rec["status"] == "todo"
    _assert_is_a_detail_record(rec, client, "workshop")


def test_a_posted_comment_returns_the_detail_shape(client, roots):
    """`TicketDetail.postComment` also does `setTask(await r.json())`."""
    t = tasks.add(roots["workshop"], title="Discuss me")
    rec = client.post(f"/api/tasks/workshop/{t['id']}/comments",
                      json={"text": "a thought"}).json()
    assert [c["text"] for c in rec["comments"]] == ["a thought"]
    _assert_is_a_detail_record(rec, client, "workshop")
    cid = rec["comments"][0]["id"]
    gone = client.delete(f"/api/tasks/workshop/{t['id']}/comments/{cid}").json()
    assert gone["comments"] == []
    _assert_is_a_detail_record(gone, client, "workshop")


def test_create_returns_the_detail_shape(client, roots):
    out = client.post("/api/tasks", json={"scope": "workshop", "title": "Brand new"})
    assert out.status_code == 200, out.text
    rec = out.json()["task"]
    assert rec["title"] == "Brand new"
    _assert_is_a_detail_record(rec, client, "workshop")


def test_no_single_task_route_hand_builds_its_own_shape():
    """The structural half of the fix: one helper, so a route added later cannot
    quietly ship the old five-key record. `_with_latitude` is what the
    hand-built shape ended at, and only `_detail` and the list loop may call it."""
    import inspect
    src = inspect.getsource(tasks_all)
    callers = sorted(line.strip() for line in src.splitlines()
                     if "_with_latitude(" in line and "def _with_latitude" not in line)
    assert callers == ["out.append(_with_latitude(rec, skey, lat_cache))",
                       "return _with_latitude(rec, scope)"], callers


# ---- Fix 3: the assignee, at the store level --------------------------------

def test_update_could_not_change_an_assignee_through_the_legacy_parameter(roots):
    """The bug itself, pinned so the legacy parameter's shape is not mistaken for
    a working reassign. `assignee=` is match AND write: naming a value the ticket
    does not carry matches nothing."""
    root = roots["workshop"]
    t = tasks.add(root, title="Mine", assignee="agent:alpha")
    assert tasks.update(root, t["id"], assignee="agent:beta") is None
    assert tasks.get_task(root, t["id"])["assignee"] == "agent:alpha"


def test_set_assignee_writes_without_filtering(roots):
    root = roots["workshop"]
    t = tasks.add(root, title="Mine", assignee="agent:alpha")
    got = tasks.update(root, t["id"], set_assignee="agent:beta")
    assert got is not None
    assert got["assignee"] == "agent:beta"
    assert tasks.get_task(root, t["id"])["assignee"] == "agent:beta"


def test_clearing_the_assignee_hands_the_ticket_back_to_the_operator(roots):
    """"" is not None: `_assignee_from_legacy` reads a blank assignee as the
    operator, so returning None here would read back as "operator" on the very
    next load. The call and the store must agree."""
    root = roots["workshop"]
    t = tasks.add(root, title="Mine", assignee="agent:alpha")
    got = tasks.update(root, t["id"], set_assignee="  ")
    assert got["assignee"] == tasks.DEFAULT_OWNER
    assert got["owner"] == tasks.DEFAULT_OWNER
    assert tasks.get_task(root, t["id"])["assignee"] == tasks.DEFAULT_OWNER


def test_owner_still_refuses_another_delegates_task(roots):
    """The half that must NOT change: the filter is what stops an agent mutating
    a task belonging to a different delegate."""
    root = roots["workshop"]
    t = tasks.add(root, title="Alpha's", assignee="agent:alpha")
    assert tasks.update(root, t["id"], status="done", owner="agent:beta") is None
    assert tasks.get_task(root, t["id"])["status"] == "todo"
    assert tasks.update(root, t["id"], status="done", owner="agent:alpha") is not None


def test_owner_is_a_filter_and_never_a_write(roots):
    """`owner=` scopes the lookup. It has never written the field and must not
    start, or an agent patching a status would silently rewrite the assignee.

    Shown through the LEGACY bare-slug form, which is the case where a write
    would be visible: `_match_assignee` accepts "alpha" for a ticket assigned
    "agent:alpha", so a filter value that also wrote would leave the record
    holding the denormalized spelling."""
    root = roots["workshop"]
    t = tasks.add(root, title="Alpha's", assignee="agent:alpha")
    got = tasks.update(root, t["id"], status="doing", owner="alpha")
    assert got is not None
    assert got["assignee"] == "agent:alpha"
    assert tasks.get_task(root, t["id"])["assignee"] == "agent:alpha"


def test_set_assignee_still_respects_an_explicit_owner_filter(roots):
    """Reassigning is a write; who is allowed to do it is still the filter's
    question, so the two compose rather than one disabling the other."""
    root = roots["workshop"]
    t = tasks.add(root, title="Alpha's", assignee="agent:alpha")
    assert tasks.update(root, t["id"], set_assignee="agent:gamma",
                        owner="agent:beta") is None
    assert tasks.update(root, t["id"], set_assignee="agent:gamma",
                        owner="agent:alpha") is not None
    assert tasks.get_task(root, t["id"])["assignee"] == "agent:gamma"


def test_answering_a_question_does_not_rewrite_the_assignee(roots):
    """`append_clarification`'s `assignee` argument is the same delegate FILTER
    `get_task` applies two lines above it. It was forwarded to `update` as
    `assignee=`, which also WRITES, so answering a question restamped the field
    with whatever form the caller happened to scope by."""
    root = roots["workshop"]
    t = tasks.add(root, title="Alpha's", assignee="agent:alpha")
    got = tasks.append_clarification(root, t["id"], question="which?",
                                     answer="the first", assignee="alpha")
    assert got is not None
    assert got["assignee"] == "agent:alpha"
    assert "the first" in tasks.get_task(root, t["id"])["body"]


# ---- Fix 3: the assignee, over HTTP -----------------------------------------

def test_patching_a_new_assignee_saves_instead_of_404ing(client, roots):
    t = tasks.add(roots["workshop"], title="Mine", assignee="agent:alpha")
    got = client.patch(f"/api/tasks/workshop/{t['id']}",
                       json={"assignee": "agent:beta"})
    assert got.status_code == 200, got.text
    assert got.json()["assignee"] == "agent:beta"


def test_a_reassign_no_longer_drops_the_rest_of_the_save(client, roots):
    """The damage was never limited to the assignee: the whole PATCH was one
    write, so a reassign took the title, body and status edits down with it."""
    t = tasks.add(roots["workshop"], title="Old title", status="draft")
    got = client.patch(f"/api/tasks/workshop/{t['id']}", json={
        "assignee": "agent:beta", "title": "New title",
        "body": "New body", "status": "todo"})
    assert got.status_code == 200, got.text
    rec = got.json()
    assert (rec["assignee"], rec["title"], rec["body"], rec["status"]) == \
        ("agent:beta", "New title", "New body", "todo")
    stored = tasks.get_task(roots["workshop"], t["id"])
    assert stored["assignee"] == "agent:beta"
    assert stored["title"] == "New title"


def test_an_assignee_can_be_cleared_over_http(client, roots):
    t = tasks.add(roots["workshop"], title="Mine", assignee="agent:alpha")
    rec = client.patch(f"/api/tasks/workshop/{t['id']}", json={"assignee": ""}).json()
    assert rec["assignee"] == tasks.DEFAULT_OWNER


def test_the_operator_route_assigns_a_previously_unassigned_ticket(client, roots):
    t = tasks.add(roots["workshop"], title="Nobody's")
    rec = client.patch(f"/api/tasks/workshop/{t['id']}",
                       json={"assignee": "agent:alpha"}).json()
    assert rec["assignee"] == "agent:alpha"
