"""Search must not teach agents the retired model.

MEASURED, 2026-08-22, before any of this existed. Docs are 66% of the index
(3,834 of 5,773 chunks) and this repo keeps several design generations on disk:

  "is an app the same thing as a project"
      -> docs/workshop-v1/00-overview.md ranked FIRST. Its own banner says it is
         historical and CLAUDE.md says not to follow it. The actual model of
         record, app-is-the-entity-2026-08-20.md, lost by 0.001. Two of the five
         hits were that one historical file.

So search was actively surfacing superseded designs as the answer, which is the
most expensive thing this corpus can do to an agent: it is how work gets built
against a plan that was replaced.

Two corrections are pinned here, plus the two traps that would silently invert
the ranking if the parsing were done loosely.
"""
from __future__ import annotations

import numpy as np
import pytest

from services import semantic_index as si


# --------------------------------------------------------------- doc_status

def test_a_plain_superseded_banner_is_detected():
    assert si.doc_status(
        "# Old plan\n\n> **Superseded on 2026-07-18** by `new.md`\n") == "superseded"


def test_a_status_line_is_detected():
    assert si.doc_status("# X\n\nStatus: **superseded (historical reference)**\n") \
        == "superseded"


def test_a_current_doc_is_not_marked():
    assert si.doc_status("# New plan\n\nStatus: **built**, 2026-08-12.\n") == ""


def test_TRAP_a_doc_that_supersedes_others_is_not_demoted():
    """THE INVERSION. 34 docs in this tree open by saying what they REPLACE
    ("Supersedes the architecture in design.md", "Supersedes nothing"). Those
    are the CURRENT documents. A naive /supersede/ match demotes exactly the
    files a search should surface first."""
    assert si.doc_status(
        "# New model\n\nStatus: built, 2026-08-12. Supersedes the architecture "
        "in `design.md`\n") == ""
    assert si.doc_status("# New\n\nSupersedes nothing; extends the layered prompt.\n") == ""


def test_TRAP_partly_superseded_is_still_current():
    """entity-model-2026-07-18.md carries "PARTLY SUPERSEDED": one SECTION was
    replaced. CLAUDE.md still calls it the authoritative model and says "start
    there". The qualifier and the claim are three lines apart in the real file,
    which is why this is judged per BLOCK and not per line."""
    body = (
        "# Entity model\n\n"
        "> **PARTLY SUPERSEDED, 2026-08-20.** The App-vs-Project split this\n"
        "> document defines (and the paragraph that says to keep it as-is)\n"
        "> is superseded by `app-is-the-entity-2026-08-20.md`.\n"
    )
    assert si.doc_status(body) == ""


def test_TRAP_a_template_field_label_is_not_a_status():
    """docs/decisions/0000-template.md describes the field rather than filling
    it in: "Supersedes / superseded by: (optional, link the other record)"."""
    body = ("# ADR template\n\n- Status: proposed | accepted | superseded\n"
            "- Supersedes / superseded by: (optional, link the other record)\n")
    assert si.doc_status(body) == ""


def test_a_banner_far_down_the_body_does_not_count():
    """A supersession sentence deep in a document is usually ABOUT some other
    document's fate, not a declaration of this one's."""
    body = "# Live doc\n\n" + ("filler\n" * 40) + "\nSuperseded on 2026-01-01 by x\n"
    assert si.doc_status(body) == ""


# ------------------------------------------------------------------ ranking

def _index(monkeypatch, rows):
    """A tiny hand-built index: rows are (ref, status, vector)."""
    meta = [{"kind": "doc", "ref": r, "title": r, "scope": "", "ts": "",
             "text": f"body of {r}", "status": s} for r, s, _ in rows]
    vecs = np.array([v for _, _, v in rows], dtype=np.float32)
    monkeypatch.setattr(si, "load", lambda: (meta, vecs))
    monkeypatch.setattr(si, "_background_rebuild_if_stale", lambda: False)
    monkeypatch.setattr(si, "embed", lambda t: np.array([[1.0, 0.0]], dtype=np.float32))


def test_a_superseded_doc_loses_to_a_current_one_it_narrowly_outscored(monkeypatch):
    """The measured case: 0.788 historical vs 0.787 current. Raw score puts the
    historical doc first; it must not be what the agent reads first."""
    _index(monkeypatch, [
        ("old.md", "superseded", [0.788, 0.0]),
        ("new.md", "", [0.787, 0.0]),
    ])
    hits = si.search("q", limit=2)["hits"]
    assert [h["ref"] for h in hits] == ["new.md", "old.md"]


def test_a_superseded_doc_is_still_returned_and_is_labelled(monkeypatch):
    """DEMOTED, NOT HIDDEN. Sometimes the history IS the question. The floor is
    applied to the real score, so a historical passage that clears it is still
    answered -- carrying its status, so the reader can see what it is."""
    _index(monkeypatch, [("old.md", "superseded", [0.80, 0.0])])
    hits = si.search("q", limit=3)["hits"]
    assert [h["ref"] for h in hits] == ["old.md"]
    assert hits[0]["status"] == "superseded"
    assert hits[0]["score"] == pytest.approx(0.80, abs=1e-3)  # the REAL score


def test_one_document_cannot_take_the_whole_answer(monkeypatch):
    """Adjacent chunks of a file are near-identical in embedding space, so one
    plan took three of five slots. The budget should buy distinct sources."""
    _index(monkeypatch, [
        ("a.md", "", [0.90, 0.0]), ("a.md", "", [0.89, 0.0]),
        ("a.md", "", [0.88, 0.0]), ("a.md", "", [0.87, 0.0]),
        ("b.md", "", [0.86, 0.0]), ("c.md", "", [0.85, 0.0]),
    ])
    refs = [h["ref"] for h in si.search("q", limit=4)["hits"]]
    assert refs.count("a.md") <= si._MAX_PER_REF
    assert "b.md" in refs and "c.md" in refs
