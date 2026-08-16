"""Filing a ticket must not wait on an embedding or a local model.

Reported twice by the operator: "why does creating tickets take several seconds
of loading or waiting before created". Measured 2026-08-16 on the live store:
`decide` embeds the new text against EVERY open ticket, one HTTP call per text,
17.5s for 28 texts, and then may ask a local model to confirm a merge. All of it
inside the click.

Three rules fix it, and these pin them:

  1. the vector cache, so a ticket's words are embedded once, not on every filing
  2. the interactive tier, so a click answers on the free canonical-hash check
  3. the pass that runs afterwards, which LINKS a near-duplicate rather than
     merging one behind the operator's back
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services import task_dedup, tasks


@pytest.fixture
def no_embedding(monkeypatch):
    """Any embedding call in an interactive path is the bug this file is about."""
    def boom(texts):
        raise AssertionError(
            f"the interactive path embedded {len(texts)} texts; it must not")
    monkeypatch.setattr(task_dedup, "embed_texts", boom)


def test_the_interactive_tier_does_not_embed(no_embedding):
    d = task_dedup.decide("File a ticket from the card",
                          [{"id": "t1", "text": "Something else entirely"}],
                          semantic=False)
    assert d["decision"] == "distinct"
    assert d["stage"] == "hash-only-interactive"


def test_an_exact_duplicate_is_still_caught_without_embedding(no_embedding):
    """The canonical-hash tier is pure computation, so it stays on the click."""
    d = task_dedup.decide("Wire the toolbar to the picker",
                          [{"id": "t1", "text": "wire the toolbar to the picker"}],
                          semantic=False)
    assert d["decision"] == "exact"
    assert d["matched_id"] == "t1"


def test_filing_interactively_does_not_embed(tmp_path, no_embedding):
    out = task_dedup.dedup_add(tmp_path, text="A ticket filed from a button",
                               semantic=False)
    assert out["decision"] == "distinct"
    assert tasks.get_task(tmp_path, out["task"]["id"]) is not None


def test_the_pass_afterwards_links_and_never_merges(tmp_path, monkeypatch):
    """A merge decided after the operator watched his ticket appear would take
    it away again. The link says the same thing about the work and removes
    nothing."""
    first = tasks.add(tmp_path, text="Rewrite the toolbar picker")
    second = task_dedup.dedup_add(
        tmp_path, text="Rework the picker in the toolbar", semantic=False)["task"]
    # Both are near-identical in meaning; force the semantic tier to say so.
    monkeypatch.setattr(task_dedup, "embed_texts",
                        lambda texts: [[1.0, 0.0]] * len(texts))
    monkeypatch.setattr(task_dedup, "same_work_llm",
                        lambda a, b, timeout_s=None: True)

    out = task_dedup.link_near_matches(tmp_path, second["id"])

    assert out["status"] == "linked"
    assert out["decision"] in ("merge", "exact")
    # The filed ticket SURVIVES a decision that would have merged it.
    assert tasks.get_task(tmp_path, second["id"]) is not None
    kinds = {e["kind"] for e in tasks.get_task(tmp_path, second["id"])["evidence"]}
    assert "related-to" in kinds
    back = {e["kind"] for e in tasks.get_task(tmp_path, first["id"])["evidence"]}
    assert "related-from" in back


def test_a_vector_is_embedded_once_and_then_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(task_dedup, "_vec_cache_path",
                        lambda: tmp_path / "vectors.json")
    monkeypatch.setattr(task_dedup, "_vec_cache_mem", None, raising=False)
    calls: list[int] = []

    def counted(texts):
        calls.append(len(texts))
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(task_dedup, "_ollama_embed", counted)
    monkeypatch.setattr(task_dedup, "_http_embed", lambda t, u: None)

    corpus = ["ticket one", "ticket two", "ticket three"]
    task_dedup.embed_texts(corpus)
    task_dedup.embed_texts(corpus)                       # entirely cached
    task_dedup.embed_texts(corpus + ["a brand new one"])  # one new text only

    assert calls == [3, 1], f"expected 3 then 1 embedded, got {calls}"


def test_the_cache_key_includes_the_model(tmp_path, monkeypatch):
    """A cosine between two models' vectors is a number with no meaning, so a
    model change must not be able to serve the old one."""
    monkeypatch.setattr(task_dedup, "_EMBED_MODEL", "model-a")
    a = task_dedup._vec_key("the same words")
    monkeypatch.setattr(task_dedup, "_EMBED_MODEL", "model-b")
    assert task_dedup._vec_key("the same words") != a


def test_warming_is_free_when_everything_is_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(task_dedup, "_vec_cache_path",
                        lambda: tmp_path / "vectors.json")
    monkeypatch.setattr(task_dedup, "_vec_cache_mem", None, raising=False)
    monkeypatch.setattr(task_dedup, "_http_embed", lambda t, u: None)
    monkeypatch.setattr(task_dedup, "_ollama_embed",
                        lambda texts: [[1.0, 0.0] for _ in texts])
    tasks.add(tmp_path, text="An open ticket in this scope")

    first = task_dedup.warm_vectors([Path(tmp_path)])
    second = task_dedup.warm_vectors([Path(tmp_path)])

    assert first["embedded"] == 1
    assert second["embedded"] == 0
