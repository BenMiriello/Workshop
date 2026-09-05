"""The ambient index (`knowledge.index_block`) is a ranked, token-capped digest,
not a listing of every note (Phase 0 of docs/platform-redesign/
memory-and-documentation-model-2026-08-14.md, sec 5.5). Key properties:

  - it truncates at a token budget and DECLARES what it left out, rather than
    presenting a partial list as complete;
  - most-recently-updated notes rank first;
  - `layered_index_block` spends ONE total budget across every layer it
    renders (narrowest layer first), rather than the per-layer budget being
    multiplied by the number of layers in a turn.
"""
from __future__ import annotations

import asyncio
import os
import re
import time

import pytest

from services import knowledge, ownership


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """An isolated `workshop` knowledge scope under tmp_path."""
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    monkeypatch.setenv("WORKSHOP_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(knowledge, "_root", lambda: tmp_path)
    monkeypatch.setattr(knowledge, "_runtime", lambda: tmp_path / ".runtime")
    shared = tmp_path / "shared" / "knowledge"
    shared.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(knowledge, "_shared_knowledge_root", lambda: shared)
    from wsplatform import paths
    monkeypatch.setattr(paths, "projects_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(paths, "workshop_root", lambda: tmp_path)
    ownership.set_current_owner(4242)
    return tmp_path


def _put(scope, note_id, title, body, **kw):
    asyncio.run(knowledge.put_note(scope, note_id, title, body, **kw))


def _touch(scope_key: str, note_id: str, when: float) -> None:
    """Backdate/forward-date a note's mtime for deterministic ranking tests."""
    f = knowledge.scope_dir(scope_key) / f"{note_id}.md"
    os.utime(f, (when, when))


class TestTruncationDeclaresRemainder:
    def test_it_stops_at_the_token_budget(self, tree):
        for i in range(20):
            _put("workshop", f"note-{i}", f"Note number {i}",
                 f"body of note {i}")
        # A tiny budget so only a couple of ~10-token lines fit.
        block = knowledge.index_block("workshop", max_tokens=20)
        lines = block.splitlines()
        assert len(lines) < 20 + 1, "did not truncate"
        assert lines[-1].startswith("(") and lines[-1].endswith(
            "; use the search tool to reach them)")

    def test_the_remainder_count_matches_what_was_left_out(self, tree):
        for i in range(10):
            _put("workshop", f"note-{i}", f"Note number {i}", "body")
        block = knowledge.index_block("workshop", max_tokens=15)
        shown = sum(1 for l in block.splitlines() if l.startswith("- "))
        m = re.search(r"^\((\d+) more notes", block.splitlines()[-1])
        assert m, "no remainder line"
        assert int(m.group(1)) == 10 - shown

    def test_a_block_that_fits_declares_nothing(self, tree):
        _put("workshop", "solo", "Solo note", "body")
        block = knowledge.index_block("workshop", max_tokens=800)
        assert "more notes in this scope" not in block

    def test_a_single_oversized_note_still_shows_one_line(self, tree):
        """An empty digest is a worse failure than a slightly-over-budget one:
        the loop always shows at least one note even if it alone exceeds the
        budget."""
        _put("workshop", "huge", "A very very very long title that is long",
             "body", hook="x" * 200)
        block = knowledge.index_block("workshop", max_tokens=1)
        assert block.startswith("- A very very very long title")


class TestRecencyRanking:
    def test_more_recent_note_ranks_first(self, tree):
        _put("workshop", "a", "Note A", "body")
        _touch("workshop", "a", time.time() - 1000)
        _put("workshop", "b", "Note B", "body")
        _touch("workshop", "b", time.time())

        block = knowledge.index_block("workshop")
        titles = [l for l in block.splitlines() if l.startswith("- ")]
        assert titles[0].startswith("- Note B")
        assert titles[1].startswith("- Note A")


class TestNoMetadataStillParses:
    def test_a_note_written_before_metadata_existed_still_renders(self, tree):
        """No `<!-- author: ... -->` marker at all, the shape of every note on
        disk before this change."""
        d = knowledge.scope_dir("workshop")
        (d / "ancient.md").write_text("# Ancient note\n\nno marker at all\n")
        # It must be in the scope index too, same as any note.
        idx = d / knowledge.INDEX_FILENAME
        idx.write_text("# Knowledge index\n\n- [Ancient note](ancient.md)\n")

        block = knowledge.index_block("workshop")
        assert "Ancient note" in block

    def test_editing_an_old_note_does_not_invent_metadata(self, tree):
        """Writing a note with no author/type/pinned given must not stamp a
        marker onto a body that never had one - that would make every future
        read of an untouched-by-this-change note byte-different for no
        reason."""
        _put("workshop", "plain", "Plain note", "just words")
        raw = (knowledge.scope_dir("workshop") / "plain.md").read_text()
        assert not raw.startswith("<!--")


class TestLayeredBudgetIsOneTotal:
    """The bug found after the first cut of this digest shipped: `index_block`
    took its own per-layer token budget, and `layered_index_block` called it
    once per layer with no total, so a turn reading three or four layers
    MULTIPLIED the cap instead of being bounded by it. `layered_index_block`
    now owns one total and allocates it narrowest-layer-first."""

    def test_the_total_is_respected_across_three_populated_layers(self, tree):
        long_hook = "a deliberately long hook so each line costs real tokens"
        for i in range(15):
            _put("workshop", f"w-{i}", f"Workshop note number {i}", "body",
                 hook=long_hook)
        for i in range(5):
            _put("role:tester", f"r-{i}", f"Role tester note {i}", "body",
                 hook=long_hook)
        for i in range(5):
            _put("project:demo", f"p-{i}", f"Project demo note {i}", "body",
                 hook=long_hook)

        text, used = knowledge.layered_index_block(
            "project:demo", role="tester", total_tokens=80)
        assert set(used) == {"workshop", "role:tester", "project:demo"}
        total = knowledge._approx_tokens(text)
        # Generous slack for footer/first-note overshoot across layers (each
        # truncating layer can overshoot its exact allowance by at most one
        # footer line plus the one note it always shows), but nowhere near
        # what rendering three uncapped layers would cost.
        assert total <= 80 + 80, (
            f"layered block ({total} tokens) did not respect its 80-token total")

        # It is a real cap, not a no-op: the same layers with a huge budget
        # render far more.
        uncapped, _ = knowledge.layered_index_block(
            "project:demo", role="tester", total_tokens=100_000)
        assert knowledge._approx_tokens(uncapped) > total * 2

    def test_a_starved_layer_still_announces_its_note_count_not_silence(self, tree):
        long_hook = "a deliberately long hook so each line costs real tokens too"
        for i in range(20):
            _put("workshop", f"w-{i}", f"Workshop note number {i}", "body",
                 hook=long_hook)
        for i in range(3):
            _put("project:demo", f"p-{i}", f"Project note {i}", "body",
                 hook=long_hook)

        # total_tokens=1: even workshop's governance floor (40% of 1, rounded
        # down) is 0, so both it and the narrower project:demo layer render as
        # stubs - deterministically, regardless of exact token arithmetic. See
        # TestGovernanceFloor below for the case where the floor is large
        # enough to hold real content.
        text, used = knowledge.layered_index_block("project:demo", total_tokens=1)

        assert "workshop" in used, "the starved layer vanished instead of announcing itself"
        workshop_block = text.split("[workshop]\n", 1)[1].split("\n\n[", 1)[0]
        assert workshop_block.strip() == (
            "(20 notes in this scope; use the search tool to reach them)"
        )
        # And it carries no note titles/hooks - a starved layer names a count,
        # never content, or it would blow the budget it was denied.
        assert "- " not in workshop_block


class TestGovernanceFloor:
    """Defect found by the coordinator: pure narrowest-first allocation
    starves the GOVERNANCE layer (`read_layers`'s first, widest element -
    house style and operator preferences, design doc sec 4.3) whenever a
    narrow scope has more notes than the budget allows, which is backwards -
    governance is the one layer that must always reach the model. It now gets
    a reserved floor, spent before narrower layers compete for the rest."""

    def test_governance_keeps_its_floor_against_a_note_heavy_narrow_layer(self, tree):
        long_hook = "enough hook text on every single line to cost real tokens here"
        for i in range(10):
            _put("workshop", f"w-{i}", f"Workshop preference {i}", "body",
                 hook=long_hook)
        # Far more notes than a 200-token total could ever hold, so pure
        # narrowest-first allocation would consume the whole budget here and
        # leave nothing for workshop.
        for i in range(200):
            _put("project:demo", f"p-{i}", f"Project note {i}", "body",
                 hook=long_hook)

        total = 200
        text, used = knowledge.layered_index_block("project:demo", total_tokens=total)

        assert "workshop" in used
        workshop_block = text.split("[workshop]\n", 1)[1].split("\n\n[", 1)[0]
        # Governance shows REAL content, not the "N notes" stub: the floor
        # exists so it never gets starved down to a bare count by a busy
        # narrow scope.
        assert workshop_block.startswith("- Workshop preference"), (
            f"governance layer was starved instead of using its floor:\n{workshop_block}")

        floor = int(total * knowledge.GOVERNANCE_FLOOR_FRACTION)
        gov_tokens = knowledge._approx_tokens(workshop_block)
        # It used a meaningful fraction of its floor (not just the one note
        # every layer always shows regardless of budget).
        assert gov_tokens >= floor * 0.5, (
            f"governance used only {gov_tokens} tokens of a {floor}-token floor")

        # And the narrow layer still got the rest of the budget, not zero:
        # the floor is a reservation for governance, not a demotion of
        # everyone else to nothing.
        assert "project:demo" in used

    def test_an_unused_floor_returns_to_the_narrower_layers(self, tree):
        """Workshop (governance) has only ONE short note, far under its
        floor. The unspent floor must not be wasted: the narrower layer
        should be able to use it."""
        _put("workshop", "solo", "The only workshop note", "body")
        for i in range(30):
            _put("project:demo", f"p-{i}", f"Project note {i}", "body",
                 hook="a hook long enough to cost real tokens on its own")

        text, used = knowledge.layered_index_block("project:demo", total_tokens=200)
        project_block = text.split("[project:demo]\n", 1)[1]
        project_lines = [l for l in project_block.splitlines() if l.startswith("- ")]
        # More than the ~40 tokens a bare 60%-of-200 remainder would hold if
        # workshop's unused floor were not returned to the pool.
        assert len(project_lines) >= 4


class TestStubNeverCostsMoreThanContent:
    """Defect found by the coordinator: a starved layer with exactly one
    short note was rendering `(1 notes in this scope; ...)`, a sentence
    costing about the same as the note it replaced. A stub must never cost
    more than the content it stands in for."""

    def test_content_wins_when_it_fits_the_stubs_own_cost(self, tree):
        _put("workshop", "solo", "Short note", "body")  # no hook: a short line
        for i in range(20):
            _put("project:demo", f"p-{i}", f"Project note {i}", "body",
                 hook="a deliberately long hook so each line costs real tokens")

        # total_tokens=1 drives workshop's governance floor to 0, the
        # budget<=0 stub path - but its one SHORT note costs less than the
        # stub sentence that would replace it, so the note wins.
        text, used = knowledge.layered_index_block("project:demo", total_tokens=1)
        workshop_block = text.split("[workshop]\n", 1)[1].split("\n\n[", 1)[0]
        assert workshop_block.startswith("- Short note"), (
            f"a stub replaced content that fit within its own cost:\n{workshop_block}")
        assert "notes in this scope" not in workshop_block

    def test_the_stub_still_wins_when_content_would_cost_more(self, tree):
        long_hook = ("a hook long enough that even a single note costs more "
                     "than a short stub sentence would cost on its own")
        for i in range(5):
            _put("workshop", f"w-{i}", f"Workshop note {i}", "body", hook=long_hook)
        for i in range(20):
            _put("project:demo", f"p-{i}", f"Project note {i}", "body", hook=long_hook)

        text, used = knowledge.layered_index_block("project:demo", total_tokens=1)
        workshop_block = text.split("[workshop]\n", 1)[1].split("\n\n[", 1)[0]
        assert "notes in this scope" in workshop_block, (
            f"content that costs more than the stub was shown anyway:\n{workshop_block}")


class TestSingularPlural:
    def test_the_stub_says_one_note_not_one_notes(self, tree):
        long_hook = ("enough hook text that this single note costs noticeably "
                     "more than the short stub sentence naming it would cost")
        _put("workshop", "solo", "A note with a long hook", "body", hook=long_hook)

        text, used = knowledge.layered_index_block("workshop", total_tokens=0)
        assert "(1 note in this scope; use the search tool to reach them)" in text
        assert "1 notes" not in text

    def test_the_remainder_footer_says_one_more_note_not_notes(self, tree):
        _put("workshop", "a", "Note A", "body")
        _put("workshop", "b", "Note B", "body")

        block = knowledge.index_block("workshop", max_tokens=1)
        # `index_block` always shows one note regardless of budget; with
        # exactly 2 notes total, exactly 1 is left over.
        assert block.splitlines()[-1] == (
            "(1 more note in this scope; use the search tool to reach them)"
        )
        assert "1 more notes" not in block
