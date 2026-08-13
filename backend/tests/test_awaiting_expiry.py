"""Which blocked tickets a timeout may ever auto-resolve.

WHY THIS IS AN ALLOWLIST. `_normalize_awaiting` defaults `kind` to "input", and
its own docstring notes that a legacy `{question, since}` record reads as `input`
too. `input` is an EXPIRING kind. So a denylist of permanent kinds would have
made every unlabelled block and every pre-existing block in the live stores
silently auto-resolvable on the day expiry shipped.

`kind` is an open set by design, so the DEFAULT has to be the safe side of the
line. Anything not named in `EXPIRING_AWAITING_KINDS` waits for a human forever.
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


def _blocked(root: Path, **awaiting) -> dict:
    awaiting.setdefault("ask", "Which store should this write to?")
    t = tasks.add(root, title="A ticket that stopped to ask",
                  body="Objective: something.")
    return tasks.update(root, t["id"], awaiting=awaiting)


def test_no_block_never_expires(root: Path) -> None:
    t = tasks.add(root, title="Unblocked", body="Objective: something.")
    assert tasks.awaiting_may_expire(t) is False


@pytest.mark.parametrize("kind", sorted(tasks.EXPIRING_AWAITING_KINDS))
def test_expiring_kinds_may_expire(root: Path, kind: str) -> None:
    assert tasks.awaiting_may_expire(_blocked(root, kind=kind)) is True


@pytest.mark.parametrize("kind", ["permission", "review", "hard-gate", "escalation"])
def test_other_kinds_never_expire(root: Path, kind: str) -> None:
    """Including `permission`, and including any future slug nobody has thought
    of yet. That is what makes the open set safe."""
    assert tasks.awaiting_may_expire(_blocked(root, kind=kind)) is False


def test_an_unlabelled_block_expires_because_it_normalizes_to_input(root: Path) -> None:
    """Pinning the actual behaviour rather than the hoped-for one: a block with
    no `kind` becomes `input`, which IS expiring. This test exists so the
    consequence is visible in the suite instead of being a surprise in
    production — a caller that wants a permanent block must SAY so."""
    rec = _blocked(root)
    assert rec["awaiting"]["kind"] == "input"
    assert tasks.awaiting_may_expire(rec) is True


def test_a_legacy_question_record_reads_as_input(root: Path) -> None:
    t = tasks.add(root, title="Legacy", body="Objective: something.")
    rec = tasks.update(root, t["id"],
                       awaiting={"question": "Old shape?", "since": "2026-01-01"})
    assert rec["awaiting"]["kind"] == "input"


def test_the_set_is_a_frozenset_so_it_cannot_be_widened_at_runtime(root: Path) -> None:
    assert isinstance(tasks.EXPIRING_AWAITING_KINDS, frozenset)
    with pytest.raises(AttributeError):
        tasks.EXPIRING_AWAITING_KINDS.add("permission")  # type: ignore[attr-defined]
