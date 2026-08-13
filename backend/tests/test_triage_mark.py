"""The mark that lets a queue triager terminate.

THE BUG THIS FILE EXISTS TO PREVENT. The note triager keys its frontier on file
mtime, which is safe only because that role cannot write notes. A QUEUE triager
writes the store it reads, and `tasks.update()` sets `updated` on every single
write — so a frontier keyed on `updated` re-selects every ticket the triager
itself just wrote to, forever. Marking a ticket `underspecified` would re-arm it
for the next pass, which would mark it again.

The fix is a content hash over the spec fields only. `test_awaiting_write_does_
not_move_the_hash` is the regression guard: it must fail if `updated` is ever
added to the hashed payload.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import tasks  # noqa: E402


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    return tmp_path


def _add(root: Path, **kw) -> dict:
    kw.setdefault("title", "Bind a scope to an external tracker")
    kw.setdefault("body", "Objective: one binding per scope.")
    return tasks.add(root, **kw)


def _mark(root: Path, task: dict, decision: str = "ok") -> dict:
    """Write the mark the triager would write for this decision."""
    return tasks.update(root, task["id"], triage={
        "decision": decision, "spec_hash": tasks.spec_hash(task),
    })


# --- the loop guard ---------------------------------------------------------

def test_status_write_does_not_move_the_hash(root: Path) -> None:
    """todo -> doing -> todo is not a new question."""
    t = _add(root)
    before = tasks.spec_hash(t)
    moved = tasks.update(root, t["id"], status="doing")
    assert tasks.spec_hash(moved) == before
    back = tasks.update(root, t["id"], status="todo")
    assert tasks.spec_hash(back) == before


def test_awaiting_write_does_not_move_the_hash(root: Path) -> None:
    """THE regression test. The triager's own `underspecified` write must not
    re-arm the ticket. If this fails, the queue triager does not terminate."""
    t = _add(root)
    before = tasks.spec_hash(t)
    blocked = tasks.update(root, t["id"], awaiting={
        "ask": "What does done look like here?",
        "kind": "missing-info",
        "raised_by": "agent:queue-triager",
    })
    assert blocked["awaiting"] is not None, "the block must actually have landed"
    assert tasks.spec_hash(blocked) == before


def test_triage_pass_terminates(root: Path) -> None:
    """The whole point, asserted end to end: a ticket the triager has judged and
    written a block onto is NOT due on the next pass."""
    t = _add(root)
    assert tasks.triage_due(t) is True
    blocked = tasks.update(root, t["id"], awaiting={
        "ask": "What does done look like?", "kind": "missing-info",
    })
    marked = _mark(root, blocked, "underspecified")
    assert tasks.triage_due(marked) is False
    # And re-reading from the store, not just the returned record.
    fresh = tasks.get_task(root, t["id"])
    assert tasks.triage_due(fresh) is False


def test_execution_and_priority_do_not_move_the_hash(root: Path) -> None:
    """They change routing, not whether the spec is adequate."""
    t = _add(root)
    before = tasks.spec_hash(t)
    t = tasks.update(root, t["id"], execution="code")
    t = tasks.update(root, t["id"], priority=50)
    assert tasks.spec_hash(t) == before


# --- and it must still re-arm when the question really changed --------------

def test_body_change_moves_the_hash(root: Path) -> None:
    t = _add(root)
    marked = _mark(root, t)
    assert tasks.triage_due(marked) is False
    edited = tasks.update(root, t["id"], body="Objective: something else entirely.")
    assert tasks.triage_due(edited) is True


def test_title_and_depends_on_and_effort_move_the_hash(root: Path) -> None:
    dep = _add(root, title="A prerequisite")
    for patch in ({"title": "A different title"},
                  {"depends_on": [dep["id"]]},
                  {"effort": "deep"}):
        t = _add(root)
        assert tasks.triage_due(_mark(root, t)) is False
        assert tasks.triage_due(tasks.update(root, t["id"], **patch)) is True, patch


def test_depends_on_hash_is_order_independent(root: Path) -> None:
    a, b = _add(root, title="A"), _add(root, title="B")
    t = _add(root, depends_on=[a["id"], b["id"]])
    reordered = dict(t, depends_on=[b["id"], a["id"]])
    assert tasks.spec_hash(t) == tasks.spec_hash(reordered)


# --- the record itself ------------------------------------------------------

def test_no_mark_is_due(root: Path) -> None:
    t = _add(root)
    assert t["triage"] is None
    assert tasks.triage_due(t) is True


def test_clearing_the_mark_makes_it_due_again(root: Path) -> None:
    t = _mark(root, _add(root))
    assert tasks.triage_due(t) is False
    cleared = tasks.update(root, t["id"], triage={})
    assert cleared["triage"] is None
    assert tasks.triage_due(cleared) is True


def test_unknown_decision_reads_as_none_rather_than_dropping_the_mark(root: Path) -> None:
    """Dropping the mark would re-select the ticket, which is the failure mode
    the field exists to stop. An unrecognized decision degrades instead."""
    t = _add(root)
    marked = tasks.update(root, t["id"], triage={
        "decision": "whatever", "spec_hash": tasks.spec_hash(t)})
    assert marked["triage"]["decision"] == "none"
    assert tasks.triage_due(marked) is False


def test_triage_is_an_exec_field(root: Path) -> None:
    """Run metadata, never sent to an external tracker."""
    assert "triage" in tasks.EXEC_FIELDS
    assert "triage" not in tasks.SYNCABLE_FIELDS
    t = _mark(root, _add(root))
    assert t["exec"]["triage"]["decision"] == "ok"


def test_a_triage_mark_does_not_fence_a_holding_worker(root: Path) -> None:
    """Recording a decision is not an edit to the ticket. If the mark bumped
    `claim_token`, triaging the queue would fence out every running worker."""
    t = _add(root)
    claimed = tasks.claim(root, t["id"], "job-1")
    token = claimed["claim_token"]
    _mark(root, claimed)
    assert tasks.get_task(root, t["id"])["claim_token"] == token
