"""A captured preference has to reach a prompt, or capturing it is a lie.

WHY THIS EXISTS. `learnings_context` used to skip the `workshop` scope on the
reading that a general chat was already covered by the `global` guardrails.
`global` and `workshop` are different stores, and `workshop` is where every
preference captured from a chat lands: a ```learning-note``` fence goes through
`task_scope.apply_learning_note`, which defaults the scope to
`knowledge.scope_for_context("general")`, which is `workshop`.

So the chat replied "Captured a preference" and wrote to a store that nothing
read. Measured 2026-08-12: eleven records, written over three weeks, none ever
retrieved. The worker did not read them either, because `project_ralph.sh` only
ever passes `project:*` or `agent:*` as the learn-scope.

The bug was invisible in every way a bug can be: no error, no empty block, a
success message, and a store on disk filling up with exactly the right content.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import chat_context, learnings  # noqa: E402


class _Provider:
    """The one method `learnings_context` asks a provider for."""

    def __init__(self, scope: str) -> None:
        self._scope = scope

    def knowledge_scope(self) -> str:
        return self._scope


def test_workshop_scope_learnings_reach_the_prompt(tmp_path, monkeypatch):
    """The regression itself: a preference captured from a general chat renders."""
    monkeypatch.setattr(learnings, "_dir", lambda: tmp_path)
    learnings.record("Never use an em-dash in copy.", scope="workshop", source="test")

    out = chat_context.learnings_context(_Provider("workshop"))
    assert "em-dash" in out, "a workshop-scope learning did not reach the prompt"


def test_global_is_not_injected_twice(tmp_path, monkeypatch):
    """`global` stays excluded from the scoped block: it is already the first one."""
    monkeypatch.setattr(learnings, "_dir", lambda: tmp_path)
    learnings.record("A global guardrail.", scope=learnings.GLOBAL, source="test")

    out = chat_context.learnings_context(_Provider(learnings.GLOBAL))
    assert out.count("A global guardrail.") == 1


def test_the_operator_block_is_named_for_what_it_holds(tmp_path, monkeypatch):
    """`workshop` holds the operator's own corrections, so it is not labelled
    "AGENT MEMORY". The label is what tells the model how much weight to give
    it against the mechanical worker noise in the other block."""
    monkeypatch.setattr(learnings, "_dir", lambda: tmp_path)
    learnings.record("Prefer rows to cards.", scope="workshop", source="test")

    out = chat_context.learnings_context(_Provider("workshop"))
    assert "OPERATOR PREFERENCES" in out
