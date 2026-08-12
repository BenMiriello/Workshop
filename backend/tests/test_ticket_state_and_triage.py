"""Derived ticket state (`state_type`, `ready`, `blocked_by`, `sort=queue`) and
the two triage verbs that completed the set (mark-duplicate, decline-with-reason).

Per docs/platform-redesign/ticket-system-consolidation-2026-08-12.md §3.1-§3.3.
Four of these tests exist for a specific hazard rather than for a feature:

  * `state_type` is DERIVED. `tasks._for_storage` strips only `_PROJECTED_KEYS`,
    so a derived field added inside `_normalize` would be written straight into
    `.tasks.json` and become a second source of truth. One test reads the raw
    file after a write and insists the key is not there.
  * `ready` must be PURE. `next_ready` calls `reclaim()`, which rewrites
    `doing` -> `parked` at the attempt cap and persists. The ticket list polls
    every few seconds, so a GET that reclaimed would dead-letter live work
    because a tab was open. One test builds a claim that reclaim WOULD park,
    reads it over HTTP repeatedly, and insists it is still `doing` — then calls
    `next_ready` to prove the fixture was genuinely reclaimable all along.
  * `blocked_by` resolves across scopes while `ready` does not. A row that reads
    `ready: true` next to a cross-scope blocker is the documented scheduler gap,
    not a bug, and is pinned here so nobody "fixes" it into a behaviour change
    for the sweep.
  * `sort=queue` must equal what the scheduler would pick, or the top of the
    list is a lie.

Router is exercised through a bare FastAPI app (auth attaches at catalog-mount
time in main.py), with two tmp scopes so cross-scope resolution has something
real to cross.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
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


def _row(payload: dict, title: str) -> dict:
    return next(t for t in payload["tasks"] if t["title"] == title)


# ---- state_type -------------------------------------------------------------

def test_every_status_maps_to_one_of_five_types():
    got = {s: tasks.state_type(s) for s in tasks.STATUSES}
    assert got == {
        "draft": "backlog",
        "parked": "backlog",
        "todo": "unstarted",
        "doing": "started",
        "in_review": "started",
        "blocked": "started",
        "done": "completed",
        "cancelled": "canceled",
    }
    assert set(got.values()) <= set(tasks.STATE_TYPES)
    assert len(tasks.STATE_TYPES) == 5


def test_the_type_is_spelled_with_one_l_and_the_status_with_two():
    """Matching every tracker's API on the type, while the persisted status value
    stays as it is in live records."""
    assert "cancelled" in tasks.STATUSES
    assert tasks.state_type("cancelled") == "canceled"


def test_an_unknown_status_still_gets_a_bucket():
    assert tasks.state_type("nonsense") == "backlog"
    assert tasks.state_type(None) == "backlog"


def test_state_type_is_on_every_list_row(client, roots):
    tasks.add(roots["workshop"], title="waiting")
    tasks.add(roots["workshop"], title="stuck", status="blocked")
    r = client.get("/api/tasks").json()
    assert {t["title"]: t["state_type"] for t in r["tasks"]} == {
        "waiting": "unstarted", "stuck": "started"}


def test_state_type_is_on_the_detail_record(client, roots):
    t = tasks.add(roots["workshop"], title="park me", status="parked")
    got = client.get(f"/api/tasks/workshop/{t['id']}").json()
    assert got["state_type"] == "backlog"


def test_state_type_is_absent_from_the_store_on_disk(client, roots):
    """The hazard the whole route-layer placement exists for: `_for_storage`
    strips only `exec` and `blocked_request`, so a derived key on the record
    would be persisted and could then be hand-edited out of step with `status`."""
    t = tasks.add(roots["workshop"], title="Write me out")
    # A read that projects, then a write, so a projected key could leak into the
    # record the write persists if anything stamped it onto the stored dict.
    client.get("/api/tasks").json()
    client.get(f"/api/tasks/workshop/{t['id']}").json()
    client.patch(f"/api/tasks/workshop/{t['id']}", json={"priority": 3})

    raw = json.loads((roots["workshop"] / tasks.STORE_NAME).read_text())
    stored = next(x for x in raw["tasks"] if x["id"] == t["id"])
    assert "state_type" not in stored
    assert "ready" not in stored
    assert "blocked_by" not in stored


# ---- ready ------------------------------------------------------------------

def test_is_ready_is_a_pure_predicate_over_what_it_is_handed():
    todo = {"id": "a", "status": "todo", "depends_on": []}
    assert tasks.is_ready(todo, {"a": todo}) is True
    assert tasks.is_ready({"id": "b", "status": "doing"}, {}) is False
    open_dep = {"id": "c", "status": "todo"}
    blocked = {"id": "d", "status": "todo", "depends_on": ["c"]}
    assert tasks.is_ready(blocked, {"c": open_dep, "d": blocked}) is False
    assert tasks.is_ready(blocked, {"c": {"id": "c", "status": "done"}}) is True


def test_a_dependency_the_index_cannot_see_reads_as_satisfied():
    """The scheduler's quirk, kept deliberately: `next_ready` builds `by_id` from
    ONE root, so an id it cannot resolve does not gate. Honouring it would change
    which tickets the sweep picks up."""
    t = {"id": "a", "status": "todo", "depends_on": ["elsewhere"]}
    assert tasks.is_ready(t, {"a": t}) is True


def test_ready_is_true_only_for_todo_with_satisfied_deps(client, roots):
    tasks.add(roots["workshop"], title="free")
    tasks.add(roots["workshop"], title="running", status="doing")
    dep = tasks.add(roots["workshop"], title="upstream")
    tasks.add(roots["workshop"], title="waiting on it", depends_on=[dep["id"]])
    r = client.get("/api/tasks").json()
    assert _row(r, "free")["ready"] is True
    assert _row(r, "running")["ready"] is False
    assert _row(r, "upstream")["ready"] is True
    assert _row(r, "waiting on it")["ready"] is False


def test_closing_the_dependency_makes_the_dependent_ready(client, roots):
    dep = tasks.add(roots["workshop"], title="upstream")
    tasks.add(roots["workshop"], title="downstream", depends_on=[dep["id"]])
    tasks.update(roots["workshop"], dep["id"], status="done")
    r = client.get("/api/tasks").json()
    assert _row(r, "downstream")["ready"] is True


def test_the_ready_filter_returns_only_what_the_scheduler_could_pick(client, roots):
    tasks.add(roots["workshop"], title="free")
    tasks.add(roots["workshop"], title="drafted", status="draft")
    dep = tasks.add(roots["workshop"], title="upstream")
    tasks.add(roots["workshop"], title="waiting on it", depends_on=[dep["id"]])
    r = client.get("/api/tasks?ready=true").json()
    assert sorted(t["title"] for t in r["tasks"]) == ["free", "upstream"]
    assert r["total"] == 2
    # And its complement, so the filter is a real predicate rather than a shortcut.
    not_ready = client.get("/api/tasks?ready=false").json()
    assert sorted(t["title"] for t in not_ready["tasks"]) == ["drafted", "waiting on it"]


def test_ready_is_on_the_detail_record_too(client, roots):
    dep = tasks.add(roots["workshop"], title="upstream")
    down = tasks.add(roots["workshop"], title="downstream", depends_on=[dep["id"]])
    assert client.get(f"/api/tasks/workshop/{down['id']}").json()["ready"] is False
    assert client.get(f"/api/tasks/workshop/{dep['id']}").json()["ready"] is True


# ---- ready must never reclaim ----------------------------------------------

def _stale_claim_at_cap(root: Path, title: str = "Crashed run") -> str:
    """A `doing` task whose worker died with the attempt cap exhausted: exactly
    what `reclaim` dead-letters into `parked`. Aged by hand because nothing in
    the public API can make a heartbeat old, and an in-window claim would make
    the test pass for the wrong reason."""
    t = tasks.add(root, title=title, max_attempts=1)
    assert tasks.claim(root, t["id"], "job-1", pid=99999) is not None
    p = root / tasks.STORE_NAME
    data = json.loads(p.read_text())
    old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    for rec in data["tasks"]:
        if rec["id"] == t["id"]:
            rec["last_heartbeat"] = old
            rec["lease_until"] = old
    # Only heartbeat/lease change, so the rendered TODO.md is untouched and
    # reconcile still short-circuits on the stored export_sha.
    p.write_text(json.dumps(data, indent=2) + "\n")
    return t["id"]


def test_a_list_poll_does_not_dead_letter_a_live_claim(client, roots):
    tid = _stale_claim_at_cap(roots["workshop"])
    for _ in range(3):  # the panel polls; three ticks must be as harmless as one
        r = client.get("/api/tasks").json()
        assert _row(r, "Crashed run")["status"] == "doing"
    assert tasks.get_task(roots["workshop"], tid)["status"] == "doing"


def test_the_ready_filter_does_not_dead_letter_a_live_claim(client, roots):
    tid = _stale_claim_at_cap(roots["workshop"])
    client.get("/api/tasks?ready=true")
    client.get("/api/tasks?ready=false")
    client.get("/api/tasks?sort=queue")
    assert tasks.get_task(roots["workshop"], tid)["status"] == "doing"


def test_the_detail_get_does_not_dead_letter_a_live_claim(client, roots):
    tid = _stale_claim_at_cap(roots["workshop"])
    got = client.get(f"/api/tasks/workshop/{tid}").json()
    assert got["status"] == "doing"
    assert got["ready"] is False
    assert got["state_type"] == "started"
    assert tasks.get_task(roots["workshop"], tid)["status"] == "doing"


def test_the_scheduler_still_reclaims_the_very_same_task(client, roots):
    """Proves the fixture above was genuinely reclaimable: the reads left it
    alone, and the one caller that is supposed to park it still does."""
    tid = _stale_claim_at_cap(roots["workshop"])
    client.get("/api/tasks")
    tasks.next_ready(roots["workshop"])
    assert tasks.get_task(roots["workshop"], tid)["status"] == "parked"


# ---- blocked_by -------------------------------------------------------------

def test_blocked_by_names_the_open_dependency(client, roots):
    dep = tasks.add(roots["workshop"], title="upstream")
    tasks.add(roots["workshop"], title="downstream", depends_on=[dep["id"]])
    row = _row(client.get("/api/tasks").json(), "downstream")
    assert row["blocked_by"] == [{"id": dep["id"], "scope": "workshop",
                                  "title": "upstream", "status": "todo"}]


def test_blocked_by_drops_a_dependency_that_is_finished(client, roots):
    done = tasks.add(roots["workshop"], title="landed", status="done")
    cancelled = tasks.add(roots["workshop"], title="dropped", status="cancelled")
    tasks.add(roots["workshop"], title="downstream",
              depends_on=[done["id"], cancelled["id"]])
    row = _row(client.get("/api/tasks").json(), "downstream")
    assert row["blocked_by"] == []
    assert row["ready"] is True


def test_a_cross_scope_blocker_carries_its_own_scope(client, roots):
    upstream = tasks.add(roots["project:other"], title="Land the migration")
    tasks.add(roots["workshop"], title="Use the new field",
              depends_on=[upstream["id"]])
    row = _row(client.get("/api/tasks").json(), "Use the new field")
    assert row["blocked_by"] == [{"id": upstream["id"], "scope": "project:other",
                                  "title": "Land the migration", "status": "todo"}]
    # The documented gap: the scheduler cannot see across scopes, so this row is
    # `ready` while visibly blocked. Pinned so the two are never quietly aligned
    # in a way that changes which tickets the sweep picks up.
    assert row["ready"] is True


def test_a_cross_scope_blocker_resolves_under_a_scope_filter(client, roots):
    """A `scope=` query loads one store; the blocker lives in another and must
    still resolve, which is the branch that pays for the extra scan."""
    upstream = tasks.add(roots["project:other"], title="Land the migration")
    tasks.add(roots["workshop"], title="Use the new field",
              depends_on=[upstream["id"]])
    r = client.get("/api/tasks?scope=workshop").json()
    assert r["scopes"] == 1
    assert _row(r, "Use the new field")["blocked_by"][0]["scope"] == "project:other"


def test_blocked_by_is_on_the_detail_record(client, roots):
    upstream = tasks.add(roots["project:other"], title="Land the migration")
    down = tasks.add(roots["workshop"], title="Use it", depends_on=[upstream["id"]])
    got = client.get(f"/api/tasks/workshop/{down['id']}").json()
    assert [b["id"] for b in got["blocked_by"]] == [upstream["id"]]
    assert got["blocked_by"][0]["scope"] == "project:other"


def test_an_unresolvable_dependency_is_not_rendered_as_a_blocker(client, roots):
    t = tasks.add(roots["workshop"], title="Points at a ghost")
    tasks.update(roots["workshop"], t["id"], depends_on=["deadbeef"])
    row = _row(client.get("/api/tasks").json(), "Points at a ghost")
    assert row["blocked_by"] == []


def test_a_done_dependency_is_visible_even_when_done_is_filtered_out(client, roots):
    """include_done=false must not make a satisfied dependency look unsatisfied:
    the index is built from the full store and the filter applied per row."""
    dep = tasks.add(roots["workshop"], title="landed", status="done")
    tasks.add(roots["workshop"], title="downstream", depends_on=[dep["id"]])
    r = client.get("/api/tasks?include_done=false").json()
    assert [t["title"] for t in r["tasks"]] == ["downstream"]
    assert _row(r, "downstream")["ready"] is True
    assert _row(r, "downstream")["blocked_by"] == []


# ---- queue sort -------------------------------------------------------------

def test_queue_sorts_by_priority_then_oldest_first(client, roots):
    a = tasks.add(roots["workshop"], title="old normal", priority=1)
    b = tasks.add(roots["workshop"], title="new normal", priority=1)
    c = tasks.add(roots["workshop"], title="urgent", priority=9)
    # `created` comes from the clock and two adds can land in the same second;
    # stamp the order the test is about instead of racing it.
    for tid, created in ((a["id"], "2026-01-01T00:00:00+00:00"),
                         (b["id"], "2026-06-01T00:00:00+00:00"),
                         (c["id"], "2026-08-01T00:00:00+00:00")):
        _set_created(roots["workshop"], tid, created)
    r = client.get("/api/tasks?sort=queue").json()
    assert [t["title"] for t in r["tasks"]] == ["urgent", "old normal", "new normal"]


def _set_created(root: Path, task_id: str, created: str) -> None:
    p = root / tasks.STORE_NAME
    data = json.loads(p.read_text())
    for rec in data["tasks"]:
        if rec["id"] == task_id:
            rec["created"] = created
    p.write_text(json.dumps(data, indent=2) + "\n")


def test_queue_puts_the_task_the_scheduler_would_claim_first(client, roots):
    for i, name in enumerate(("first filed", "second filed", "third filed")):
        t = tasks.add(roots["workshop"], title=name, priority=5)
        _set_created(roots["workshop"], t["id"], f"2026-0{i + 1}-01T00:00:00+00:00")
    tasks.add(roots["workshop"], title="top priority", priority=8)
    r = client.get("/api/tasks?sort=queue&ready=true").json()
    assert r["tasks"][0]["title"] == tasks.next_ready(roots["workshop"])["title"]
    assert r["tasks"][0]["title"] == "top priority"


def test_queue_is_a_known_sort_key(client, roots):
    assert "queue" in tasks.SORT_KEYS
    tasks.add(roots["workshop"], title="only")
    assert client.get("/api/tasks?sort=queue").json()["sort"] == "queue"


def test_queue_reversed_is_the_exact_inverse(client, roots):
    tasks.add(roots["workshop"], title="low", priority=1)
    tasks.add(roots["workshop"], title="high", priority=9)
    fwd = [t["title"] for t in client.get("/api/tasks?sort=queue").json()["tasks"]]
    back = [t["title"] for t in
            client.get("/api/tasks?sort=queue&desc=true").json()["tasks"]]
    assert fwd == ["high", "low"]
    assert back == list(reversed(fwd))


# ---- triage: mark duplicate -------------------------------------------------

def test_marking_a_duplicate_links_cancels_and_records_why(client, roots):
    canonical = tasks.add(roots["workshop"], title="Fix the alerter")
    dup = tasks.add(roots["workshop"], title="Alerter is broken")
    r = client.post(f"/api/tasks/workshop/{dup['id']}/duplicate-of",
                    json={"target_id": canonical["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["canonical"]["id"] == canonical["id"]
    assert body["task"]["status"] == "cancelled"
    assert client.get(f"/api/tasks/workshop/{dup['id']}").json()["state_type"] \
        == "canceled"

    merged = tasks.get_task(roots["workshop"], dup["id"])
    assert merged["relates_to"] == [canonical["id"]]
    assert [e["kind"] for e in merged["evidence"]] == ["duplicate-of"]
    assert canonical["id"] in merged["evidence"][0]["ref"]


def test_the_canonical_ticket_links_back_in_the_same_scope(client, roots):
    canonical = tasks.add(roots["workshop"], title="Fix the alerter")
    dup = tasks.add(roots["workshop"], title="Alerter is broken")
    client.post(f"/api/tasks/workshop/{dup['id']}/duplicate-of",
                json={"target_id": canonical["id"]})
    kept = tasks.get_task(roots["workshop"], canonical["id"])
    assert kept["relates_to"] == [dup["id"]]
    assert [e["kind"] for e in kept["evidence"]] == ["duplicate-from"]
    assert kept["status"] == "todo"  # the survivor is untouched otherwise


def test_a_duplicate_can_point_at_another_scope(client, roots):
    canonical = tasks.add(roots["project:other"], title="Fix the alerter")
    dup = tasks.add(roots["workshop"], title="Alerter is broken")
    r = client.post(f"/api/tasks/workshop/{dup['id']}/duplicate-of",
                    json={"target_id": canonical["id"],
                          "target_scope": "project:other"})
    assert r.status_code == 200, r.text
    assert r.json()["canonical"]["scope"] == "project:other"
    merged = tasks.get_task(roots["workshop"], dup["id"])
    assert merged["status"] == "cancelled"
    assert merged["relates_to"] == [canonical["id"]]
    # No write into the other store: one act, one tree.
    kept = tasks.get_task(roots["project:other"], canonical["id"])
    assert kept["relates_to"] == []
    assert kept["evidence"] == []
    # The forward link still resolves for display.
    got = client.get(f"/api/tasks/workshop/{dup['id']}").json()
    assert got["links"]["relates_to"][0]["scope"] == "project:other"


def test_a_ticket_cannot_be_its_own_duplicate(client, roots):
    t = tasks.add(roots["workshop"], title="Only one of me")
    r = client.post(f"/api/tasks/workshop/{t['id']}/duplicate-of",
                    json={"target_id": t["id"]})
    assert r.status_code == 400
    assert tasks.get_task(roots["workshop"], t["id"])["status"] == "todo"


def test_the_same_id_in_another_scope_is_not_self(client, roots):
    """Same bare id, different store: a real pair, not the self-reference guard."""
    dup = tasks.add(roots["workshop"], title="Alerter is broken")
    other = roots["project:other"]
    canonical = tasks.add(other, title="Fix the alerter")
    p = other / tasks.STORE_NAME
    data = json.loads(p.read_text())
    data["tasks"][0]["id"] = dup["id"]
    p.write_text(json.dumps(data, indent=2) + "\n")
    assert canonical["id"] != dup["id"]  # the fixture rewrote it on purpose
    r = client.post(f"/api/tasks/workshop/{dup['id']}/duplicate-of",
                    json={"target_id": dup["id"], "target_scope": "project:other"})
    assert r.status_code == 200, r.text


def test_a_missing_canonical_ticket_is_a_404(client, roots):
    dup = tasks.add(roots["workshop"], title="Alerter is broken")
    r = client.post(f"/api/tasks/workshop/{dup['id']}/duplicate-of",
                    json={"target_id": "deadbeef"})
    assert r.status_code == 404
    assert tasks.get_task(roots["workshop"], dup["id"])["status"] == "todo"


def test_a_missing_duplicate_is_a_404(client, roots):
    canonical = tasks.add(roots["workshop"], title="Fix the alerter")
    r = client.post("/api/tasks/workshop/deadbeef/duplicate-of",
                    json={"target_id": canonical["id"]})
    assert r.status_code == 404


def test_an_unknown_target_scope_is_refused(client, roots):
    dup = tasks.add(roots["workshop"], title="Alerter is broken")
    r = client.post(f"/api/tasks/workshop/{dup['id']}/duplicate-of",
                    json={"target_id": "whatever", "target_scope": "project:ghost"})
    assert r.status_code == 400


def test_marking_a_duplicate_twice_does_not_duplicate_the_link(client, roots):
    canonical = tasks.add(roots["workshop"], title="Fix the alerter")
    dup = tasks.add(roots["workshop"], title="Alerter is broken")
    for _ in range(2):
        client.post(f"/api/tasks/workshop/{dup['id']}/duplicate-of",
                    json={"target_id": canonical["id"]})
    assert tasks.get_task(roots["workshop"], dup["id"])["relates_to"] == [canonical["id"]]
    assert tasks.get_task(roots["workshop"], canonical["id"])["relates_to"] == [dup["id"]]


# ---- triage: decline with a reason ------------------------------------------

def test_a_cancel_reason_lands_in_evidence_and_on_the_ticket(client, roots):
    t = tasks.add(roots["workshop"], title="Rewrite the exporter")
    r = client.patch(f"/api/tasks/workshop/{t['id']}",
                     json={"status": "cancelled",
                           "reason": "the exporter is being deleted next week"})
    assert r.status_code == 200, r.text
    rec = tasks.get_task(roots["workshop"], t["id"])
    assert rec["status"] == "cancelled"
    assert rec["evidence"][-1]["kind"] == "decision"
    assert "being deleted next week" in rec["evidence"][-1]["ref"]
    assert "being deleted next week" in rec["blocked_reason"]


def test_a_reason_is_never_required(client, roots):
    t = tasks.add(roots["workshop"], title="Rewrite the exporter")
    r = client.patch(f"/api/tasks/workshop/{t['id']}", json={"status": "cancelled"})
    assert r.status_code == 200
    rec = tasks.get_task(roots["workshop"], t["id"])
    assert rec["status"] == "cancelled"
    assert rec["evidence"] == []


def test_a_reason_with_no_transition_is_refused_not_dropped(client, roots):
    t = tasks.add(roots["workshop"], title="Rewrite the exporter")
    r = client.patch(f"/api/tasks/workshop/{t['id']}", json={"reason": "because"})
    assert r.status_code == 400
    assert tasks.get_task(roots["workshop"], t["id"])["evidence"] == []


def test_a_reason_rides_any_stopping_transition(client, roots):
    t = tasks.add(roots["workshop"], title="Rewrite the exporter")
    client.patch(f"/api/tasks/workshop/{t['id']}",
                 json={"status": "parked", "reason": "waiting on the vendor"})
    rec = tasks.get_task(roots["workshop"], t["id"])
    assert rec["blocked_reason"] == "waiting on the vendor"
    assert rec["evidence"][-1]["ref"] == "parked: waiting on the vendor"


def test_a_reason_on_a_moving_transition_is_recorded_but_not_a_blocker(client, roots):
    t = tasks.add(roots["workshop"], title="Rewrite the exporter", status="draft")
    client.patch(f"/api/tasks/workshop/{t['id']}",
                 json={"status": "todo", "reason": "scoped it properly"})
    rec = tasks.get_task(roots["workshop"], t["id"])
    assert rec["evidence"][-1]["ref"] == "todo: scoped it properly"
    assert rec["blocked_reason"] is None
