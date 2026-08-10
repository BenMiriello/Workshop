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
