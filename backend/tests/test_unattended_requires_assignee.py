"""An unattended sweep must NAME a worker, and it sees only what is assigned to it.

WHY THIS IS A GATE AND NOT A PREFERENCE. Every other unattended gate is a filter
over everything ever filed: the scope is armed, the ticket is `todo` with its
dependencies met, its `execution` is `code`, its spec passes
`ticket_readiness`. None of them is a decision about a PARTICULAR ticket, so
arming one scope armed the entire backlog at once -- measured on 2026-09-04 as
130 of 184 open tickets, 105 of which agents had filed for themselves
(`docs/what-may-run-unattended-2026-09-04.md`). That measurement is the reason
autonomy is off, and it was accurate.

The rule this file pins is one sentence: **the unattended path is always
assignee-scoped, and `operator` is not a worker.** It buys the property the
other gates cannot:

    an unscoped sweep selects NOTHING            however full the queue is
    assigning a ticket to a worker               is the grant, per ticket
    unassigning it                               is the revocation
    the attended path                            is untouched

That last line is half the rule and the easiest half to lose. `next_ready` is
consulted by BOTH the sweep and a person acting on their own instruction, and
narrowing a person is not what this is for; `test_the_attended_path_is_
unchanged` fails if a later edit makes the scoping unconditional.

The fixture arms the scope FOR REAL rather than patching a predicate, for the
reason `test_next_ready_unattended.py` documents at length: an assertion of the
form `is None` goes green the moment anything upstream short-circuits, so every
refusal here is paired with a selection that proves the queue was live.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import tasks  # noqa: E402

WORKER = "agent:wstest-maker"
OTHER_WORKER = "agent:wstest-other"

READY_BODY = (
    "Objective: one binding per scope, chosen in Settings.\n"
    "Output: a binding record exists and `GET /api/trackers` returns it.\n"
    "Start from: backend/services/tracker_bindings.py\n"
    "Boundaries: do not touch the chat store."
)


@pytest.fixture()
def root(tmp_path: Path, monkeypatch) -> Path:
    """A file-backed store in a scope that is genuinely armed `autonomous`.

    `key_for_root` is structural, so `<tmp>/projects/widgets` IS
    `project:widgets`, and `scope_execution` reads the mode from
    `$WORKSHOP_ROOT/.runtime`. Nothing about the operator's own tree, tenant or
    arming state is read or written by any test here.
    """
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    scope_root = tmp_path / "projects" / "widgets"
    scope_root.mkdir(parents=True)
    (tmp_path / ".runtime").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".runtime" / "scope-execution.json").write_text(
        json.dumps({"project:widgets": "autonomous"}))
    # The refusal log is deduplicated per process; a test asserting on it must
    # start from a clean memo or it inherits the previous test's line.
    tasks._LAST_REFUSAL.clear()
    return scope_root


def _ticket(root: Path, *, title: str = "Bind a scope to an external tracker",
            assignee: str | None = tasks.DEFAULT_OWNER, priority: int = 0) -> dict:
    """A ticket that passes every gate EXCEPT, by default, the assignment one."""
    return tasks.add(root, title=title, body=READY_BODY, execution="code",
                     priority=priority, touches=["backend/services/x.py"],
                     assignee=assignee)


# ---- the refusal -----------------------------------------------------------

def test_an_unscoped_unattended_sweep_selects_nothing(root: Path) -> None:
    """THE HEADLINE. A queue full of tickets that pass every other gate, and a
    sweep that named no worker gets none of them."""
    for i in range(5):
        _ticket(root, title=f"Perfectly runnable ticket {i}", priority=i)

    assert tasks.next_ready(root, unattended=True) is None
    # ...and the queue really was live, which is what stops this going vacuous.
    assert tasks.next_ready(root) is not None


def test_the_operator_is_not_a_worker(root: Path) -> None:
    """The default assignee of all 184 open tickets on 2026-09-04. Naming it is
    not naming a worker, or the rule would be satisfied by the status quo."""
    _ticket(root, assignee=tasks.DEFAULT_OWNER)
    assert tasks.next_ready(root, assignee=tasks.DEFAULT_OWNER,
                            unattended=True) is None


def test_an_unassigned_ticket_is_not_eligible_for_anyone(root: Path) -> None:
    """The attended path treats an unassigned ticket as claimable by ANY sweep
    (`_match_assignee`, and `in_scope`'s fallback). The unattended path must not,
    or unassigning would revoke nothing.

    Measured while writing this: a file-backed store cannot actually hold an
    unassigned ticket for long -- `_normalize` mirrors `owner = assignee or
    operator` on write and `_assignee_from_legacy` reads a blank assignee back
    through `owner`, so "nobody" round-trips to the operator. The rule is
    written to cover both states rather than relying on that, because the
    relational stores and the sync path do not share the mirror.
    """
    _ticket(root, assignee=None)
    assert tasks.next_ready(root, assignee=WORKER, unattended=True) is None
    assert tasks.next_ready(root, assignee=OTHER_WORKER, unattended=True) is None
    assert tasks.next_ready(root, unattended=True) is None


def test_a_worker_sees_only_its_own(root: Path) -> None:
    mine = _ticket(root, title="Assigned to me", assignee=WORKER)
    _ticket(root, title="Assigned to somebody else", assignee=OTHER_WORKER,
            priority=90)

    picked = tasks.next_ready(root, assignee=WORKER, unattended=True)
    assert picked is not None and picked["id"] == mine["id"]


# ---- the grant, and taking it back -----------------------------------------

def test_assigning_to_a_worker_is_what_makes_it_eligible(root: Path) -> None:
    """Direction one. The same ticket, the same armed scope, the same sweep: the
    only thing that changes is who it is assigned to."""
    t = _ticket(root, assignee=tasks.DEFAULT_OWNER)
    assert tasks.next_ready(root, assignee=WORKER, unattended=True) is None

    tasks.update(root, t["id"], set_assignee=WORKER)

    picked = tasks.next_ready(root, assignee=WORKER, unattended=True)
    assert picked is not None and picked["id"] == t["id"]


def test_unassigning_reverses_it(root: Path) -> None:
    """Direction two, and the reason this needs no revocation mechanism of its
    own: the grant is a field on the ticket, so it comes back the same way."""
    t = _ticket(root, assignee=WORKER)
    assert tasks.next_ready(root, assignee=WORKER, unattended=True) is not None

    tasks.update(root, t["id"], set_assignee=tasks.DEFAULT_OWNER)

    assert tasks.next_ready(root, assignee=WORKER, unattended=True) is None
    # Still open work for a person, which is the whole point of handing it back.
    assert tasks.next_ready(root) is not None


# ---- what must NOT change --------------------------------------------------

def test_the_attended_path_is_unchanged(root: Path) -> None:
    """A person acting on their own instruction is not a sweep, and this rule
    must not narrow one. The regression guard against making the scoping
    unconditional: everything the attended path returned before, it still
    returns -- including a ticket on the operator, and an unready one, which the
    unattended path refuses twice over."""
    unready = tasks.add(root, title="Vague but urgent", body="", priority=100,
                        assignee=tasks.DEFAULT_OWNER)
    _ticket(root, title="Someone else's", assignee=OTHER_WORKER, priority=50)

    # Priority-first, no readiness gate, no assignment gate: the human gets the
    # most important thing whoever it belongs to.
    picked = tasks.next_ready(root)
    assert picked is not None and picked["id"] == unready["id"]

    # And scoped to a worker, still no readiness or execution gate.
    scoped = tasks.next_ready(root, assignee=OTHER_WORKER)
    assert scoped is not None and scoped["title"] == "Someone else's"


def test_the_other_unattended_gates_still_apply_to_the_assigned_set(
        root: Path) -> None:
    """The rule composes with the existing gates rather than replacing them.
    Assigning a ticket to a worker does not make it runnable if a machine cannot
    do it or the spec is inadequate."""
    tasks.add(root, title="Assigned but not for a machine", body=READY_BODY,
              execution="judgement", assignee=WORKER,
              touches=["backend/services/x.py"])
    tasks.add(root, title="Assigned but underspecified", body="",
              execution="code", assignee=WORKER)

    assert tasks.next_ready(root, assignee=WORKER, unattended=True) is None
    assert tasks.next_ready(root) is not None


def test_arming_a_scope_grants_nothing_on_its_own(root: Path) -> None:
    """The property the arrangement lacked. The scope is armed `autonomous` for
    every assertion in this file, and it takes an assignment -- not the arming --
    to make one ticket runnable."""
    for i in range(3):
        _ticket(root, title=f"In the backlog {i}", assignee=tasks.DEFAULT_OWNER)
    mine = _ticket(root, title="Handed to a worker on purpose", assignee=WORKER)

    picked = tasks.next_ready(root, assignee=WORKER, unattended=True)
    assert picked is not None and picked["id"] == mine["id"]
    assert tasks.next_ready(root, unattended=True) is None


# ---- the refusal is legible ------------------------------------------------

def test_a_refusal_does_not_look_like_an_empty_queue(root: Path, caplog) -> None:
    """Four silent-success defects were found in this tree in two days, and each
    was a refusal that returned the same value as "nothing to do". These two
    outcomes are both None and must not be the same line in the log."""
    for i in range(3):
        _ticket(root, title=f"Runnable but unassigned {i}")

    with caplog.at_level(logging.WARNING, logger="services.tasks"):
        assert tasks.next_ready(root, unattended=True) is None
    said = "\n".join(r.getMessage() for r in caplog.records)
    assert "no worker was named" in said
    assert "3 ticket(s)" in said


def test_a_worker_with_nothing_assigned_says_so(root: Path, caplog) -> None:
    for i in range(2):
        _ticket(root, title=f"Runnable, on the operator {i}",
                assignee=tasks.DEFAULT_OWNER)

    with caplog.at_level(logging.WARNING, logger="services.tasks"):
        assert tasks.next_ready(root, assignee=WORKER, unattended=True) is None
    said = "\n".join(r.getMessage() for r in caplog.records)
    assert WORKER in said and "none is assigned to it" in said


def test_an_actually_empty_queue_stays_quiet(root: Path, caplog) -> None:
    """The other half: "there is no work" is a true, boring answer and must not
    be dressed up as a refusal, or the refusal stops being worth reading."""
    with caplog.at_level(logging.WARNING, logger="services.tasks"):
        assert tasks.next_ready(root, unattended=True) is None
    assert caplog.records == []


def test_the_refusal_does_not_repeat_itself_unchanged(root: Path, caplog) -> None:
    """A resident runner polls twice a minute forever. The line is emitted when
    the condition begins and whenever the count changes, not on every poll."""
    _ticket(root, title="Runnable but unassigned")

    with caplog.at_level(logging.WARNING, logger="services.tasks"):
        tasks.next_ready(root, unattended=True)
        tasks.next_ready(root, unattended=True)
        tasks.next_ready(root, unattended=True)
    assert len(caplog.records) == 1

    with caplog.at_level(logging.WARNING, logger="services.tasks"):
        _ticket(root, title="A second runnable but unassigned ticket")
        tasks.next_ready(root, unattended=True)
    assert len(caplog.records) == 2
