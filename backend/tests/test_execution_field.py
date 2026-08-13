"""`execution`: whether a machine can do this ticket at all.

The queue is ordered priority-first, and on the day this was written the head of
it was a root-requiring security ticket at priority 100. An unattended sweep that
GUESSED would have spent its first five runs failing on work no agent can do. So
the field fails closed in every direction: unset is not `code`, and a typo is not
`code` either.
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


def test_unset_is_none(root: Path) -> None:
    assert _add(root)["execution"] is None


@pytest.mark.parametrize("value", tasks.EXECUTIONS)
def test_valid_values_round_trip(root: Path, value: str) -> None:
    t = _add(root, execution=value)
    assert t["execution"] == value
    assert tasks.get_task(root, t["id"])["execution"] == value


@pytest.mark.parametrize("bogus", ["cody", "CODE ", "", "true", "1", None])
def test_unknown_value_coerces_to_none_not_code(root: Path, bogus) -> None:
    """The one assertion that matters. A typo must never read as eligible."""
    t = _add(root, execution=bogus)
    assert t["execution"] in (None, "code")
    if bogus not in ("CODE ",):
        assert t["execution"] is None


def test_case_and_whitespace_are_tolerated(root: Path) -> None:
    assert _add(root, execution="  Code ")["execution"] == "code"


def test_update_sets_and_clears(root: Path) -> None:
    t = _add(root)
    assert tasks.update(root, t["id"], execution="judgement")["execution"] == "judgement"
    # "" clears back to UNCLASSIFIED, which is ineligible — not to "code".
    assert tasks.update(root, t["id"], execution="")["execution"] is None


def test_execution_is_spec_not_exec(root: Path) -> None:
    """A tracker would legitimately want it, and it is a thing the operator sets
    rather than a thing a run writes."""
    assert "execution" not in tasks.EXEC_FIELDS


def test_execution_does_not_fence_a_holding_worker(root: Path) -> None:
    t = _add(root)
    claimed = tasks.claim(root, t["id"], "job-1")
    token = claimed["claim_token"]
    tasks.update(root, t["id"], execution="code")
    assert tasks.get_task(root, t["id"])["claim_token"] == token
