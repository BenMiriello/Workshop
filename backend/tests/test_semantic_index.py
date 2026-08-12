"""The parts of the semantic index that can be checked without an embedder.

The embedding call itself is a network round trip to Ollama and is not mocked
into a fake vector space here: a test that asserts cosine values against made-up
vectors proves nothing about retrieval. What IS worth pinning is everything
around it, because every bug this module actually hit while being written was in
that surrounding code rather than in the maths.
"""
import json

import numpy as np
import pytest

from services import semantic_index as si


class TestChunking:
    def test_short_text_is_one_chunk(self):
        assert si._split("a short note") == ["a short note"]

    def test_empty_text_yields_nothing(self):
        assert si._split("") == []
        assert si._split("   \n\n  ") == []

    def test_paragraphs_are_packed_up_to_the_cap(self):
        paras = "\n\n".join(["x" * 300] * 6)
        chunks = si._split(paras)
        assert len(chunks) > 1
        assert all(len(c) <= si.CHUNK_CHARS for c in chunks)

    def test_a_paragraph_longer_than_the_cap_is_split_not_dropped(self):
        """The whole text has to survive. An early version silently discarded
        the tail of any paragraph over the cap."""
        big = "y" * (si.CHUNK_CHARS * 3)
        chunks = si._split(big)
        assert len(chunks) >= 3
        assert sum(len(c) for c in chunks) >= len(big)

    def test_it_does_not_lose_content_at_a_boundary(self):
        text = "\n\n".join([f"paragraph {i} " + "z" * 200 for i in range(20)])
        joined = "".join(si._split(text))
        for i in range(20):
            assert f"paragraph {i}" in joined


class TestDigest:
    def test_same_text_same_source_is_stable(self):
        a = si.Chunk("note", "n.md", "T", "workshop", "t", "body")
        b = si.Chunk("note", "n.md", "T", "workshop", "t", "body")
        assert a.digest() == b.digest()

    def test_the_title_and_timestamp_do_not_change_identity(self):
        """Identity is the CONTENT. Re-titling a note must not force a re-embed,
        and a file whose mtime moved without an edit must not either."""
        a = si.Chunk("note", "n.md", "Old title", "workshop", "2026-01-01", "body")
        b = si.Chunk("note", "n.md", "New title", "workshop", "2026-08-12", "body")
        assert a.digest() == b.digest()

    def test_the_same_sentence_in_two_files_is_two_chunks(self):
        a = si.Chunk("note", "one.md", "T", "", "t", "identical body")
        b = si.Chunk("note", "two.md", "T", "", "t", "identical body")
        assert a.digest() != b.digest()

    def test_a_changed_body_changes_identity(self):
        a = si.Chunk("note", "n.md", "T", "", "t", "body")
        b = si.Chunk("note", "n.md", "T", "", "t", "body edited")
        assert a.digest() != b.digest()


class TestTheIndexRoundTrips:
    """The bug that actually happened, and would silently empty the index.

    `_save` writes JSONL and `load` read it back with `str.splitlines()`. Python
    splits on U+2028, U+2029, U+0085, \\v, \\f and \\x1c-\\x1e as well as \\n, and
    JSON escapes none of them. One chunk carrying a U+2028 cut a valid record in
    half, `json.loads` raised, and the whole index reported as unreadable and
    returned zero results. It cost a full rebuild to notice.
    """
    SEPARATORS = [" ", " ", "", "\v", "\f", "\x1c", "\x1e"]

    @pytest.fixture(autouse=True)
    def _tmp_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "index_dir", lambda: tmp_path)

    def test_a_plain_round_trip(self):
        meta = [{"kind": "note", "ref": "a.md", "text": "hello", "digest": "d1"}]
        si._save(meta, np.ones((1, 4), dtype=np.float32))
        back, vecs = si.load()
        assert back == meta
        assert vecs.shape == (1, 4)

    @pytest.mark.parametrize("sep", SEPARATORS)
    def test_a_line_separator_inside_a_chunk_survives(self, sep):
        meta = [{"kind": "note", "ref": "a.md", "text": f"before{sep}after", "digest": "d1"},
                {"kind": "note", "ref": "b.md", "text": "second row", "digest": "d2"}]
        si._save(meta, np.ones((2, 4), dtype=np.float32))
        back, vecs = si.load()
        assert len(back) == 2, f"{sep!r} broke the JSONL round trip"
        assert back[0]["text"] == f"before{sep}after"
        assert vecs.shape[0] == 2

    def test_a_missing_index_is_empty_not_an_error(self):
        meta, vecs = si.load()
        assert meta == [] and vecs.size == 0

    def test_a_corrupt_index_degrades_to_empty(self, tmp_path):
        (tmp_path / "index.meta.jsonl").write_text("{not json\n")
        np.save(tmp_path / "index.vecs.npy", np.ones((1, 4), dtype=np.float32))
        meta, _ = si.load()
        assert meta == []

    def test_disagreeing_row_counts_degrade_to_empty(self, tmp_path):
        """A half-written index must not be searched. The rows are positional,
        so a mismatch means every result would carry the wrong metadata."""
        (tmp_path / "index.meta.jsonl").write_text(json.dumps({"a": 1}) + "\n")
        np.save(tmp_path / "index.vecs.npy", np.ones((5, 4), dtype=np.float32))
        meta, _ = si.load()
        assert meta == []


class TestSearchGuards:
    @pytest.fixture(autouse=True)
    def _tmp_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "index_dir", lambda: tmp_path)

    def test_an_empty_query_never_embeds(self, monkeypatch):
        def boom(_):
            raise AssertionError("embed must not be called for an empty query")
        monkeypatch.setattr(si, "embed", boom)
        assert si.search("") == []
        assert si.search("   ") == []

    def test_an_empty_index_never_embeds(self, monkeypatch):
        def boom(_):
            raise AssertionError("embed must not be called against an empty index")
        monkeypatch.setattr(si, "embed", boom)
        assert si.search("a real question") == []

    def test_the_floor_drops_everything_below_it(self, monkeypatch):
        meta = [{"kind": "note", "ref": "a.md", "title": "A", "scope": "", "ts": "",
                 "text": "a", "digest": "d1"}]
        si._save(meta, np.array([[1.0, 0.0]], dtype=np.float32))
        monkeypatch.setattr(si, "embed", lambda t: np.array([[1.0, 0.0]], dtype=np.float32))
        assert len(si.search("q", min_score=0.5)) == 1
        # Same hit, floor above it: nothing rather than a confident-looking miss.
        assert si.search("q", min_score=1.1) == []

    def test_kind_and_scope_filters_apply(self, monkeypatch):
        meta = [
            {"kind": "note", "ref": "a", "title": "A", "scope": "workshop", "ts": "", "text": "a", "digest": "1"},
            {"kind": "vault", "ref": "b", "title": "B", "scope": "", "ts": "", "text": "b", "digest": "2"},
        ]
        si._save(meta, np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32))
        monkeypatch.setattr(si, "embed", lambda t: np.array([[1.0, 0.0]], dtype=np.float32))
        assert {h["kind"] for h in si.search("q", kinds=("note",), min_score=0.5)} == {"note"}
        assert {h["scope"] for h in si.search("q", scope="workshop", min_score=0.5)} == {"workshop"}

    def test_the_vault_is_not_searched_by_default(self):
        """A search about routing code must never surface a personal note."""
        assert "vault" not in si.DEFAULT_KINDS
        assert "vault" in si.KINDS

    def test_a_hit_carries_the_body_and_its_provenance(self, monkeypatch):
        meta = [{"kind": "chat", "ref": "thread-1", "title": "T", "scope": "development",
                 "ts": "2026-08-12T00:00:00+00:00", "text": "the body", "digest": "1"}]
        si._save(meta, np.array([[1.0, 0.0]], dtype=np.float32))
        monkeypatch.setattr(si, "embed", lambda t: np.array([[1.0, 0.0]], dtype=np.float32))
        hit = si.search("q", min_score=0.5)[0]
        # Being unable to read what was found is the defect this replaces.
        assert hit["text"] == "the body"
        for field in ("kind", "ref", "title", "scope", "ts", "score"):
            assert field in hit


class TestEmbedNormalisation:
    def test_a_zero_vector_does_not_become_nan(self, monkeypatch):
        """A NaN row poisons every later comparison, silently and forever."""
        class FakeResp:
            def raise_for_status(self): pass
            def json(self): return {"embeddings": [[0.0, 0.0, 0.0]]}

        class FakeClient:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **k): return FakeResp()

        import httpx
        monkeypatch.setattr(httpx, "Client", FakeClient)
        out = si.embed(["anything"])
        assert not np.isnan(out).any()
