"""What the reply SAYS happened to a learning-note is what happened to it.

Ported from the pre-fold version of this file when `.runtime/learnings` became
standing notes in the one memory store (ticket 81198145). The behaviour is
unchanged and is worth keeping exactly: the write path reconciles and can
DECLINE, and reporting "captured" for a refusal was the failure the
reconciliation exists to stop, moved one layer out. The store stays clean and
the operator is told something untrue about it.
"""
import pytest

from services import knowledge, standing, task_scope


@pytest.fixture
def scope(tmp_path, monkeypatch):
    d = tmp_path / "knowledge"
    d.mkdir()
    monkeypatch.setattr(knowledge, "scope_dir", lambda key, create=False: d)
    return "workshop"


def _apply(reply: str) -> list[str]:
    _cleaned, receipts = task_scope.apply_learning_note("general", reply)
    return receipts


def _fence(note: str) -> str:
    return 'Sure.\n\n```learning-note\n{"note": "%s", "scope": "workshop"}\n```\n' % note


def test_a_refused_note_is_not_reported_as_captured(scope):
    receipts = _apply(_fence("ts_typecheck_fail"))
    assert receipts
    assert "not captured" in receipts[0]
    assert standing.count(scope) == 0


def test_a_refusal_says_why(scope):
    receipts = _apply(_fence("deploy rejected"))
    assert "machine status" in receipts[0]


def test_a_real_guardrail_is_reported_as_captured(scope):
    receipts = _apply(_fence("Always run the typecheck before you claim a change is done."))
    assert "Captured a preference" in receipts[0]
    assert standing.count(scope) == 1


def test_restating_a_recorded_preference_says_already_recorded(scope):
    text = "Always run the typecheck before you claim a change is done."
    _apply(_fence(text))
    receipts = _apply(_fence(text))
    assert "already recorded" in receipts[0]
    assert standing.count(scope) == 1


def test_the_receipt_carries_no_em_dash(scope):
    """frontend/CLAUDE.md rule 11 covers any backend message that reaches the
    screen, and a receipt does."""
    for note in ("ts_typecheck_fail", "Always run the typecheck before you claim it is done."):
        for r in _apply(_fence(note)):
            assert "—" not in r
