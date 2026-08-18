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
        # No note/chat/app registries exist under a bare tmp_path; a scope
        # filter that has to resolve a chat's context_key would otherwise
        # explode on an unrelated missing file. Not what these tests probe.
        monkeypatch.setattr(si, "_resolve_chunk_scope", lambda kind, raw: raw)

    def test_an_empty_query_never_embeds(self, monkeypatch):
        def boom(_):
            raise AssertionError("embed must not be called for an empty query")
        monkeypatch.setattr(si, "embed", boom)
        assert si.search("")["hits"] == []
        assert si.search("   ")["hits"] == []

    def test_an_empty_index_never_embeds(self, monkeypatch):
        def boom(_):
            raise AssertionError("embed must not be called against an empty index")
        monkeypatch.setattr(si, "embed", boom)
        assert si.search("a real question")["hits"] == []

    def test_the_floor_drops_everything_below_it(self, monkeypatch):
        meta = [{"kind": "note", "ref": "a.md", "title": "A", "scope": "", "ts": "",
                 "text": "a", "digest": "d1"}]
        si._save(meta, np.array([[1.0, 0.0]], dtype=np.float32))
        monkeypatch.setattr(si, "embed", lambda t: np.array([[1.0, 0.0]], dtype=np.float32))
        assert len(si.search("q", min_score=0.5)["hits"]) == 1
        # Same hit, floor above it: nothing rather than a confident-looking miss.
        assert si.search("q", min_score=1.1)["hits"] == []

    def test_kind_and_scope_filters_apply(self, monkeypatch):
        meta = [
            {"kind": "note", "ref": "a", "title": "A", "scope": "workshop", "ts": "", "text": "a", "digest": "1"},
            {"kind": "vault", "ref": "b", "title": "B", "scope": "project:other", "ts": "", "text": "b", "digest": "2"},
        ]
        si._save(meta, np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32))
        monkeypatch.setattr(si, "embed", lambda t: np.array([[1.0, 0.0]], dtype=np.float32))
        assert {h["kind"] for h in si.search("q", kinds=("note",), min_score=0.5)["hits"]} == {"note"}
        assert {h["scope"] for h in si.search("q", scope="workshop", min_score=0.5)["hits"]} == {"workshop"}

    def test_a_scope_set_narrows_to_any_member(self, monkeypatch):
        """`search()` takes a SET of allowed scopes (design doc sec C), not just
        one -- this is what lets a caller pass a whole `reach()` result."""
        meta = [
            {"kind": "note", "ref": "a", "title": "A", "scope": "project:birds", "ts": "", "text": "a", "digest": "1"},
            {"kind": "note", "ref": "b", "title": "B", "scope": "project:budget", "ts": "", "text": "b", "digest": "2"},
            {"kind": "note", "ref": "c", "title": "C", "scope": "project:other", "ts": "", "text": "c", "digest": "3"},
        ]
        si._save(meta, np.ones((3, 2), dtype=np.float32))
        monkeypatch.setattr(si, "embed", lambda t: np.array([[1.0, 0.0]], dtype=np.float32))
        hits = si.search("q", scope={"project:birds", "project:budget"}, min_score=0.5)["hits"]
        assert {h["scope"] for h in hits} == {"project:birds", "project:budget"}

    def test_an_unscoped_chunk_is_never_excluded_by_a_scope_filter(self, monkeypatch):
        """Docs carry `scope=""` (they are cross-cutting reference material, not
        placed in the tree). A scope filter must not silently hide every doc."""
        meta = [{"kind": "doc", "ref": "docs/x.md", "title": "X", "scope": "",
                 "ts": "", "text": "x", "digest": "1"}]
        si._save(meta, np.array([[1.0, 0.0]], dtype=np.float32))
        monkeypatch.setattr(si, "embed", lambda t: np.array([[1.0, 0.0]], dtype=np.float32))
        hits = si.search("q", scope={"project:birds"}, min_score=0.5)["hits"]
        assert len(hits) == 1

    def test_the_vault_is_not_searched_by_default(self):
        """A search about routing code must never surface a personal note."""
        assert "vault" not in si.DEFAULT_KINDS
        assert "vault" in si.KINDS

    def test_a_hit_carries_the_body_and_its_provenance(self, monkeypatch):
        meta = [{"kind": "chat", "ref": "thread-1", "title": "T", "scope": "development",
                 "ts": "2026-08-12T00:00:00+00:00", "text": "the body", "digest": "1"}]
        si._save(meta, np.array([[1.0, 0.0]], dtype=np.float32))
        monkeypatch.setattr(si, "embed", lambda t: np.array([[1.0, 0.0]], dtype=np.float32))
        hit = si.search("q", min_score=0.5)["hits"][0]
        # Being unable to read what was found is the defect this replaces.
        assert hit["text"] == "the body"
        for field in ("kind", "ref", "title", "scope", "ts", "score"):
            assert field in hit
        assert si.search("q", min_score=0.5)["mode"] == "semantic"

    def test_masking_matches_the_old_fancy_index_ranking(self, monkeypatch):
        """The scope/kind filter used to be applied by `vecs[keep] @ q`
        (fancy-indexing a submatrix per call, design doc sec 7.2). It is now a
        full-corpus `vecs @ q` masked by score. This pins that the refactor did
        not change WHICH hits win or their ORDER, only how they get there."""
        rng = np.random.default_rng(0)
        n = 40
        vecs = rng.normal(size=(n, 8)).astype(np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        meta = [{"kind": "note" if i % 2 == 0 else "doc", "ref": str(i), "title": str(i),
                 "scope": "workshop" if i % 3 == 0 else "project:x", "ts": "", "text": str(i),
                 "digest": str(i)} for i in range(n)]
        si._save(meta, vecs)
        q = rng.normal(size=8).astype(np.float32)
        q /= np.linalg.norm(q)
        monkeypatch.setattr(si, "embed", lambda t: np.array([q]))

        got = si.search("q", kinds=("note",), scope="workshop", min_score=-1.0,
                         limit=100)["hits"]

        # The old approach, independently: filter first, THEN fancy-index.
        keep = [i for i, m in enumerate(meta)
                if m["kind"] == "note" and m["scope"] == "workshop"]
        sims = vecs[keep] @ q
        order = np.argsort(-sims)
        expected_refs = [meta[keep[int(j)]]["ref"] for j in order]

        assert [h["ref"] for h in got] == expected_refs


class TestLexicalFallback:
    """The point of B (design doc sec 8): a search must still answer when the
    embedder cannot, and say so."""

    @pytest.fixture(autouse=True)
    def _tmp_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "index_dir", lambda: tmp_path)
        monkeypatch.setattr(si, "_resolve_chunk_scope", lambda kind, raw: raw)

    def test_an_embedder_failure_still_returns_hits(self, monkeypatch):
        meta = [
            {"kind": "note", "ref": "a", "title": "GPU notes", "scope": "workshop",
             "ts": "", "text": "the embedder was pinned to CPU to avoid CUDA "
             "out of memory errors", "digest": "1"},
            {"kind": "note", "ref": "b", "title": "Unrelated", "scope": "workshop",
             "ts": "", "text": "a recipe for sourdough starter", "digest": "2"},
        ]
        si._save(meta, np.ones((2, 4), dtype=np.float32))

        def boom(_texts):
            raise RuntimeError("CUDA error: out of memory")
        monkeypatch.setattr(si, "embed", boom)

        result = si.search("CUDA out of memory")
        assert result["hits"], "a search must still answer when the embedder fails"
        assert result["hits"][0]["ref"] == "a"  # the lexically relevant one wins
        assert result["mode"].startswith("lexical")
        assert "embedder unavailable" in result["mode"]
        assert "CUDA error: out of memory" in result["mode"]

    def test_a_healthy_embedder_reports_semantic_mode(self, monkeypatch):
        meta = [{"kind": "note", "ref": "a", "title": "A", "scope": "", "ts": "",
                 "text": "a", "digest": "1"}]
        si._save(meta, np.array([[1.0, 0.0]], dtype=np.float32))
        monkeypatch.setattr(si, "embed", lambda t: np.array([[1.0, 0.0]], dtype=np.float32))
        assert si.search("q", min_score=0.5)["mode"] == "semantic"

    def test_lexical_ranks_by_relevance_not_substring_order(self, monkeypatch):
        """BM25, not `knowledge._plaintext_search`'s raw term count: a query of
        pure stopwords must not just return the longest document."""
        meta = [
            {"kind": "note", "ref": "long", "title": "Long", "scope": "", "ts": "",
             "text": "the a of to and is in " * 40, "digest": "1"},
            {"kind": "note", "ref": "relevant", "title": "Relevant", "scope": "",
             "ts": "", "text": "the scope tree reaches every descendant downward",
             "digest": "2"},
        ]
        si._save(meta, np.ones((2, 4), dtype=np.float32))
        monkeypatch.setattr(si, "embed", lambda t: (_ for _ in ()).throw(RuntimeError("down")))
        hits = si.search("scope tree descendant")["hits"]
        assert hits[0]["ref"] == "relevant"

    def test_lexical_still_respects_kind_and_scope_filters(self, monkeypatch):
        meta = [
            {"kind": "note", "ref": "a", "title": "A", "scope": "project:birds",
             "ts": "", "text": "reach and scope tree", "digest": "1"},
            {"kind": "note", "ref": "b", "title": "B", "scope": "project:other",
             "ts": "", "text": "reach and scope tree", "digest": "2"},
        ]
        si._save(meta, np.ones((2, 4), dtype=np.float32))
        monkeypatch.setattr(si, "embed", lambda t: (_ for _ in ()).throw(RuntimeError("down")))
        hits = si.search("reach scope tree", scope={"project:birds"})["hits"]
        assert {h["ref"] for h in hits} == {"a"}

    def test_no_query_terms_match_means_no_hits(self, monkeypatch):
        meta = [{"kind": "note", "ref": "a", "title": "A", "scope": "", "ts": "",
                 "text": "completely unrelated content", "digest": "1"}]
        si._save(meta, np.ones((1, 4), dtype=np.float32))
        monkeypatch.setattr(si, "embed", lambda t: (_ for _ in ()).throw(RuntimeError("down")))
        assert si.search("zephyr quokka")["hits"] == []


class TestBM25:
    """`_bm25_state` caches postings against the meta FILE's (size, mtime), the
    same pattern `scope_tree._load_overrides` uses, so every test here goes
    through `_save` first to keep the cache key honest -- calling
    `_bm25_scores` with a `meta` list that was never written would key the
    cache off whatever (unrelated) index happens to be on disk."""

    @pytest.fixture(autouse=True)
    def _tmp_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "index_dir", lambda: tmp_path)

    def test_a_document_containing_no_query_terms_scores_zero(self):
        meta = [{"kind": "note", "ref": "a", "text": "nothing in common here", "digest": "1"}]
        si._save(meta, np.ones((1, 4), dtype=np.float32))
        scores = si._bm25_scores("banana helicopter", meta)
        assert scores[0] == 0.0

    def test_more_occurrences_scores_higher(self):
        meta = [
            {"kind": "note", "ref": "a", "text": "scope scope scope scope tree", "digest": "1"},
            {"kind": "note", "ref": "b", "text": "scope tree elsewhere", "digest": "2"},
        ]
        si._save(meta, np.ones((2, 4), dtype=np.float32))
        scores = si._bm25_scores("scope", meta)
        assert scores[0] > scores[1]

    def test_a_rarer_term_across_the_corpus_weighs_more(self):
        meta = [
            {"kind": "note", "ref": "a", "text": "common common rare", "digest": "1"},
            {"kind": "note", "ref": "b", "text": "common common common", "digest": "2"},
            {"kind": "note", "ref": "c", "text": "common common common", "digest": "3"},
        ]
        si._save(meta, np.ones((3, 4), dtype=np.float32))
        common_score = si._bm25_scores("common", meta)[0]
        rare_score = si._bm25_scores("rare", meta)[0]
        assert rare_score > common_score


class TestMarkStaleDebounce:
    """D6/M1: the marker write is on hot paths (every chat message, every
    ticket heartbeat), so a second `mark_stale` while the first mark is still
    unread must cost a `Path.exists()` check, not a second write."""

    @pytest.fixture(autouse=True)
    def _tmp_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "index_dir", lambda: tmp_path)

    def test_a_second_mark_does_not_rewrite_the_file(self, monkeypatch):
        si.mark_stale("first reason")
        first_mtime = si._stale_path().stat().st_mtime_ns
        calls = []
        real_write_text = type(si._stale_path()).write_text

        def spy(self, *a, **k):
            calls.append(a)
            return real_write_text(self, *a, **k)
        monkeypatch.setattr("pathlib.Path.write_text", spy)
        si.mark_stale("second reason")
        assert calls == [], "a second mark while already stale must not write again"
        assert si._stale_path().stat().st_mtime_ns == first_mtime
        # The first cause is what is on disk, not the most recent one.
        assert si._stale_path().read_text().strip() == "first reason"

    def test_marks_again_once_cleared(self):
        si.mark_stale("first")
        si._clear_stale()
        si.mark_stale("second")
        assert si._stale_path().read_text().strip() == "second"

    def test_never_raises_when_the_index_dir_is_unwritable(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr("pathlib.Path.write_text", boom)
        si.mark_stale("anything")  # must not raise


class TestMarkStaleCallSites:
    """D6/M1: the two other durable stores in DEFAULT_KINDS (chat, ticket) plus
    the doc write route must mark the index stale on their own write paths --
    before this, only knowledge.py's two note call sites did, so a chat sent
    today was unfindable until someone ran a manual rebuild."""

    @pytest.fixture(autouse=True)
    def _tmp_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "index_dir", lambda: tmp_path)

    @pytest.fixture()
    def owned(self, tmp_path, monkeypatch):
        from services import ownership
        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
        ownership.set_current_owner(1)
        try:
            yield
        finally:
            ownership.clear_current_owner()

    def test_a_chat_message_marks_the_index_stale(self, owned):
        from services import chat_log, chat_store
        assert not si.is_stale()
        tid = chat_store.create_thread("general")["id"]
        chat_log.append_message_sync(tid, "user", "hello")
        assert si.is_stale()

    def test_an_idempotent_resend_does_not_re_mark(self, owned):
        """A dedup hit returns before reaching the append at all, so it must
        not even redundantly re-mark an already-clear index."""
        from services import chat_log, chat_store
        tid = chat_store.create_thread("general")["id"]
        chat_log.append_message_sync(tid, "user", "hello", client_id="c1")
        si._clear_stale()
        chat_log.append_message_sync(tid, "user", "hello", client_id="c1")
        assert not si.is_stale()

    def test_a_non_message_event_does_not_mark_the_index(self, owned):
        """`run`/`boundary`/`scope`/`title` events are not in the indexed
        corpus (`collect_chats` only reads `kind == "message"`), so marking on
        every event -- several times per turn -- would be pure waste."""
        from services import chat_log, chat_store
        tid = chat_store.create_thread("general")["id"]
        chat_log.append_sync(tid, {"kind": "turn", "turn_id": "r1", "state": "started"})
        assert not si.is_stale()

    def test_a_chat_write_survives_a_broken_mark_stale(self, owned, monkeypatch):
        """A memory-index bookkeeping failure must never break a chat send."""
        from services import chat_log, chat_store
        monkeypatch.setattr(si, "mark_stale",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        tid = chat_store.create_thread("general")["id"]
        ev = chat_log.append_message_sync(tid, "user", "still lands")
        assert ev["content"] == "still lands"
        assert chat_log.read_events(tid)[-1]["content"] == "still lands"

    def test_a_ticket_create_marks_the_index_stale(self, tmp_path):
        from services import tasks
        root = tmp_path / "workshop"
        root.mkdir()
        assert not si.is_stale()
        tasks.add(root, title="A ticket", body="body text")
        assert si.is_stale()

    def test_a_ticket_update_marks_the_index_stale(self, tmp_path):
        from services import tasks
        root = tmp_path / "workshop"
        root.mkdir()
        t = tasks.add(root, title="A ticket", body="body text")
        si._clear_stale()
        tasks.update(root, t["id"], body="edited body")
        assert si.is_stale()

    def test_a_ticket_delete_marks_the_index_stale(self, tmp_path):
        from services import tasks
        root = tmp_path / "workshop"
        root.mkdir()
        t = tasks.add(root, title="A ticket", body="body text")
        si._clear_stale()
        tasks.remove(root, t["id"])
        assert si.is_stale()

    def test_a_ticket_write_survives_a_broken_mark_stale(self, tmp_path, monkeypatch):
        """A memory-index bookkeeping failure must never break a ticket write."""
        from services import tasks
        root = tmp_path / "workshop"
        root.mkdir()
        monkeypatch.setattr(si, "mark_stale",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        t = tasks.add(root, title="Still filed", body="body text")
        assert t["title"] == "Still filed"
        assert tasks.get_task(root, t["id"]) is not None

    def test_a_doc_write_marks_the_index_stale(self, tmp_path, monkeypatch):
        from routes import docs_index
        monkeypatch.setattr(docs_index.paths, "workshop_root", lambda: tmp_path)
        (tmp_path / "docs").mkdir()
        f = tmp_path / "docs" / "x.md"
        f.write_text("# X\n\noriginal")
        assert not si.is_stale()
        docs_index.write_doc(docs_index.PutDocIn(path="docs/x.md", content="# X\n\nedited"))
        assert si.is_stale()


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
