"""One tracker connection, per-scope targets, and a triage inbox that gates it.

The properties worth pinning are the ones that make an integration trustworthy
rather than the ones that make it work: a poll never creates runnable work, a
rejection is permanent, a disconnect never destroys anything, and an account the
installation has declared off limits is refused everywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import (tasks, tracker_bindings, trackers, triage_inbox)  # noqa: E402
from services.trackers import RemoteIssue  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    from services import ownership
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    ownership.set_current_owner(6)
    yield tmp_path / "store"
    ownership.clear_current_owner()


# ---- the status map ---------------------------------------------------------


def test_finished_and_dropped_close_for_different_reasons():
    assert trackers.remote_state("done") == ("closed", "completed")
    assert trackers.remote_state("cancelled") == ("closed", "not_planned")


def test_every_open_status_maps_to_open():
    for s in ("todo", "doing", "in_review", "blocked", "parked"):
        assert trackers.remote_state(s) == ("open", None)


def test_a_draft_has_no_remote_mapping():
    """A draft is not work yet and never reaches a tracker, so mapping one would
    be a lie about what the tracker contains."""
    assert "draft" not in trackers.STATUS_TO_REMOTE


def test_a_not_planned_close_comes_back_as_cancelled():
    """The one nuance worth carrying across: closing as not-planned is a
    cancellation, not a completion."""
    assert trackers.local_status("closed", closed_reason="not_planned") == "cancelled"
    assert trackers.local_status("closed", closed_reason="completed") == "done"
    assert trackers.local_status("open") == "todo"


def test_the_field_table_covers_every_syncable_field():
    assert set(trackers.OWNERSHIP) == set(trackers.SYNCABLE)


def test_workshop_owns_status_and_the_tracker_owns_the_words():
    assert trackers.OWNERSHIP["status"] == "workshop"
    for f in ("title", "body", "labels", "assignee"):
        assert trackers.OWNERSHIP[f] == "remote"


# ---- the idempotency marker -------------------------------------------------


def test_the_marker_round_trips_and_is_invisible():
    marker = trackers.idem_marker("abc123")
    body = f"Objective: do it.\n\n{marker}"
    assert trackers.find_idem(body) == "abc123"
    # It is ours, so it must never read as a change the operator made.
    assert trackers.strip_idem(body) == "Objective: do it."


def test_a_body_with_no_marker_claims_nothing():
    assert trackers.find_idem("just a body") is None


# ---- the connection ---------------------------------------------------------


def test_connecting_stores_one_provider_and_a_target(env):
    rec = tracker_bindings.connect(provider="github",
                                   target="acme/workshop-tickets")
    assert rec["provider"] == "github"
    assert rec["targets"]["workshop"] == "acme/workshop-tickets"
    assert rec["mode"] == "import"


def test_a_second_provider_is_refused_not_swapped(env, monkeypatch):
    """One integration at a time, and switching is a deliberate act with
    consequences for every linked ticket.

    Linear is pretended-built here so this tests the one-at-a-time rule rather
    than the unsupported-provider rule, which has its own test below and which
    correctly fires first for a provider that genuinely does not exist yet."""
    monkeypatch.setattr(trackers, "PROVIDERS", ("github", "linear"))
    monkeypatch.setattr(tracker_bindings.trackers, "PROVIDERS", ("github", "linear"))
    tracker_bindings.connect(provider="github", target="acme/a")
    with pytest.raises(ValueError, match="disconnect it first"):
        tracker_bindings.connect(provider="linear", target="team")


def test_an_unbuilt_provider_is_refused(env):
    with pytest.raises(ValueError, match="unsupported provider"):
        tracker_bindings.connect(provider="jira", target="PROJ")


def test_a_blocked_account_is_refused_as_a_target(env, monkeypatch):
    """The list is the installation's, so the test declares one and expects it
    honoured. Nothing in the platform knows any particular org name."""
    from services import github as gh_repo

    monkeypatch.setenv(gh_repo.BLOCKED_OWNERS_ENV, "offlimits-org")
    with pytest.raises(ValueError, match="offlimits-org"):
        tracker_bindings.connect(provider="github", target="offlimits-org/thing")


def test_nothing_is_blocked_when_the_install_names_nothing(env, monkeypatch):
    from services import github as gh_repo

    monkeypatch.delenv(gh_repo.BLOCKED_OWNERS_ENV, raising=False)
    rec = tracker_bindings.connect(provider="github", target="someone/thing")
    assert rec["targets"]["workshop"] == "someone/thing"


def test_scopes_point_at_their_own_targets(env):
    tracker_bindings.connect(provider="github", target="acme/a", scope="workshop")
    tracker_bindings.connect(provider="github", target="acme/b",
                             scope="project:widgets")
    assert tracker_bindings.target_for("workshop") == "acme/a"
    assert tracker_bindings.target_for("project:widgets") == "acme/b"


def test_a_scope_with_no_target_does_not_inherit_one(env):
    """Filing into a repo nobody chose is how tickets reach a stranger's tracker."""
    tracker_bindings.connect(provider="github", target="acme/a", scope="workshop")
    assert tracker_bindings.target_for("project:widgets") is None
    assert tracker_bindings.binding_for("project:widgets") is None


def test_import_mode_forbids_writes_and_sync_allows_them(env):
    tracker_bindings.connect(provider="github", target="acme/a", mode="import")
    assert tracker_bindings.writes_allowed("workshop") is False
    tracker_bindings.set_mode("sync")
    assert tracker_bindings.writes_allowed("workshop") is True


def test_mode_off_means_not_integrated(env):
    tracker_bindings.connect(provider="github", target="acme/a")
    tracker_bindings.set_mode("off")
    assert tracker_bindings.binding_for("workshop") is None


# ---- triage -----------------------------------------------------------------


class FakeTracker:
    provider = "github"

    def __init__(self, issues):
        self._issues = issues

    def list_since(self, cursor, filt):
        return self._issues, "2026-08-10T00:00:00Z"

    def labels(self):
        return []


def _issue(n: int, *, workshop_id=None, title=None) -> RemoteIssue:
    return RemoteIssue(
        remote_id=str(n), key=f"#{n}", url=f"https://github.com/x/y/issues/{n}",
        title=title or f"Issue {n}", body="Objective: from the tracker.",
        state="open", labels=["workshop"], updated="2026-08-10T00:00:00Z",
        workshop_id=workshop_id)


@pytest.fixture
def integrated(env, monkeypatch):
    tracker_bindings.connect(provider="github", target="acme/a")
    return env


def _stub_adapter(monkeypatch, issues):
    monkeypatch.setattr(trackers, "for_binding", lambda b: FakeTracker(issues))
    monkeypatch.setattr(triage_inbox.trackers, "for_binding",
                        lambda b: FakeTracker(issues))


def test_a_poll_creates_candidates_not_tickets(integrated, monkeypatch):
    """The whole point: nothing external ever starts a worker."""
    root = integrated
    _stub_adapter(monkeypatch, [_issue(1), _issue(2)])
    out = triage_inbox.sync("workshop", root)
    assert out["raised"] == 2
    assert tasks.list_tasks(root) == []


def test_accepting_creates_a_linked_draft(integrated, monkeypatch):
    root = integrated
    _stub_adapter(monkeypatch, [_issue(1)])
    triage_inbox.sync("workshop", root)
    cand = triage_inbox.list_candidates("workshop")[0]

    out = triage_inbox.accept(cand["id"], root)
    task = out["task"]
    # A draft, because someone else's issue is a proposal as far as we are
    # concerned, and it goes through the same approval as any other.
    assert task["status"] == "draft"
    assert task["sync"]["remote_id"] == "1"
    assert task["sync"]["state"] == "linked"
    assert task["scoped_by"] == "import:github"
    # The shadow is what makes a later three-way merge possible.
    assert task["sync"]["shadow"]["title"] == "Issue 1"


def test_accepting_twice_does_not_create_two_tickets(integrated, monkeypatch):
    root = integrated
    _stub_adapter(monkeypatch, [_issue(1)])
    triage_inbox.sync("workshop", root)
    cand = triage_inbox.list_candidates("workshop")[0]
    triage_inbox.accept(cand["id"], root)
    again = triage_inbox.accept(cand["id"], root)
    assert again["created"] is False
    assert len(tasks.list_tasks(root)) == 1


def test_ignoring_is_permanent_across_polls(integrated, monkeypatch):
    """A rejection a later poll undoes is why people switch integrations off."""
    root = integrated
    _stub_adapter(monkeypatch, [_issue(1)])
    triage_inbox.sync("workshop", root)
    cand = triage_inbox.list_candidates("workshop")[0]
    triage_inbox.ignore(cand["id"])

    triage_inbox.sync("workshop", root)
    assert triage_inbox.list_candidates("workshop", "pending") == []
    assert len(triage_inbox.list_candidates("workshop", "ignored")) == 1


def test_an_ignore_can_be_undone(integrated, monkeypatch):
    """Permanent with no way back is a trap, not a safeguard."""
    root = integrated
    _stub_adapter(monkeypatch, [_issue(1)])
    triage_inbox.sync("workshop", root)
    cand = triage_inbox.list_candidates("workshop")[0]
    triage_inbox.ignore(cand["id"])
    triage_inbox.unignore(cand["id"])
    assert len(triage_inbox.list_candidates("workshop", "pending")) == 1


def test_an_already_accepted_candidate_is_not_re_raised(integrated, monkeypatch):
    root = integrated
    _stub_adapter(monkeypatch, [_issue(1)])
    triage_inbox.sync("workshop", root)
    cand = triage_inbox.list_candidates("workshop")[0]
    triage_inbox.accept(cand["id"], root)
    triage_inbox.sync("workshop", root)
    assert triage_inbox.list_candidates("workshop", "pending") == []


def test_an_issue_we_wrote_is_not_a_candidate(integrated, monkeypatch):
    """It carries our marker, so it is the other side of a ticket we have — not
    something new to triage."""
    root = integrated
    _stub_adapter(monkeypatch, [_issue(1, workshop_id="deadbeef")])
    out = triage_inbox.sync("workshop", root)
    assert out["raised"] == 0
    assert out["skipped"] == 1


def test_polling_an_unintegrated_scope_is_a_no_op(env, monkeypatch):
    assert triage_inbox.sync("workshop", env)["reason"] == "not integrated"


def test_snoozing_takes_it_off_the_list(integrated, monkeypatch):
    root = integrated
    _stub_adapter(monkeypatch, [_issue(1)])
    triage_inbox.sync("workshop", root)
    cand = triage_inbox.list_candidates("workshop")[0]
    triage_inbox.snooze(cand["id"], days=3)
    assert triage_inbox.list_candidates("workshop", "pending") == []
    assert triage_inbox.get(cand["id"])["snooze_until"]


# ---- disconnect -------------------------------------------------------------


def test_unlinking_freezes_the_link_rather_than_erasing_it(integrated, monkeypatch):
    """A ticket has to be able to say what it used to be, permanently."""
    root = integrated
    _stub_adapter(monkeypatch, [_issue(1)])
    triage_inbox.sync("workshop", root)
    cand = triage_inbox.list_candidates("workshop")[0]
    task = triage_inbox.accept(cand["id"], root)["task"]

    tasks.update(root, task["id"], unlink=True)
    got = tasks.get_task(root, task["id"])
    assert got["sync"] is None
    assert got["external_refs"][0]["remote_id"] == "1"
    assert got["external_refs"][0]["unlinked_at"]


def test_disconnecting_clears_the_connection_and_keeps_the_source(env):
    tracker_bindings.connect(provider="github", target="acme/a")
    out = tracker_bindings.disconnect()
    assert out["provider"] is None
    assert out["disconnected"]["provider"] == "github"
    assert tracker_bindings.binding_for("workshop") is None
