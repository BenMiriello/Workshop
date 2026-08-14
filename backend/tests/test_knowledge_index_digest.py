"""The ambient index (`knowledge.index_block`) is a ranked, token-capped digest,
not a listing of every note (Phase 0 of docs/platform-redesign/
memory-and-documentation-model-2026-08-14.md, sec 5.5). Four properties are
load-bearing and covered here:

  - it truncates at a token budget and DECLARES what it left out, rather than
    presenting a partial list as complete;
  - pinned notes rank first, ahead of recency and type;
  - a note with no `type`/`pinned` metadata at all (every note written before
    this existed) still parses and still renders, defaulting to fact/unpinned;
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
    from services import paths
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


class TestPinnedRanksFirst:
    def test_pinned_beats_more_recent_unpinned(self, tree):
        _put("workshop", "old-pin", "Old pinned note", "body", pinned=True)
        _touch("workshop", "old-pin", time.time() - 1000)
        _put("workshop", "new-plain", "Newer unpinned note", "body")
        _touch("workshop", "new-plain", time.time())

        block = knowledge.index_block("workshop")
        first_line = block.splitlines()[0]
        assert "Old pinned note" in first_line

    def test_recency_breaks_ties_among_unpinned(self, tree):
        _put("workshop", "a", "Note A", "body")
        _touch("workshop", "a", time.time() - 1000)
        _put("workshop", "b", "Note B", "body")
        _touch("workshop", "b", time.time())

        block = knowledge.index_block("workshop")
        titles = [l for l in block.splitlines() if l.startswith("- ")]
        assert titles[0].startswith("- Note B")
        assert titles[1].startswith("- Note A")

    def test_type_breaks_ties_among_unpinned_same_mtime(self, tree):
        now = time.time()
        _put("workshop", "a-fact", "A fact note", "body", note_type="fact")
        _touch("workshop", "a-fact", now)
        _put("workshop", "b-decision", "A decision note", "body",
             note_type="decision")
        _touch("workshop", "b-decision", now)

        block = knowledge.index_block("workshop")
        titles = [l for l in block.splitlines() if l.startswith("- ")]
        assert titles[0].startswith("- A decision note")
        assert titles[1].startswith("- A fact note")


class TestNoMetadataStillParses:
    def test_a_note_written_before_metadata_existed_still_renders(self, tree):
        """No `<!-- author: ... -->` marker at all, the shape of every note on
        disk before this change."""
        d = knowledge.scope_dir("workshop")
        (d / "ancient.md").write_text("# Ancient note\n\nno marker at all\n")
        # It must be in the MEMORY.md index too, same as any note.
        idx = d / "MEMORY.md"
        idx.write_text("# Knowledge index\n\n- [Ancient note](ancient.md)\n")

        block = knowledge.index_block("workshop")
        assert "Ancient note" in block

    def test_it_defaults_to_fact_and_unpinned(self, tree):
        body = "no marker at all"
        assert knowledge.note_type(body) == "fact"
        assert knowledge.note_pinned(body) is False

    def test_an_author_only_marker_still_defaults_type_and_pinned(self, tree):
        body = "<!-- author: operator -->\nsome content"
        author, rest = knowledge.split_author(body)
        assert author == "operator"
        assert rest == "some content"
        assert knowledge.note_type(body) == "fact"
        assert knowledge.note_pinned(body) is False

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

        # total_tokens=1: the narrower layer (project:demo) is allocated
        # first and always shows at least its own first note, which alone
        # exceeds a 1-token budget - guaranteeing nothing is left for the
        # wider workshop layer, deterministically, regardless of exact token
        # arithmetic.
        text, used = knowledge.layered_index_block("project:demo", total_tokens=1)

        assert "workshop" in used, "the starved layer vanished instead of announcing itself"
        workshop_block = text.split("[workshop]\n", 1)[1].split("\n\n[", 1)[0]
        assert workshop_block.strip() == (
            "(20 notes in this scope; use the search tool to reach them)"
        )
        # And it carries no note titles/hooks - a starved layer names a count,
        # never content, or it would blow the budget it was denied.
        assert "- " not in workshop_block
