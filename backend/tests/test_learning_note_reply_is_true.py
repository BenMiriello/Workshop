"""The chat's reply about a learning must match what the store actually did.

ADR 0022 made the learnings write path reconcile: a candidate can be refused
(not instructional guidance), can supersede an existing rule, or can be a noop
against one already recorded. Before this, `apply_learning_note` reported
"Captured a preference in learnings" for every one of those outcomes.

That is the same defect the reconciliation exists to fix, moved one layer out.
The store stays clean and the operator is told something untrue about it, which
is strictly worse than the original bug: they now believe a preference is
recorded that is not, and will not restate it.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from services import learnings, task_scope


def _apply(context_key: str, note: str) -> list[str]:
    block = '```learning-note\n{"note": %s}\n```' % _json_str(note)
    fn = task_scope.apply_learning_note
    out = asyncio.run(fn(context_key, block)) if inspect.iscoroutinefunction(fn) else fn(context_key, block)
    return out[1]


def _json_str(s: str) -> str:
    import json
    return json.dumps(s)


@pytest.fixture
def isolated_learnings(tmp_path, monkeypatch):
    """Redirect the learnings store at a temp tree.

    Deliberately patches the module's own path helpers rather than setting
    WORKSHOP_RUNTIME_DIR: that variable is documented in several places and is
    read by nothing (measured 2026-08-16), so a test trusting it would write to
    the operator's real store while appearing isolated. That happened once
    during this work; it is not allowed to happen from a test.
    """
    monkeypatch.setattr(learnings, "_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(learnings, "_path",
                        lambda s, *a, **k: tmp_path / f"{str(s).replace(':', '-')}.jsonl")
    monkeypatch.setattr(learnings, "_archive_path",
                        lambda s, *a, **k: tmp_path / f"{str(s).replace(':', '-')}.archive.jsonl")
    return tmp_path


def test_a_refused_note_is_not_reported_as_captured(isolated_learnings):
    receipts = _apply("workshop", "Ralph rejected: ts_typecheck_fail")
    joined = " ".join(receipts)
    assert "not captured" in joined
    assert "Captured a preference" not in joined


def test_a_refusal_says_why(isolated_learnings):
    receipts = _apply("workshop", "py_typecheck_fail")
    assert "machine status token" in " ".join(receipts)


def test_a_real_guardrail_is_reported_as_captured(isolated_learnings):
    receipts = _apply("workshop",
                      "Never force-push to a shared branch without asking first.")
    assert "Captured a preference" in " ".join(receipts)


def test_restating_a_recorded_preference_says_already_recorded(isolated_learnings):
    text = "Always run the reviewer gate before marking a ticket done."
    _apply("workshop", text)
    receipts = _apply("workshop", text)
    joined = " ".join(receipts)
    assert "already recorded" in joined
    assert "Captured a preference" not in joined


def test_the_receipt_carries_no_em_dash(isolated_learnings):
    """Repo copy rule: no em-dashes in anything the operator reads."""
    for note in ("Ralph rejected: ts_typecheck_fail",
                 "Never force-push to a shared branch without asking first."):
        for r in _apply("workshop", note):
            assert "—" not in r
