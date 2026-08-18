"""Ordering, paging, discussion, and resolved links on the ticket surface.

Four things the ticket API could not do, each of which showed up as a defect in
the UI rather than as a missing feature:

  * The list returned raw store order (insertion order) with no sort and no
    paging, so `priority` was displayed on every card and sorted on by nothing,
    and a caller with more than a screenful got an arbitrary slice with no way
    to reach the rest.
  * There was no discussion channel at all. `evidence` is the system's own
    record of a run and carries no author, so a note from a person had nowhere
    to go that was not the spec itself.
  * `depends_on`, `relates_to` and `parent` hold BARE task ids with no scope.
    Every client built hrefs from the CURRENT ticket's scope, so a cross-scope
    dependency linked to a 404, and `parent` was rendered nowhere at all.

The router is exercised through a bare FastAPI app (auth attaches at
catalog-mount time in main.py), with two scopes pointed at tmp stores so the
cross-scope resolution has something real to cross.
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
    # `_resolve_links` closes over the module-level name, so patch it there too.
    monkeypatch.setattr(tasks_all, "scope_roots", lambda: list(roots.items()),
                        raising=False)
    app = FastAPI()
    app.include_router(tasks_all.router, prefix="/api")
    return TestClient(app)


# ---- ordering + paging ------------------------------------------------------

def test_sort_by_priority_puts_the_biggest_first(client, roots):
    tasks.add(roots["workshop"], title="low", priority=1)
    tasks.add(roots["workshop"], title="high", priority=9)
    tasks.add(roots["workshop"], title="mid", priority=5)
    r = client.get("/api/tasks?sort=priority")
    assert [t["title"] for t in r.json()["items"]] == ["high", "mid", "low"]


def test_sort_by_title_is_alphabetical_and_case_insensitive(client, roots):
    for name in ("banana", "Apple", "cherry"):
        tasks.add(roots["workshop"], title=name)
    r = client.get("/api/tasks?sort=title")
    assert [t["title"] for t in r.json()["items"]] == ["Apple", "banana", "cherry"]


def test_sort_by_status_leads_with_what_is_stuck(client, roots):
    tasks.add(roots["workshop"], title="finished", status="done")
    tasks.add(roots["workshop"], title="waiting", status="todo")
    tasks.add(roots["workshop"], title="stuck", status="blocked")
    r = client.get("/api/tasks?sort=status")
    assert [t["title"] for t in r.json()["items"]] == ["stuck", "waiting", "finished"]


def test_an_unknown_sort_key_falls_back_instead_of_erroring(client, roots):
    tasks.add(roots["workshop"], title="only")
    r = client.get("/api/tasks?sort=nonsense")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1


def test_limit_pages_and_total_reports_the_whole_filtered_set(client, roots):
    for i in range(5):
        tasks.add(roots["workshop"], title=f"t{i}", priority=i)
    r = client.get("/api/tasks?sort=priority&limit=2").json()
    assert [t["title"] for t in r["items"]] == ["t4", "t3"]
    assert len(r["items"]) == 2
    # `page.total` is the filtered set BEFORE paging, which is what a
    # "showing 2 of 5" line needs. `len(items)` is this page; ADR 0023 dropped
    # the old top-level `count` because a derivable field can disagree.
    assert r["page"]["total"] == 5
    assert r["page"]["limit"] == 2

    nxt = client.get("/api/tasks?sort=priority&limit=2&offset=2").json()
    assert [t["title"] for t in nxt["items"]] == ["t2", "t1"]
    assert nxt["page"]["total"] == 5
    assert nxt["page"]["offset"] == 2


def test_offset_past_the_end_is_an_empty_page_not_an_error(client, roots):
    tasks.add(roots["workshop"], title="only")
    r = client.get("/api/tasks?offset=50")
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["page"]["total"] == 1


def test_q_matches_title_or_body_case_insensitively(client, roots):
    tasks.add(roots["workshop"], title="Rebuild the alerter", body="it stopped")
    tasks.add(roots["workshop"], title="Unrelated", body="mentions the ALERTER here")
    tasks.add(roots["workshop"], title="Nothing to do with it", body="quiet")
    r = client.get("/api/tasks?q=alerter").json()
    assert {t["title"] for t in r["items"]} == {"Rebuild the alerter", "Unrelated"}
    assert r["page"]["total"] == 2


def test_q_composes_with_the_status_filter(client, roots):
    tasks.add(roots["workshop"], title="alerter one", status="done")
    tasks.add(roots["workshop"], title="alerter two", status="todo")
    r = client.get("/api/tasks?q=alerter&status=todo").json()
    assert [t["title"] for t in r["items"]] == ["alerter two"]


# ---- discussion -------------------------------------------------------------

def test_a_comment_round_trips_with_its_author(client, roots):
    t = tasks.add(roots["workshop"], title="Discuss me")
    r = client.post(f"/api/tasks/workshop/{t['id']}/comments",
                    json={"text": "  decided to wait for the migration  "})
    assert r.status_code == 200, r.text
    got = client.get(f"/api/tasks/workshop/{t['id']}").json()
    assert len(got["comments"]) == 1
    assert got["comments"][0]["text"] == "decided to wait for the migration"
    assert got["comments"][0]["author"] == "operator"


def test_an_agent_can_name_itself(client, roots):
    t = tasks.add(roots["workshop"], title="Discuss me")
    client.post(f"/api/tasks/workshop/{t['id']}/comments",
                json={"text": "found the cause", "author": "agent:maker"})
    got = client.get(f"/api/tasks/workshop/{t['id']}").json()
    assert got["comments"][0]["author"] == "agent:maker"


def test_an_empty_comment_is_refused(client, roots):
    t = tasks.add(roots["workshop"], title="Discuss me")
    r = client.post(f"/api/tasks/workshop/{t['id']}/comments", json={"text": "   "})
    assert r.status_code == 400


def test_commenting_on_a_missing_ticket_is_a_404(client):
    r = client.post("/api/tasks/workshop/nope/comments", json={"text": "hi"})
    assert r.status_code == 404


def test_a_comment_is_not_the_spec(client, roots):
    """A comment must never leak into the text a worker is handed. That is the
    whole reason `append_clarification` writes answers into the BODY instead."""
    t = tasks.add(roots["workshop"], title="Ship it", body="Objective: ship.")
    client.post(f"/api/tasks/workshop/{t['id']}/comments",
                json={"text": "do NOT ship on a Friday"})
    got = client.get(f"/api/tasks/workshop/{t['id']}").json()
    assert "Friday" not in got["body"]
    assert "Friday" not in got["text"]


def test_comments_delete_individually(client, roots):
    t = tasks.add(roots["workshop"], title="Discuss me")
    client.post(f"/api/tasks/workshop/{t['id']}/comments", json={"text": "first"})
    client.post(f"/api/tasks/workshop/{t['id']}/comments", json={"text": "second"})
    got = client.get(f"/api/tasks/workshop/{t['id']}").json()
    cid = got["comments"][0]["id"]
    r = client.delete(f"/api/tasks/workshop/{t['id']}/comments/{cid}")
    assert r.status_code == 200
    left = client.get(f"/api/tasks/workshop/{t['id']}").json()["comments"]
    assert [c["text"] for c in left] == ["second"]


def test_deleting_a_comment_that_is_not_there_is_a_404(client, roots):
    t = tasks.add(roots["workshop"], title="Discuss me")
    r = client.delete(f"/api/tasks/workshop/{t['id']}/comments/nope")
    assert r.status_code == 404


# ---- resolved links ---------------------------------------------------------

def test_children_resolve_and_carry_their_scope(client, roots):
    parent = tasks.add(roots["workshop"], title="The big one")
    kid = tasks.add(roots["workshop"], title="A piece of it", parent=parent["id"])
    got = client.get(f"/api/tasks/workshop/{parent['id']}").json()
    assert [c["id"] for c in got["links"]["children"]] == [kid["id"]]
    assert got["links"]["children"][0]["scope"] == "workshop"
    assert got["links"]["children"][0]["title"] == "A piece of it"


def test_a_child_links_back_to_its_parent(client, roots):
    parent = tasks.add(roots["workshop"], title="The big one")
    kid = tasks.add(roots["workshop"], title="A piece of it", parent=parent["id"])
    got = client.get(f"/api/tasks/workshop/{kid['id']}").json()
    assert got["links"]["parent"]["id"] == parent["id"]
    assert got["links"]["parent"]["title"] == "The big one"


def test_a_cross_scope_dependency_resolves_to_its_own_scope(client, roots):
    """The defect this exists for: the client built the href from the CURRENT
    ticket's scope, so this link went to /tickets/workshop/<id> and 404'd."""
    upstream = tasks.add(roots["project:other"], title="Land the migration")
    downstream = tasks.add(roots["workshop"], title="Use the new field",
                           depends_on=[upstream["id"]])
    got = client.get(f"/api/tasks/workshop/{downstream['id']}").json()
    dep = got["links"]["depends_on"][0]
    assert dep["id"] == upstream["id"]
    assert dep["scope"] == "project:other"
    assert dep["title"] == "Land the migration"


def test_an_unresolvable_edge_is_reported_not_linked(client, roots):
    t = tasks.add(roots["workshop"], title="Points at a ghost")
    tasks.update(roots["workshop"], t["id"], depends_on=["deadbeef"])
    got = client.get(f"/api/tasks/workshop/{t['id']}").json()
    assert got["links"]["depends_on"] == []
    assert got["links"]["missing"] == ["deadbeef"]


def test_a_ticket_with_no_edges_reports_empty_links(client, roots):
    t = tasks.add(roots["workshop"], title="Alone")
    got = client.get(f"/api/tasks/workshop/{t['id']}").json()
    assert got["links"] == {"parent": None, "children": [], "depends_on": [],
                            "relates_to": [], "missing": []}
