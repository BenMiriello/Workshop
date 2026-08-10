"""Publish, push, pull — and the shadow that makes the merge honest.

Without a record of what both sides last agreed on, every difference is a guess
about who changed what and the only implementable rule is last-writer-wins, which
silently destroys work. With the shadow, "changed here", "changed there" and
"changed on both" are three distinguishable cases, and only the third needs the
operator.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import (tasks, tracker_bindings, tracker_sync, trackers)  # noqa: E402
from services.trackers import RemoteIssue  # noqa: E402


class FakeTracker:
    """A remote that records what it was told and can be edited underneath us."""

    provider = "github"

    def __init__(self):
        self.issues: dict[str, RemoteIssue] = {}
        self.created: list[dict] = []
        self.transitions: list[tuple[str, str]] = []
        self.comments: list[tuple[str, str]] = []
        self._n = 0

    def _mk(self, title, body, labels, state="open", reason=None):
        self._n += 1
        n = str(self._n)
        issue = RemoteIssue(remote_id=n, key=f"#{n}",
                            url=f"https://github.com/x/y/issues/{n}",
                            title=title, body=body, state=state,
                            closed_reason=reason, labels=list(labels),
                            updated="2026-08-10T00:00:00Z")
        self.issues[n] = issue
        return issue

    def create(self, *, title, body, labels, idem):
        self.created.append({"title": title, "body": body, "labels": list(labels),
                             "idem": idem})
        return self._mk(title, body, labels)

    def get(self, remote_id):
        return self.issues[remote_id]

    def update(self, remote_id, patch):
        cur = self.issues[remote_id]
        self.issues[remote_id] = RemoteIssue(
            remote_id=cur.remote_id, key=cur.key, url=cur.url,
            title=patch.get("title", cur.title), body=patch.get("body", cur.body),
            state=cur.state, closed_reason=cur.closed_reason,
            labels=patch.get("labels", cur.labels), updated="2026-08-10T01:00:00Z")
        return self.issues[remote_id]

    def transition(self, remote_id, status):
        state, reason = trackers.remote_state(status)
        self.transitions.append((remote_id, status))
        cur = self.issues[remote_id]
        self.issues[remote_id] = RemoteIssue(
            remote_id=cur.remote_id, key=cur.key, url=cur.url, title=cur.title,
            body=cur.body, state=state, closed_reason=reason,
            labels=cur.labels, updated="2026-08-10T02:00:00Z")
        return self.issues[remote_id]

    def comment(self, remote_id, markdown):
        self.comments.append((remote_id, markdown))

    def labels(self):
        return []

    def whoami(self):
        return trackers.Account(login="BenMiriello")

    # Test helper: somebody edits the issue in the tracker.
    def edit_remote(self, remote_id, **kw):
        cur = self.issues[remote_id]
        self.issues[remote_id] = RemoteIssue(
            remote_id=cur.remote_id, key=cur.key, url=cur.url,
            title=kw.get("title", cur.title), body=kw.get("body", cur.body),
            state=kw.get("state", cur.state),
            closed_reason=kw.get("closed_reason", cur.closed_reason),
            labels=kw.get("labels", cur.labels), updated="2026-08-10T03:00:00Z")
        return self.issues[remote_id]


@pytest.fixture
def synced(tmp_path, monkeypatch):
    """A scope in `sync` mode with a fake remote, and a tmp ticket store."""
    from services import ownership
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    ownership.set_current_owner(6)
    tracker_bindings.connect(provider="github", target="BenMiriello/a", mode="sync")
    fake = FakeTracker()
    monkeypatch.setattr(tracker_sync.trackers, "for_binding", lambda b: fake)
    yield tmp_path / "store", fake
    ownership.clear_current_owner()


# ---- publish ----------------------------------------------------------------


def test_an_approved_ticket_is_published(synced):
    root, fake = synced
    t = tasks.add(root, title="Bind a scope", body="Objective: do it.")
    out = tracker_sync.publish("workshop", root, t["id"])
    assert out["sync"]["remote_key"] == "#1"
    assert fake.created[0]["title"] == "Bind a scope"
    # The idempotency key is the task id, so a crashed create cannot double-file.
    assert fake.created[0]["idem"] == t["id"]


def test_a_draft_is_never_published(synced):
    """Approval is the publish gate. A draft is not work yet."""
    root, fake = synced
    t = tasks.add(root, title="A proposal", status="draft")
    assert tracker_sync.publish("workshop", root, t["id"]) is None
    assert fake.created == []


def test_publishing_twice_does_nothing(synced):
    root, fake = synced
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    assert tracker_sync.publish("workshop", root, t["id"]) is None
    assert len(fake.created) == 1


def test_import_mode_never_publishes(synced):
    """`import` means the tracker belongs to someone else."""
    root, fake = synced
    tracker_bindings.set_mode("import")
    t = tasks.add(root, title="Bind a scope")
    assert tracker_sync.publish("workshop", root, t["id"]) is None
    assert fake.created == []


def test_the_selection_label_goes_on_what_we_create(synced):
    """Otherwise our own issue fails our own filter on the next poll and looks
    like a stranger's."""
    root, fake = synced
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    assert "workshop" in fake.created[0]["labels"]


def test_publishing_records_the_shadow(synced):
    root, _ = synced
    t = tasks.add(root, title="Bind a scope", body="Objective: do it.")
    out = tracker_sync.publish("workshop", root, t["id"])
    assert out["sync"]["shadow"]["title"] == "Bind a scope"
    assert out["sync"]["state"] == "linked"


def test_a_publish_failure_is_recorded_and_does_not_raise(synced, monkeypatch):
    root, fake = synced

    def boom(**kw):
        raise RuntimeError("github said no")

    monkeypatch.setattr(fake, "create", boom)
    t = tasks.add(root, title="Bind a scope")
    assert tracker_sync.publish("workshop", root, t["id"]) is None
    kinds = [e["kind"] for e in tasks.get_task(root, t["id"])["evidence"]]
    assert "sync-error" in kinds


def test_publish_is_a_no_op_with_no_connection(tmp_path, monkeypatch):
    """The bug the first version had: approving a ticket on a box with no tracker
    configured raised out of the approval."""
    from services import ownership
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    root = tmp_path / "store"
    t = tasks.add(root, title="Bind a scope")
    assert tracker_sync.publish("workshop", root, t["id"]) is None


# ---- push -------------------------------------------------------------------


def test_finishing_closes_the_remote(synced):
    root, fake = synced
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    tasks.update(root, t["id"], status="done")
    tracker_sync.push_status("workshop", root, t["id"])
    assert fake.issues["1"].state == "closed"
    assert fake.issues["1"].closed_reason == "completed"


def test_cancelling_closes_it_as_not_planned(synced):
    root, fake = synced
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    tasks.update(root, t["id"], status="cancelled")
    tracker_sync.push_status("workshop", root, t["id"])
    assert fake.issues["1"].closed_reason == "not_planned"


def test_a_status_that_does_not_change_the_remote_state_is_not_pushed(synced):
    """todo -> doing is both "open". A no-op write is a wasted request."""
    root, fake = synced
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    tasks.update(root, t["id"], status="doing")
    assert tracker_sync.push_status("workshop", root, t["id"]) is None
    assert fake.transitions == []


def test_a_local_edit_writes_through_and_refreshes_the_shadow(synced):
    """Write-through is what stops the next poll reading our own edit as a remote
    change."""
    root, fake = synced
    t = tasks.add(root, title="Bind a scope", body="Objective: do it.")
    tracker_sync.publish("workshop", root, t["id"])
    tasks.update(root, t["id"], title="Bind a scope to a tracker")
    out = tracker_sync.push_text("workshop", root, t["id"])
    assert fake.issues["1"].title == "Bind a scope to a tracker"
    assert out["sync"]["shadow"]["title"] == "Bind a scope to a tracker"


# ---- pull -------------------------------------------------------------------


def test_a_remote_edit_comes_back(synced):
    root, fake = synced
    t = tasks.add(root, title="Bind a scope", body="Objective: do it.")
    tracker_sync.publish("workshop", root, t["id"])
    fake.edit_remote("1", title="Bind a scope, renamed upstream")

    out = tracker_sync.pull("workshop", root)
    assert out["pulled"] == 1 and out["conflicts"] == 0
    assert tasks.get_task(root, t["id"])["title"] == "Bind a scope, renamed upstream"


def test_nothing_changed_means_nothing_pulled(synced):
    root, _ = synced
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    assert tracker_sync.pull("workshop", root)["pulled"] == 0


def test_closing_the_remote_finishes_the_ticket(synced):
    """The one thing a tracker can tell us that our own state machine did not
    know: somebody closed it over there."""
    root, fake = synced
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    fake.edit_remote("1", state="closed", closed_reason="completed")
    tracker_sync.pull("workshop", root)
    assert tasks.get_task(root, t["id"])["status"] == "done"


def test_closing_as_not_planned_cancels_rather_than_completes(synced):
    root, fake = synced
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    fake.edit_remote("1", state="closed", closed_reason="not_planned")
    tracker_sync.pull("workshop", root)
    assert tasks.get_task(root, t["id"])["status"] == "cancelled"


def test_both_sides_changing_is_a_conflict_and_nothing_is_lost(synced):
    root, fake = synced
    t = tasks.add(root, title="Bind a scope", body="Objective: do it.")
    tracker_sync.publish("workshop", root, t["id"])

    tasks.update(root, t["id"], title="My title")
    fake.edit_remote("1", title="Their title")

    out = tracker_sync.pull("workshop", root)
    assert out["conflicts"] == 1
    got = tasks.get_task(root, t["id"])
    # The tracker owns the words, so it wins the write.
    assert got["title"] == "Their title"
    assert got["sync"]["state"] == "conflict"
    # And the operator's version is kept, not thrown away.
    assert got["sync"]["conflict"]["mine"]["title"] == "My title"
    assert got["sync"]["conflict"]["theirs"]["title"] == "Their title"


def test_a_local_only_change_is_not_a_conflict(synced):
    """Changed here, unchanged there: keep local and let a push carry it."""
    root, fake = synced
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    tasks.update(root, t["id"], title="My title")

    out = tracker_sync.pull("workshop", root)
    assert out["conflicts"] == 0
    assert tasks.get_task(root, t["id"])["title"] == "My title"


def test_keeping_mine_reasserts_and_pushes_it(synced):
    root, fake = synced
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    tasks.update(root, t["id"], title="My title")
    fake.edit_remote("1", title="Their title")
    tracker_sync.pull("workshop", root)

    tracker_sync.resolve_conflict("workshop", root, t["id"], keep="mine")
    got = tasks.get_task(root, t["id"])
    assert got["title"] == "My title"
    assert fake.issues["1"].title == "My title"   # the only way local wins
    assert got["sync"]["conflict"] is None
    assert got["sync"]["state"] == "linked"


def test_keeping_theirs_just_clears_the_flag(synced):
    root, fake = synced
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    tasks.update(root, t["id"], title="My title")
    fake.edit_remote("1", title="Their title")
    tracker_sync.pull("workshop", root)

    tracker_sync.resolve_conflict("workshop", root, t["id"], keep="theirs")
    got = tasks.get_task(root, t["id"])
    assert got["title"] == "Their title"
    assert got["sync"]["conflict"] is None


def test_an_unreachable_remote_marks_the_link_stale_not_broken(synced, monkeypatch):
    root, fake = synced
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])

    def boom(_id):
        raise RuntimeError("network")

    monkeypatch.setattr(fake, "get", boom)
    tracker_sync.pull("workshop", root)
    assert tasks.get_task(root, t["id"])["sync"]["state"] == "stale"


def test_an_unlinked_ticket_is_not_pulled(synced):
    root, _ = synced
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    tasks.update(root, t["id"], unlink=True)
    assert tracker_sync.pull("workshop", root)["pulled"] == 0


def test_a_comment_is_the_only_write_import_mode_makes(synced):
    root, fake = synced
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    tracker_bindings.set_mode("import")
    assert tracker_sync.comment("workshop", root, t["id"], "run finished") is True
    assert fake.comments[0][1] == "run finished"


# ---- the call sites ----------------------------------------------------------
#
# The engine above was fully tested and completely unwired: `push_status`,
# `push_text` and `comment` had no caller but these tests, so a binding in `sync`
# mode was publish-and-pull only. Everything below pins the WIRING — the edit
# route and the job's terminal point — because that is the part that was missing,
# and a passing engine test says nothing about whether anything calls it.


@pytest.fixture
def edit_api(synced, monkeypatch):
    """The task PATCH route pointed at the synced scope's store."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import tasks_all

    root, fake = synced
    monkeypatch.setattr(tasks_all, "_root_for_scope",
                        lambda scope: root if scope == "workshop" else None)
    app = FastAPI()
    app.include_router(tasks_all.router, prefix="/api")
    return TestClient(app), root, fake


def test_editing_a_linked_ticket_writes_through_to_the_remote(edit_api):
    """§4: the edit reaches the tracker, and the shadow is refreshed from the
    response so the next poll does not read our own words as a remote change."""
    client, root, fake = edit_api
    t = tasks.add(root, title="Bind a scope", body="Objective: do it.")
    tracker_sync.publish("workshop", root, t["id"])

    r = client.patch(f"/api/tasks/workshop/{t['id']}",
                     json={"title": "Bind a scope to a tracker"})
    assert r.status_code == 200
    assert fake.issues["1"].title == "Bind a scope to a tracker"
    got = tasks.get_task(root, t["id"])
    assert got["title"] == "Bind a scope to a tracker"
    assert got["sync"]["shadow"]["title"] == "Bind a scope to a tracker"


def test_a_refused_remote_write_rejects_the_local_edit(edit_api, monkeypatch):
    """§4: "If the remote write fails, the edit is rejected with the error."
    Saving it anyway is exactly the local-state-the-remote-never-saw the whole
    write-through rule exists to prevent."""
    client, root, fake = edit_api
    t = tasks.add(root, title="Bind a scope", body="Objective: do it.")
    tracker_sync.publish("workshop", root, t["id"])

    def boom(_id, _patch):
        raise RuntimeError("github said no")

    monkeypatch.setattr(fake, "update", boom)
    r = client.patch(f"/api/tasks/workshop/{t['id']}", json={"title": "A new title"})
    assert r.status_code == 502
    assert "github said no" in r.json()["detail"]
    # The local ticket did not move, and the operator can see why.
    got = tasks.get_task(root, t["id"])
    assert got["title"] == "Bind a scope"
    assert "sync-error" in [e["kind"] for e in got["evidence"]]


def test_a_local_ticket_edits_without_touching_any_remote(edit_api):
    """Not every ticket is in the tracker, and an unlinked one must not pay for
    the integration."""
    client, root, fake = edit_api
    t = tasks.add(root, title="Bind a scope")
    r = client.patch(f"/api/tasks/workshop/{t['id']}", json={"title": "Renamed"})
    assert r.status_code == 200
    assert tasks.get_task(root, t["id"])["title"] == "Renamed"
    assert fake.issues == {}


def test_import_mode_keeps_an_edit_local(edit_api):
    """`import` means the tracker belongs to someone else: we never write its
    text, and the local edit is still saved."""
    client, root, fake = edit_api
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    tracker_bindings.set_mode("import")

    r = client.patch(f"/api/tasks/workshop/{t['id']}", json={"title": "Renamed"})
    assert r.status_code == 200
    assert tasks.get_task(root, t["id"])["title"] == "Renamed"
    assert fake.issues["1"].title == "Bind a scope"


def test_editing_the_body_alone_keeps_the_title(edit_api):
    """The pending edit is merged against the stored ticket before it is sent,
    so a body-only patch does not blank the remote title."""
    client, root, fake = edit_api
    t = tasks.add(root, title="Bind a scope", body="Objective: do it.")
    tracker_sync.publish("workshop", root, t["id"])

    client.patch(f"/api/tasks/workshop/{t['id']}", json={"body": "Objective: do it well."})
    assert fake.issues["1"].title == "Bind a scope"
    assert fake.issues["1"].body == "Objective: do it well."


def test_a_label_edit_keeps_the_selection_label_on(edit_api):
    """Replacing the label set without ours would drop the issue out of our own
    filter and make it read as a stranger's on the next poll."""
    client, root, fake = edit_api
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])

    client.patch(f"/api/tasks/workshop/{t['id']}", json={"labels": ["bug"]})
    assert set(fake.issues["1"].labels) == {"bug", "workshop"}


def test_an_operator_status_change_transitions_the_remote(edit_api):
    client, root, fake = edit_api
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])

    r = client.patch(f"/api/tasks/workshop/{t['id']}", json={"status": "done"})
    assert r.status_code == 200
    assert fake.issues["1"].state == "closed"
    assert r.json()["sync"]["shadow"]["state"] == "closed"


def test_a_failed_status_push_does_not_reject_the_local_transition(edit_api, monkeypatch):
    """Workshop owns status, so the state machine does not wait on github. The
    failure is not silent: it lands on the ticket the operator is looking at."""
    client, root, fake = edit_api
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])

    def boom(_id, _status):
        raise RuntimeError("github said no")

    monkeypatch.setattr(fake, "transition", boom)
    r = client.patch(f"/api/tasks/workshop/{t['id']}", json={"status": "done"})
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert "sync-error" in [e["kind"] for e in r.json()["evidence"]]


# ---- a run ending -------------------------------------------------------------


@pytest.fixture
def finishing(synced, monkeypatch, tmp_path):
    """`jobs.finish` wired to the synced scope's store, as a sweep would hit it."""
    from services import jobs, task_scope

    root, fake = synced
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(jobs, "JOBS_LOG", tmp_path / "jobs.jsonl")
    # The workshop root queue IS this store for the test, which is what makes
    # `key_for_root` answer "workshop" and find the binding.
    monkeypatch.setattr(task_scope, "WORKSHOP_ROOT", root)
    return jobs, root, fake


def test_a_finished_run_closes_the_issue_and_comments_once(finishing):
    """§9.2: one run-summary comment per run — not comment mirroring."""
    jobs, root, fake = finishing
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    tasks.update(root, t["id"], status="done")

    job = jobs.start(task_id=t["id"], trigger="manual")
    jobs.finish(job["id"], status="done")

    assert fake.issues["1"].state == "closed"
    assert len(fake.comments) == 1
    body = fake.comments[0][1]
    assert job["id"] in body
    assert "done" in body


def test_a_failed_run_says_so_on_the_issue_and_leaves_it_open(finishing):
    jobs, root, fake = finishing
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    tasks.update(root, t["id"], status="blocked")

    job = jobs.start(task_id=t["id"], trigger="manual")
    jobs.finish(job["id"], status="failed")

    assert fake.issues["1"].state == "open"
    assert "Nothing here is verified." in fake.comments[0][1]


def test_import_mode_comments_but_never_transitions(finishing):
    """The one write someone else's tracker gets, and no more."""
    jobs, root, fake = finishing
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    tracker_bindings.set_mode("import")
    tasks.update(root, t["id"], status="done")

    job = jobs.start(task_id=t["id"], trigger="manual")
    jobs.finish(job["id"], status="done")

    assert fake.transitions == []
    assert len(fake.comments) == 1


def test_a_disconnected_scope_hears_nothing(finishing):
    jobs, root, fake = finishing
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    tracker_bindings.set_mode("off")
    tasks.update(root, t["id"], status="done")

    job = jobs.start(task_id=t["id"], trigger="manual")
    jobs.finish(job["id"], status="done")

    assert fake.transitions == [] and fake.comments == []


def test_an_unlinked_ticket_produces_no_run_comment(finishing):
    jobs, root, fake = finishing
    t = tasks.add(root, title="Bind a scope")
    tasks.update(root, t["id"], status="done")

    job = jobs.start(task_id=t["id"], trigger="manual")
    jobs.finish(job["id"], status="done")

    assert fake.comments == []


def test_a_run_that_worked_two_tickets_comments_on_each_once(finishing):
    jobs, root, fake = finishing
    a = tasks.add(root, title="First")
    b = tasks.add(root, title="Second")
    tracker_sync.publish("workshop", root, a["id"])
    tracker_sync.publish("workshop", root, b["id"])

    job = jobs.start(trigger="manual")
    for t in (a, b):
        tasks.update(root, t["id"], job_id=job["id"], status="done")
    jobs.finish(job["id"], status="done")

    assert sorted(c[0] for c in fake.comments) == ["1", "2"]


def test_a_broken_remote_does_not_fail_the_job(finishing, monkeypatch):
    """A run that did its work is never marked failed because telling github
    about it went wrong."""
    jobs, root, fake = finishing
    t = tasks.add(root, title="Bind a scope")
    tracker_sync.publish("workshop", root, t["id"])
    tasks.update(root, t["id"], status="done")

    def boom(*a, **kw):
        raise RuntimeError("network")

    monkeypatch.setattr(fake, "transition", boom)
    monkeypatch.setattr(fake, "comment", boom)
    job = jobs.start(task_id=t["id"], trigger="manual")
    line = jobs.finish(job["id"], status="done")
    assert line["status"] == "done"
