"""Search must not rank a conversation ABOUT a procedure above the procedure.

MEASURED, 2026-09-01, on the live 12,912-chunk index. The repository's own
agent-rules file names one document in bold as the only current source for how
to apply a change. Asked the question that document answers:

  "how do I restart the backend"
      -> chat, chat, chat, then that document at rank 4.
  "why is the backend restart deferred"
      -> chat first; the document's best passage was the second-highest raw
         score in the whole corpus and still lost the top slot.

Similarity alone cannot separate those: a conversation about a thing and the
thing itself are about equally similar to a question about it. So the ranking
gained two signals beside the supersession penalty that was already there --
what a source DECLARES itself to be, and what KIND of answer it is -- and one
gate that keeps a bonus from turning into an override.

The four properties pinned here are the ones that make the difference between a
tie-break and a thumb on the scale. Every one of them was a real, measured
failure of an earlier draft of this change, not a hypothetical.
"""
from __future__ import annotations

import numpy as np
import pytest

from services import semantic_index as si


# ------------------------------------------------------- declared authority

def test_the_rules_file_naming_a_doc_makes_it_authoritative(tmp_path, monkeypatch):
    """The declaration already exists in every checkout; this is a second
    reader of it, not a new convention."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "how-to.md").write_text("# How to\n\nDo the thing.\n")
    (tmp_path / "docs" / "other.md").write_text("# Other\n\nSomething else.\n")
    (tmp_path / "AGENTS.md").write_text(
        "# Rules\n\nThe procedure is [`docs/how-to.md`](docs/how-to.md) and "
        "nowhere else.\n")
    monkeypatch.setattr(si.paths, "workshop_root", lambda: tmp_path)
    assert si._authoritative_docs() == {"docs/how-to.md"}


def test_a_bare_filename_resolves_only_when_it_is_unambiguous(tmp_path, monkeypatch):
    """Rules files cite some documents by bare name. Resolving one that matches
    two files would promote the wrong document; declining promotes neither,
    which costs nothing because this is a bonus and not a lookup."""
    for rel in ("docs/a/plan.md", "docs/b/plan.md", "docs/unique.md"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# x\n")
    (tmp_path / "AGENTS.md").write_text("See `plan.md` and `unique.md`.\n")
    monkeypatch.setattr(si.paths, "workshop_root", lambda: tmp_path)
    assert si._authoritative_docs() == {"docs/unique.md"}


def test_TRAP_being_named_never_rescues_a_retired_doc(tmp_path, monkeypatch):
    """A rules file names documents for more than one reason, and one of them is
    to say a set is historical. A doc that declares itself retired stays retired
    however prominently it is cited."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "old.md").write_text(
        "# Old\n\n> **Superseded on 2026-08-20** by `new.md`\n")
    (tmp_path / "AGENTS.md").write_text("Historical: `docs/old.md`. Do not follow it.\n")
    monkeypatch.setattr(si.paths, "workshop_root", lambda: tmp_path)
    statuses = {c.ref: c.status for c in si.collect_docs()}
    assert statuses["docs/old.md"] == "superseded"


# ------------------------------------------------------------- doc_status

def test_a_doc_declaring_itself_current_is_marked():
    assert si.doc_status("**Status:** CURRENT, design only.\n") == "current"


def test_a_doc_declaring_itself_historical_is_retired():
    assert si.doc_status("# Brief\n\n> **STATUS: HISTORICAL.** A stage-setting "
                         "brief.\n") == "superseded"


def test_the_words_for_a_decisions_state_are_not_read_as_currency():
    """"accepted", "built", "shipped" describe a DECISION, not whether the
    document still describes the system. A plan can be fully built and
    completely superseded, so reading them as currency would be a judgment."""
    for word in ("accepted", "built", "shipped", "design", "proposal"):
        assert si.doc_status(f"# X\n\n- Status: {word}\n") == "", word


def test_TRAP_a_document_defining_the_status_vocabulary_is_not_retired():
    """Both index files in this corpus -- the two documents whose whole job is
    to say which other document to read -- were demoted for listing the word.
    A banner CITES its replacement; a definition does not."""
    assert si.doc_status(
        "# Index\n\nStatus values: CURRENT, HISTORICAL, SUPERSEDED BY <path>, "
        "UNKNOWN. A status I judged is marked `(judged)`.\n") == ""
    assert si.doc_status(
        "# Index\n\nStatus vocabulary used below:\n\n"
        "- **CURRENT** accurate, not authoritative\n"
        "- **SUPERSEDED BY x** replaced, kept for the record\n") == ""


def test_a_real_banner_that_cites_its_replacement_still_reads_as_retired():
    """The guard above must not swallow the thing it guards."""
    assert si.doc_status(
        "# Brief\n\n> Superseded for reading purposes by `synthesis.md`.\n") \
        == "superseded"


# ------------------------------------------------------------------ ranking

def _index(monkeypatch, rows):
    """A tiny hand-built index: rows are (kind, ref, status, vector)."""
    meta = [{"kind": k, "ref": r, "title": r, "scope": "", "ts": "",
             "text": f"body of {r}", "status": s} for k, r, s, _ in rows]
    vecs = np.array([v for *_, v in rows], dtype=np.float32)
    monkeypatch.setattr(si, "load", lambda: (meta, vecs))
    monkeypatch.setattr(si, "_background_rebuild_if_stale", lambda: False)
    monkeypatch.setattr(si, "embed", lambda t: np.array([[1.0, 0.0]], dtype=np.float32))


def test_THE_MEASURED_CASE_the_procedure_beats_three_chats_about_it(monkeypatch):
    """The exact shape of the 2026-09-01 measurement: three chat passages at
    0.820/0.812/0.810 above the named source of record at 0.783."""
    _index(monkeypatch, [
        ("chat", "t1", "", [0.820, 0.0]),
        ("chat", "t2", "", [0.812, 0.0]),
        ("chat", "t3", "", [0.810, 0.0]),
        ("doc", "procedure.md", "authoritative", [0.783, 0.0]),
    ])
    hits = si.search("how do I restart the backend", limit=4)["hits"]
    assert hits[0]["ref"] == "procedure.md"
    assert hits[0]["status"] == "authoritative"


def test_the_real_score_is_still_what_is_reported(monkeypatch):
    """RANK on the adjusted score, REPORT the raw one. A reader comparing a
    printed score against the floor has to be looking at the number the floor
    was measured against."""
    _index(monkeypatch, [("doc", "procedure.md", "authoritative", [0.783, 0.0])])
    hits = si.search("q", limit=1)["hits"]
    assert hits[0]["score"] == pytest.approx(0.783, abs=1e-3)


def test_a_chat_still_wins_when_the_answer_only_exists_in_the_transcript(monkeypatch):
    """The kind offset is uniform among chats, so it can only ever act at the
    boundary BETWEEN kinds. When every candidate is a chat it changes nothing,
    which is what keeps "did we decide X" answerable."""
    _index(monkeypatch, [
        ("chat", "t1", "", [0.80, 0.0]),
        ("chat", "t2", "", [0.79, 0.0]),
        ("doc", "unrelated.md", "", [0.70, 0.0]),
    ])
    hits = si.search("did we decide this", limit=3)["hits"]
    assert [h["ref"] for h in hits] == ["t1", "t2", "unrelated.md"]


def test_THE_GATE_authority_cannot_promote_a_doc_that_was_never_in_contention(monkeypatch):
    """The failure of the ungated draft, measured on three live queries: a named
    document with nothing to say about the question beat the document that
    answered it, because the bonus was bigger than the gap. A bonus breaks a
    tie; it does not override relevance."""
    _index(monkeypatch, [
        ("doc", "answers-the-question.md", "", [0.90, 0.0]),
        ("doc", "named-but-irrelevant.md", "authoritative", [0.80, 0.0]),
    ])
    hits = si.search("q", limit=2)["hits"]
    assert [h["ref"] for h in hits] == ["answers-the-question.md",
                                        "named-but-irrelevant.md"]


def test_a_penalty_is_not_gated_the_way_a_bonus_is(monkeypatch):
    """A promoted passage has to have been in contention. A demoted one is
    already losing, so the penalty applies wherever it lands -- including well
    below the top hit, where it keeps a retired doc under a current one."""
    _index(monkeypatch, [
        ("doc", "far-ahead.md", "", [0.95, 0.0]),
        ("doc", "old.md", "superseded", [0.71, 0.0]),
        ("doc", "current.md", "", [0.70, 0.0]),
    ])
    hits = si.search("q", limit=3)["hits"]
    assert [h["ref"] for h in hits] == ["far-ahead.md", "current.md", "old.md"]


def test_no_adjustment_ever_changes_what_is_ADMITTED(monkeypatch):
    """The safety property of the whole section. Admission tests the raw cosine,
    so nothing here can make a passage invisible -- only reorder it."""
    _index(monkeypatch, [
        ("doc", "just-under.md", "authoritative", [si.MIN_SCORE - 0.01, 0.0]),
        ("chat", "just-over.md", "", [si.MIN_SCORE + 0.01, 0.0]),
    ])
    hits = si.search("q", limit=5)["hits"]
    assert [h["ref"] for h in hits] == ["just-over.md"]


def test_the_lexical_fallback_is_not_adjusted(monkeypatch):
    """BM25 scores are unbounded and corpus-relative, so a constant sized in
    cosine units means nothing against them. When the embedder is down the
    ranking stays pure lexical rather than pretending to a precision it does
    not have."""
    _index(monkeypatch, [
        ("chat", "t1", "", [0.0, 0.0]),
        ("doc", "procedure.md", "authoritative", [0.0, 0.0]),
    ])

    def boom(_):
        raise RuntimeError("embedder down")

    monkeypatch.setattr(si, "embed", boom)
    monkeypatch.setattr(si, "_bm25_scores",
                        lambda q, meta: np.array([5.0, 1.0], dtype=np.float32))
    out = si.search("q", limit=2)
    assert out["mode"].startswith("lexical")
    assert [h["ref"] for h in out["hits"]] == ["t1", "procedure.md"]
