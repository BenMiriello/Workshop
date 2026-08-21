"""Phase-4 selection: what a SWEEP may pick up, versus what a person may.

The distinction these tests pin is the one the plan draws in §3.1 and it is easy
to collapse by accident:

    readiness never vetoes a human            next_ready(unattended=False)
    only ready tickets run unattended         next_ready(unattended=True)

`next_ready` is consulted by BOTH paths, which is why `unattended` is an extra
predicate rather than a change to what the function means. If a future edit makes
readiness apply unconditionally, `test_a_human_path_is_unchanged` fails.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import tasks  # noqa: E402
from services import scope_execution  # noqa: E402


@pytest.fixture(autouse=True)
def _armed_scope(monkeypatch):
    # These tests exercise what the unattended SWEEP selects, which now presupposes
    # the scope is armed for background execution (services/scope_execution). Arm
    # every scope so the selection logic under test is reached; the arming gate
    # itself is covered by test_scope_execution.
    monkeypatch.setattr(scope_execution, "is_armed", lambda root: True)


# Passes every readiness check: objective, acceptance, entry points, boundaries.
READY_BODY = (
    "Objective: one binding per scope, chosen in Settings.\n"
    "Output: a binding record exists and `GET /api/trackers` returns it.\n"
    "Start from: backend/services/tracker_bindings.py\n"
    "Boundaries: do not touch the chat store."
)


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    return tmp_path


def _ticket(root: Path, *, title: str, execution=None, body: str = READY_BODY,
            priority: int = 0) -> dict:
    return tasks.add(root, title=title, body=body, execution=execution,
                     priority=priority, touches=["backend/services/x.py"])


def test_unattended_skips_unclassified(root: Path) -> None:
    """The fail-closed case. An unclassified ticket is not `code`."""
    _ticket(root, title="Unclassified but perfectly well specified")
    assert tasks.next_ready(root) is not None
    assert tasks.next_ready(root, unattended=True) is None


@pytest.mark.parametrize("execution", ["judgement", "external"])
def test_unattended_skips_non_code(root: Path, execution: str) -> None:
    _ticket(root, title="Not for a machine", execution=execution)
    assert tasks.next_ready(root) is not None
    assert tasks.next_ready(root, unattended=True) is None


def test_unattended_skips_unready(root: Path) -> None:
    """`code` is necessary and not sufficient: the spec still has to be adequate."""
    tasks.add(root, title="Fix the thing", body="", execution="code")
    assert tasks.next_ready(root) is not None
    assert tasks.next_ready(root, unattended=True) is None


def test_unattended_picks_a_code_and_ready_ticket(root: Path) -> None:
    t = _ticket(root, title="Bind a scope to an external tracker", execution="code")
    picked = tasks.next_ready(root, unattended=True)
    assert picked is not None and picked["id"] == t["id"]


def test_a_human_path_is_unchanged(root: Path) -> None:
    """The ratified rule: readiness must never refuse a person. This is the
    regression guard against making the predicate unconditional."""
    unready = tasks.add(root, title="Vague but urgent", body="", priority=100)
    picked = tasks.next_ready(root)
    assert picked is not None and picked["id"] == unready["id"]


def test_the_root_ticket_case(root: Path) -> None:
    """The measurement that reordered the plan, as a test.

    The live queue's head was `[SEC-C3] Verify inbound IPv6 firewall policy
    (root)` at priority 100, because ordering is priority-first and consults
    nothing else. A human still gets it first — that is correct, it IS the most
    important ticket. The sweep must not, because no agent here has root.
    """
    root_ticket = _ticket(root, title="[SEC-C3] Verify inbound IPv6 firewall policy (root)",
                          execution="external", priority=100)
    buildable = _ticket(root, title="Bind a scope to an external tracker",
                        execution="code", priority=0)

    human = tasks.next_ready(root)
    assert human is not None and human["id"] == root_ticket["id"]

    sweep = tasks.next_ready(root, unattended=True)
    assert sweep is not None and sweep["id"] == buildable["id"]


def test_ordering_within_the_eligible_set_is_still_priority_first(root: Path) -> None:
    _ticket(root, title="Lower priority", execution="code", priority=1)
    high = _ticket(root, title="Higher priority", execution="code", priority=90)
    picked = tasks.next_ready(root, unattended=True)
    assert picked is not None and picked["id"] == high["id"]


def test_dependencies_still_gate_unattended(root: Path) -> None:
    dep = _ticket(root, title="The prerequisite", execution="code")
    blocked = tasks.add(root, title="The dependent", body=READY_BODY,
                        execution="code", depends_on=[dep["id"]],
                        touches=["backend/services/x.py"])
    picked = tasks.next_ready(root, unattended=True)
    assert picked is not None and picked["id"] == dep["id"]
    tasks.update(root, dep["id"], status="done")
    picked = tasks.next_ready(root, unattended=True)
    assert picked is not None and picked["id"] == blocked["id"]


def test_empty_queue_returns_none(root: Path) -> None:
    assert tasks.next_ready(root, unattended=True) is None
