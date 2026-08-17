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

import json
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


def _isolate(tmp_path: Path, monkeypatch) -> None:
    """Redirect BOTH stores a write touches.

    `_dir()` alone is not enough, and finding that out cost a polluted repo:
    `record()` also calls `_mirror_to_md`, which resolves through
    `_legacy_md()` -> `knowledge._root()` and appends to the TRACKED
    `shared/learnings.md`. The first run of this file put a line reading
    "A global guardrail. [src:test]" into that file and into the live
    `.runtime/learnings/global.jsonl`, where it would have ridden into every
    prompt in the workspace. Both were removed by hand.

    A test that writes a learning must redirect both paths, for the same reason
    a test that authenticates as a real owner must redirect its storage.
    """
    monkeypatch.setattr(learnings, "_dir", lambda: tmp_path)
    monkeypatch.setattr(learnings, "_legacy_md", lambda: tmp_path / "learnings.md")


def test_workshop_scope_learnings_reach_the_prompt(tmp_path, monkeypatch):
    """The regression itself: a preference captured from a general chat renders."""
    _isolate(tmp_path, monkeypatch)
    learnings.record("Never use an em-dash in copy.", scope="workshop", source="test")

    out = chat_context.learnings_context(_Provider("workshop"))
    assert "em-dash" in out, "a workshop-scope learning did not reach the prompt"


def test_global_is_not_injected_twice(tmp_path, monkeypatch):
    """`global` stays excluded from the scoped block: it is already the first one.

    The text is a real guardrail rather than the placeholder "A global
    guardrail." it used to be, because the write path now runs a quality gate
    (2026-08-16): a loop-sourced two-word fragment is refused as not being
    instructional guidance, which is the entire point of the gate.
    """
    _isolate(tmp_path, monkeypatch)
    learnings.record("Always rebuild the bundle before reporting a UI change delivered.",
                     scope=learnings.GLOBAL, source="test")

    out = chat_context.learnings_context(_Provider(learnings.GLOBAL))
    assert out.count("Always rebuild the bundle") == 1


def test_a_standing_rule_outranks_incident_noise_under_the_budget(tmp_path, monkeypatch):
    """The other half of the same failure.

    The `global` block held 795 tokens against a 700 budget, and the entry the
    budget dropped was the LONGEST one, which here meant the most important: the
    Design System rule, absent from every prompt for ~25 days. It lost on three
    terms at once (old, one provenance entry, and never retrieved BECAUSE it was
    never selected), while thirteen mechanical `[ralph]` incident lines rendered
    every turn.

    So authorship is ranked above everything else. This builds the same shape at
    a tight budget: one long authored rule against short mechanical ones.
    """
    _isolate(tmp_path, monkeypatch)
    # Written by hand rather than through `record()`, because the whole point is
    # the entry that is OLD, unreinforced and never retrieved. `record()` stamps
    # everything as new, and a test built with it passes on recency alone,
    # whichever way the ranking is wired. It did, until this comment.
    store = tmp_path / "global.jsonl"
    rows = [
        {
            "id": f"r{i}", "hash": f"r{i}", "scope": "global", "source": "ralph",
            "text": f"Ralph rejected: failure mode {i}.",
            "created_at": "2026-08-12T00:00:00Z",
            "retrieval_count": 9,
            "provenance": [{"source": "ralph", "at": "2026-08-12T00:00:00Z"}] * 5,
        }
        for i in range(8)
    ]
    rows.append({
        "id": "rule", "hash": "rule", "scope": "global", "source": "legacy-md",
        "text": "DO NOT write UI without following the Design System. " + ("Detail. " * 40),
        "created_at": "2026-07-01T00:00:00Z",   # oldest
        "provenance": [{"source": "legacy-md", "at": "2026-07-01T00:00:00Z"}],
    })
    store.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    block = learnings.memory_block(learnings.GLOBAL, max_tokens=120, track=False)
    assert "following the Design System" in block, (
        "the long authored rule was dropped in favour of short incident lines"
    )


def test_the_operator_block_is_named_for_what_it_holds(tmp_path, monkeypatch):
    """`workshop` holds the operator's own corrections, so it is not labelled
    "AGENT MEMORY". The label is what tells the model how much weight to give
    it against the mechanical worker noise in the other block."""
    _isolate(tmp_path, monkeypatch)
    learnings.record("Prefer rows to cards.", scope="workshop", source="test")

    out = chat_context.learnings_context(_Provider("workshop"))
    assert "OPERATOR PREFERENCES" in out
