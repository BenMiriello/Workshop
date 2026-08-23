"""Accepting finished work merges it (C2/C5).

These pin the DECISIONS, not the plumbing: a ticket with no change accepts
exactly as it always did, a ticket whose change will not merge is refused rather
than marked done, and the bookkeeping is written only after the merge really
happened. The database-backed half lives in test_changes.py.
"""
from __future__ import annotations

import pytest

from services import work_review


class _Changes:
    """Stand-in for services.changes, recording what it was asked to do."""

    def __init__(self, found=None, raise_on_list=None):
        self._found = found or []
        self._raise_on_list = raise_on_list
        self.marked: list[tuple[str, str]] = []
        self.base_shas: list[str | None] = []

    def list_changes(self, *, ticket_id=None, **_):
        if self._raise_on_list:
            raise self._raise_on_list
        return [c for c in self._found if ticket_id in (c.get("ticket_ids") or [])]

    def mark_merged(self, change_id, commit, *, base_sha=None):
        self.marked.append((str(change_id), str(commit)))
        self.base_shas.append(base_sha)
        return {"id": change_id, "status": "merged", "merged_sha": commit}


class _Worktrees:
    def __init__(self, result, base_sha="base000"):
        self.result = result
        self.base_sha = base_sha
        self.calls: list[tuple] = []
        self.state_calls: list[tuple] = []

    def branch_state(self, project_slug, branch_slug):
        self.state_calls.append((project_slug, branch_slug))
        return {"base_ref": "main", "base_sha": self.base_sha}

    def merge_branch(self, project_slug, branch_slug, *, message=""):
        self.calls.append((project_slug, branch_slug, message))
        return self.result


def _change(**over):
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "project_slug": "development",
        "branch_slug": "task-abc",
        "ticket_ids": ["abc"],
        "status": "ready",
    }
    base.update(over)
    return base


def _wire(monkeypatch, changes, worktrees):
    monkeypatch.setattr(work_review, "changes", changes)
    monkeypatch.setattr(work_review, "worktrees", worktrees)


# --- a ticket that never produced a change is unaffected -------------------

def test_a_ticket_with_no_change_merges_nothing_and_does_not_raise(monkeypatch):
    """Every ticket that existed before changes shipped is this case, and every
    ticket worked outside worktree mode still is. Acceptance must be untouched."""
    changes = _Changes(found=[])
    worktrees = _Worktrees({"ok": True})
    _wire(monkeypatch, changes, worktrees)

    out = work_review.merge_for_task("abc")

    assert out["merged"] is False
    assert "nothing to merge" in out["reason"]
    assert worktrees.calls == [], "it must not invent a branch to merge"


def test_an_unreachable_change_store_reads_as_nothing_to_merge(monkeypatch):
    """A file-backed scope has no change store. That is not an error for a
    caller asking whether there is something to merge; the answer is no."""
    changes = _Changes(raise_on_list=RuntimeError("no relational store"))
    worktrees = _Worktrees({"ok": True})
    _wire(monkeypatch, changes, worktrees)

    assert work_review.merge_for_task("abc")["merged"] is False


def test_an_already_merged_change_is_not_merged_again(monkeypatch):
    changes = _Changes(found=[_change(status="merged")])
    worktrees = _Worktrees({"ok": True})
    _wire(monkeypatch, changes, worktrees)

    assert work_review.merge_for_task("abc")["merged"] is False
    assert worktrees.calls == []


# --- the merge itself -------------------------------------------------------

def test_accepting_merges_the_branch_and_records_the_commit(monkeypatch):
    changes = _Changes(found=[_change()])
    worktrees = _Worktrees({"ok": True, "commit": "deadbee"})
    _wire(monkeypatch, changes, worktrees)

    out = work_review.merge_for_task("abc", message="take it")

    assert out["merged"] is True
    assert out["commit"] == "deadbee"
    assert worktrees.calls == [("development", "task-abc", "take it")]
    assert changes.marked == [(_change()["id"], "deadbee")], \
        "the change is marked merged with the commit that merged it"
    assert changes.base_shas == ["base000"], \
        "the base is pinned so the accepted diff stays recoverable"
    assert worktrees.state_calls == [("development", "task-abc")], \
        "and it is read BEFORE the merge, not after"


def test_a_conflict_refuses_the_acceptance_rather_than_completing_it(monkeypatch):
    """The case the whole step exists for. A ticket marked done over a branch
    that did not land is the self-reported completion being removed."""
    changes = _Changes(found=[_change()])
    worktrees = _Worktrees({"ok": False, "reason": "CONFLICT (content): f.py"})
    _wire(monkeypatch, changes, worktrees)

    with pytest.raises(work_review.MergeRefused) as e:
        work_review.merge_for_task("abc")

    assert "CONFLICT" in str(e.value), "the reason reaches the operator verbatim"
    assert changes.marked == [], "a failed merge must never be recorded as merged"


def test_a_change_that_names_no_branch_is_refused_not_guessed_at(monkeypatch):
    changes = _Changes(found=[_change(branch_slug="")])
    worktrees = _Worktrees({"ok": True})
    _wire(monkeypatch, changes, worktrees)

    with pytest.raises(work_review.MergeRefused):
        work_review.merge_for_task("abc")
    assert worktrees.calls == []


def test_losing_the_bookkeeping_does_not_un_accept_merged_code(monkeypatch):
    """The merge already happened; refusing here would leave the trunk carrying
    work the ticket says was never taken. It is logged loudly instead."""
    class _Broken(_Changes):
        def mark_merged(self, change_id, commit, *, base_sha=None):
            raise RuntimeError("store went away")

    changes = _Broken(found=[_change()])
    worktrees = _Worktrees({"ok": True, "commit": "abc1234"})
    _wire(monkeypatch, changes, worktrees)

    out = work_review.merge_for_task("abc")
    assert out["merged"] is True and out["commit"] == "abc1234"


# --- opening ----------------------------------------------------------------

def test_opening_never_raises_because_the_work_is_already_committed(monkeypatch):
    """The caller is an unattended sweep on its success leg. A bookkeeping
    failure there must not cost the branch, which is on disk either way."""
    class _Exploding:
        NothingToReview = RuntimeError

        def open_change(self, *a, **k):
            raise ValueError("store unreachable")

    monkeypatch.setattr(work_review, "changes", _Exploding())
    assert work_review.open_for_task("development", "task-abc", task_id="abc") is None


def test_a_branch_with_nothing_ahead_opens_no_change(monkeypatch):
    class _Nothing:
        class NothingToReview(Exception):
            pass

        def open_change(self, *a, **k):
            raise self.NothingToReview("0 ahead")

    monkeypatch.setattr(work_review, "changes", _Nothing())
    assert work_review.open_for_task("development", "task-abc", task_id="abc") is None


def test_opening_carries_the_ticket_and_asks_for_ready(monkeypatch):
    seen: dict = {}

    class _Rec:
        NothingToReview = RuntimeError

        def open_change(self, project_slug, branch_slug, **kw):
            seen.update({"project_slug": project_slug, "branch_slug": branch_slug, **kw})
            return {"id": "c1"}

    monkeypatch.setattr(work_review, "changes", _Rec())
    out = work_review.open_for_task("development", "task-abc", task_id="abc",
                                    title="Do the thing")
    assert out == {"id": "c1"}
    assert seen["ticket_ids"] == ["abc"]
    assert seen["status"] == "ready", "a finished branch is ready to review, not a draft"
    assert seen["title"] == "Do the thing"
