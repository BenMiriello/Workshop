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


class TestOnDiskFingerprinting:
    """D6/M1 left a gap the marker mechanism cannot close: a doc written
    straight to `docs/*.md` by an editor or an agent's filesystem tools never
    goes through `routes/docs_index.py`, so it never calls `mark_stale`. The
    on-disk fingerprint in `_fs_is_stale` is the second, independent signal
    that catches that drift anyway, with no new write-path call site."""

    @pytest.fixture(autouse=True)
    def _tmp_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "index_dir", lambda: tmp_path)
        root = tmp_path / "fakeroot"
        (root / "docs").mkdir(parents=True)
        monkeypatch.setattr(si.paths, "workshop_root", lambda: root)
        monkeypatch.setattr(si, "available", lambda: (True, ""))
        monkeypatch.setattr(
            si, "embed",
            lambda texts: np.ones((len(texts), 4), dtype=np.float32))
        monkeypatch.setattr(si, "_COLLECTORS", {
            "note": lambda: [], "chat": lambda: [], "ticket": lambda: [],
            "doc": si.collect_docs,
        })
        self.docs_dir = root / "docs"

    def _write(self, name: str, body: str) -> None:
        (self.docs_dir / name).write_text(body)

    def test_a_direct_filesystem_doc_edit_flips_staleness(self):
        """The bug, reproduced: a doc changes on disk with no call anywhere
        near `mark_stale`, and `is_stale()` still catches it."""
        self._write("a.md", "# A\n\noriginal body")
        res = si.build(si.DEFAULT_KINDS)
        assert res["ok"] is True
        assert si.is_stale() is False

        self._write("a.md", "# A\n\nedited body")  # a plain Path.write_text,
        # exactly what an editor or an agent's Edit/Write tool does -- no
        # mark_stale call anywhere in this line.
        assert si.is_stale() is True

    def test_adding_a_doc_file_flips_staleness(self):
        self._write("a.md", "# A\n\nbody")
        si.build(si.DEFAULT_KINDS)
        assert si.is_stale() is False
        self._write("b.md", "# B\n\na second doc that did not exist before")
        assert si.is_stale() is True

    def test_removing_a_doc_file_flips_staleness(self):
        self._write("a.md", "# A\n\nbody")
        si.build(si.DEFAULT_KINDS)
        assert si.is_stale() is False
        (self.docs_dir / "a.md").unlink()
        assert si.is_stale() is True

    def test_an_unrelated_doc_untouched_is_not_stale(self):
        """Building, then checking again with nothing changed at all, must
        not itself flip staleness -- the fingerprint has to be stable."""
        self._write("a.md", "# A\n\nbody")
        si.build(si.DEFAULT_KINDS)
        assert si.is_stale() is False
        assert si.is_stale() is False

    def test_a_never_built_index_is_not_marked_stale_by_the_fs_check_alone(self):
        """No baseline yet (nothing has ever been built) means the fs signal
        stays silent -- manufacturing staleness with nothing to diff against
        would just be noise. The ordinary first-build path owns that case,
        not this one."""
        self._write("a.md", "# A\n\nbody nobody has indexed yet")
        assert si.is_stale() is False

    def test_the_explicit_marker_still_forces_a_rebuild_on_its_own(self):
        """The pre-existing note/chat/ticket-style path is untouched: a bare
        `mark_stale` call, no filesystem change at all, still flips
        `is_stale()` and `rebuild_if_stale()` still clears it."""
        self._write("a.md", "# A\n\nbody")
        si.build(si.DEFAULT_KINDS)
        assert si.is_stale() is False

        si.mark_stale("a note landed")
        assert si.is_stale() is True

        res = si.rebuild_if_stale()
        assert res is not None and res["ok"] is True
        assert si.is_stale() is False

    def test_a_partial_build_does_not_refresh_the_doc_baseline(self):
        """`build(("chat",))` never re-ran `collect_docs`, so it must not
        silently accept whatever the doc tree currently looks like as the new
        baseline -- the doc corpus in the saved index did not change."""
        self._write("a.md", "# A\n\nbody")
        si.build(si.DEFAULT_KINDS)
        assert si.is_stale() is False

        si.build(("chat",))  # collects no docs at all
        self._write("a.md", "# A\n\nedited after the partial build")
        assert si.is_stale() is True

    def test_the_fingerprint_is_stat_based_and_never_reads_a_file_body(self):
        """Constructed proof, not a timing guess: the file is statable but its
        permission bits make a body read raise `PermissionError`.
        `_stat_fingerprint` must produce a real hash anyway, which it only can
        if it never opens the file for reading (see its own docstring: a glob
        plus `stat()` per match, O(number of files), no body ever touched)."""
        import os as _os
        f = self.docs_dir / "locked.md"
        f.write_text("body that must never be read")
        _os.chmod(f, 0)
        try:
            with pytest.raises(PermissionError):
                f.read_text()
            fp = si._stat_fingerprint(self.docs_dir, "*.md")
        finally:
            _os.chmod(f, 0o644)
        assert fp, "a fingerprint must still be produced from stat() alone"


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


class TestChatChunkingKeepsTheWholeMessage:
    """The 2026-08-23 defect: `collect_chats` was the only collector that did not
    use `_split`. On a flush it kept `line[:CHUNK_CHARS]` and threw the rest of
    the message away, so 7,094,963 characters of transcript on disk were
    1,357,375 in the index (19.1%), 779 messages were cut, and the longest lost
    572,856 characters. What it ate was the LONG messages -- the designs, the
    decisions and the operator's direction -- while routes/chat.py told the
    model that compacted messages "are still in the transcript, and they are
    searchable".
    """

    @pytest.fixture()
    def fake_log(self, monkeypatch):
        from services import chat_log

        store: dict[str, list[dict]] = {}
        monkeypatch.setattr(chat_log, "list_thread_ids", lambda: list(store))
        monkeypatch.setattr(chat_log, "read_meta",
                            lambda tid: {"title": "T", "context_key": "workshop"})
        monkeypatch.setattr(chat_log, "read_events", lambda tid: store[tid])
        return store

    def test_the_tail_of_a_long_message_survives(self, fake_log):
        """Asserted the way the defect was measured: a probe taken from deep
        inside the message, not from its first chunk."""
        body = " ".join(f"sentence{i} about the storage model" for i in range(2000))
        # A short message first, deliberately: the old code only truncated when
        # the buffer was already non-empty, so a thread that OPENS with a long
        # message kept it whole (as one of the 27 oversized, mostly unembedded
        # chunks). The real transcripts that lost 572,856 characters looked
        # like this -- a question, then an answer.
        fake_log["t1"] = [
            {"kind": "message", "role": "user", "ts": "2026-08-01",
             "content": "what is the storage model"},
            {"kind": "message", "role": "assistant", "ts": "2026-08-01",
             "content": body},
        ]
        text = "\n".join(c.text for c in si.collect_chats())
        assert len(body) > 20 * si.CHUNK_CHARS, "the probe must be past the first chunk"
        for offset in (2000, 20000, len(body) - 200):
            probe = body[offset:offset + 120]
            assert " ".join(probe.split()) in " ".join(text.split()), \
                f"the message is truncated at or before character {offset}"

    def test_no_chunk_exceeds_the_cap(self, fake_log):
        """Bounded, not merely un-truncated. 27 chat chunks in the live index
        were over the cap (the largest 19,870 characters) and the embedding
        model's window is 512 tokens, so everything past their first ~2,000
        characters was semantically dead even though it was stored."""
        fake_log["t1"] = [
            {"kind": "message", "role": "user", "ts": "t", "content": "x" * 50000},
            {"kind": "message", "role": "assistant", "ts": "t", "content": "short reply"},
            {"kind": "message", "role": "user", "ts": "t",
             "content": "\n\n".join(["para " + "y" * 900] * 40)},
        ]
        chunks = si.collect_chats()
        assert chunks
        assert max(len(c.text) for c in chunks) <= si.CHUNK_CHARS

    def test_short_messages_are_still_grouped(self, fake_log):
        """The split must not turn a chat log into one chunk per line: 8,000
        one-line chunks would rank noise above substance."""
        fake_log["t1"] = [{"kind": "message", "role": "user", "ts": "t",
                           "content": f"line {i}"} for i in range(200)]
        chunks = si.collect_chats()
        assert len(chunks) < 20, f"{len(chunks)} chunks for 200 short lines"

    def test_a_long_message_does_not_swallow_the_buffered_short_ones(self, fake_log):
        fake_log["t1"] = [
            {"kind": "message", "role": "user", "ts": "t", "content": "the small question"},
            {"kind": "message", "role": "assistant", "ts": "t", "content": "z" * 5000},
        ]
        text = "\n".join(c.text for c in si.collect_chats())
        assert "the small question" in text


class TestItReportsWhatItActuallySearched:
    """DEFECT 2, 2026-08-23: the MCP tool said "No passages in the workshop are
    relevant to that (6089 indexed)" for searches the share policy had narrowed
    to as few as 2 passages. A tool that overstates its own coverage is how an
    agent concludes there is no prior art when there is."""

    @pytest.fixture(autouse=True)
    def _tmp_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "index_dir", lambda: tmp_path)
        monkeypatch.setattr(si, "_resolve_chunk_scope", lambda kind, raw: raw)

    def _corpus(self, monkeypatch):
        meta = [
            {"kind": "note", "ref": f"n{i}", "title": "N", "scope": s, "ts": "",
             "text": "alpha", "digest": str(i)}
            for i, s in enumerate(("workshop", "project:a", "project:b", "development"))
        ]
        si._save(meta, np.ones((4, 2), dtype=np.float32))
        monkeypatch.setattr(si, "embed",
                            lambda t: np.array([[1.0, 0.0]], dtype=np.float32))

    def test_an_unfiltered_search_reports_the_whole_corpus(self, monkeypatch):
        self._corpus(monkeypatch)
        res = si.search("q", min_score=0.5)
        assert res["searched"] == 4 and res["corpus"] == 4

    def test_a_filtered_search_reports_only_what_was_in_play(self, monkeypatch):
        self._corpus(monkeypatch)
        res = si.search("q", scope={"project:a"}, min_score=0.5)
        assert res["searched"] == 1, "the scope filter left one chunk, not four"
        assert res["corpus"] == 4

    def test_a_search_that_filtered_everything_out_still_says_how_big_it_was(self, monkeypatch):
        self._corpus(monkeypatch)
        res = si.search("q", scope={"project:nothing"}, min_score=0.5)
        assert res["hits"] == []
        assert res["searched"] == 0 and res["corpus"] == 4

    def test_the_counts_are_present_on_every_early_return(self, monkeypatch):
        monkeypatch.setattr(si, "embed",
                            lambda t: np.array([[1.0, 0.0]], dtype=np.float32))
        for res in (si.search(""), si.search("nothing is indexed yet")):
            assert "searched" in res and "corpus" in res, res


class TestTheFloorIsWhereTheMeasurementPutIt:
    """DEFECT 4, 2026-08-23. MIN_SCORE was 0.58, measured on 2026-08-12 against a
    3,407-chunk NOMIC index. Commit 7ce3cae replaced nomic with
    BAAI/bge-small-en-v1.5 on 2026-08-20 and said so in its own message
    ("MIN_SCORE was tuned for nomic and needs re-measuring for bge (noted)").
    It was not re-measured, and at 0.58 fifteen of eighteen nonsense queries
    cleared the floor -- "banana helicopter tuesday" at 0.647 -- so the tool's
    contract that an empty result is an answer was false.

    The 2026-08-23 re-measurement is pinned below AS DATA: top-hit scores from
    the live 12,532-chunk bge index for 30 real queries and 18 nonsense ones.
    The tests then assert the error rates the MIN_SCORE comment claims, so
    moving the floor does not just move a number -- it reports what the move
    costs in recall and buys in silence. A unit test that fabricated vectors
    would prove nothing about a threshold whose entire justification is
    measured cosine values; the live re-probe belongs in
    scripts/verify_memory.py, which has a real index to probe.
    """

    # Full natural-language questions with an answer in the corpus.
    REAL_QUESTIONS = [0.740, 0.766, 0.773, 0.776, 0.777, 0.778, 0.782, 0.783,
                      0.793, 0.796, 0.800, 0.801, 0.802, 0.813, 0.819, 0.820,
                      0.835, 0.858]
    # Real too, and each returned the right passage, but short or oblique:
    # "bwrap", "Plaid", "512 token window", "cursor not-allowed",
    # "why did we stop using nomic" (0.657, and it found the right doc).
    REAL_BUT_TERSE = [0.657, 0.668, 0.696, 0.700, 0.703, 0.713, 0.718, 0.723,
                      0.743, 0.768, 0.783, 0.792]
    # Off-corpus in plain English, plus four degenerate token strings at the top
    # ("xxxx yyyy zzzz wwww vvvv" 0.733, "zqxjv mmmm plrt gggh" 0.731, a pure
    # stopword string 0.705) which no agent generates.
    NONSENSE = [0.733, 0.731, 0.705, 0.683, 0.675, 0.664, 0.658, 0.647, 0.634,
                0.631, 0.615, 0.604, 0.594, 0.586, 0.583, 0.577, 0.576, 0.543]

    @property
    def real(self):
        return self.REAL_QUESTIONS + self.REAL_BUT_TERSE

    def test_it_is_not_still_the_nomic_value(self):
        assert si.MIN_SCORE != 0.58, "0.58 was tuned for nomic, which 7ce3cae removed"

    def test_it_rejects_nothing_that_was_measured_as_real(self):
        """A hit below the floor is INVISIBLE: the agent is told nothing has been
        written about it, which is the exact failure that cost this repo its
        whole document corpus on 2026-08-23. A weak hit above the floor at least
        arrives with its score and its source."""
        lost = [s for s in self.real if s < si.MIN_SCORE]
        assert not lost, (f"floor {si.MIN_SCORE} silently drops {len(lost)} of "
                          f"{len(self.real)} measured real queries: {lost}")

    def test_it_rejects_most_of_the_measured_nonsense(self):
        """THE BANDS OVERLAP -- bge scores a two-word real query about the same
        as a sentence about wombats -- so there is no value that admits every
        real query and rejects every nonsense one. What the floor must still do
        is cut the bulk of it: 15 of 18 cleared 0.58."""
        admitted = [s for s in self.NONSENSE if s >= si.MIN_SCORE]
        assert len(admitted) <= 7, (
            f"floor {si.MIN_SCORE} admits {len(admitted)} of {len(self.NONSENSE)} "
            "nonsense queries; 7 is what the comment on MIN_SCORE claims")
        assert len(admitted) < 15, "no better than the stale nomic floor"

    def test_the_two_claims_are_consistent_with_each_other(self):
        """If this ever fails, the corpus has moved and the floor needs
        re-measuring rather than nudging."""
        assert min(self.real) >= si.MIN_SCORE > sorted(self.NONSENSE)[len(self.NONSENSE) // 2]


class TestTheSupersededPenaltyIsSizedToThisModel:
    """0.05 was sized against nomic's 0.09-wide informative band. On bge the
    competitive band is much tighter: the spread between the first and eighth
    hit of a real question measured 0.015 to 0.077, mean 0.038 over eight
    questions on 2026-08-23. A penalty larger than that whole spread stops
    demoting a superseded chunk and starts burying it."""

    MEAN_TOP8_SPREAD = 0.038

    def test_it_is_smaller_than_the_competitive_spread(self):
        assert si._SUPERSEDED_PENALTY < self.MEAN_TOP8_SPREAD

    def test_it_still_breaks_the_tie_it_exists_for(self):
        """Measured 2026-08-23 for "is an app the same thing as a project":
        docs/workshop-v1/00-overview.md (superseded) 0.788, beating
        app-is-the-entity-2026-08-20.md at 0.787 -- the retired model ahead of
        the model of record by 0.001."""
        assert 0.788 - si._SUPERSEDED_PENALTY < 0.787

    def test_it_leaves_a_clearly_better_historical_passage_in_front(self):
        """Sometimes the history IS the answer. For "what did the v1 workshop
        design say about assistants and builders" the same superseded doc scored
        0.777 against a best current chunk of 0.745."""
        assert 0.777 - si._SUPERSEDED_PENALTY > 0.745

    def test_it_never_touches_admission(self, tmp_path, monkeypatch):
        """The penalty applies to ORDER only; the floor test runs on the RAW
        score, so a question whose only answer is historical still gets one.

        Built as a live search rather than read off the source, with a chunk
        whose raw score clears the floor and whose demoted score does not."""
        monkeypatch.setattr(si, "index_dir", lambda: tmp_path)
        monkeypatch.setattr(si, "_resolve_chunk_scope", lambda kind, raw: raw)
        raw = si.MIN_SCORE + si._SUPERSEDED_PENALTY / 2
        assert raw - si._SUPERSEDED_PENALTY < si.MIN_SCORE <= raw, "bad fixture"
        si._save([{"kind": "doc", "ref": "docs/old.md", "title": "Old", "scope": "",
                   "ts": "", "text": "the historical answer", "digest": "1",
                   "status": "superseded"}],
                 np.array([[raw, 0.0]], dtype=np.float32))
        monkeypatch.setattr(si, "embed",
                            lambda t: np.array([[1.0, 0.0]], dtype=np.float32))
        hits = si.search("a question only history answers")["hits"]
        assert len(hits) == 1, "a demoted chunk must still be admitted on its raw score"
        assert hits[0]["status"] == "superseded", \
            "and it must arrive LABELLED, or the reader cannot see the demotion"


class TestCollectNotesWalksSubdirectories:
    """Regression for 2026-08-29: collect_notes used d.glob("*.md") so notes
    in system/ and user/ (added 2026-08-28 commit 1084114e) were invisible."""

    def test_a_note_in_a_subfolder_is_returned(self, tmp_path, monkeypatch):
        scope_dir = tmp_path / "knowledge"
        sub = scope_dir / "system"
        sub.mkdir(parents=True)
        (sub / "my-note.md").write_text("# My Note\n\nThis note lives in system/.\n")

        from services import knowledge
        monkeypatch.setattr(knowledge, "all_scope_dirs", lambda: [("development", scope_dir)])

        chunks = si.collect_notes()
        refs = {c.ref for c in chunks}
        assert any("my-note" in r for r in refs), (
            f"subfolder note not found; refs seen: {sorted(refs)}"
        )

    def test_a_nested_scope_is_indexed_once_under_its_own_scope(self, tmp_path, monkeypatch):
        """Scope directories NEST: shared/knowledge is the workshop scope and
        shared/knowledge/development sits inside it. Making the walk recursive
        made the parent claim the child's notes a second time, under the wrong
        scope -- and scope drives the reach mask, so that is a leak, not just a
        duplicate. Measured 2026-08-29: 658 note rows for 345 distinct chunks."""
        parent = tmp_path / "knowledge"
        child = parent / "development"
        (parent / "user").mkdir(parents=True)
        child.mkdir(parents=True)
        (parent / "user" / "mine.md").write_text("# Mine\n\nA workshop note.\n")
        (child / "system" / "theirs.md").parent.mkdir(parents=True)
        (child / "system" / "theirs.md").write_text("# Theirs\n\nA development note.\n")

        from services import knowledge
        monkeypatch.setattr(knowledge, "all_scope_dirs",
                            lambda: [("workshop", parent), ("development", child)])

        chunks = si.collect_notes()
        by_ref = {}
        for c in chunks:
            by_ref.setdefault(c.ref, []).append(c.scope)

        theirs = [r for r in by_ref if "theirs" in r]
        assert len(theirs) == 1, f"the nested note must appear once, saw {theirs}"
        assert by_ref[theirs[0]] == ["development"], (
            f"and only under its own scope, saw {by_ref[theirs[0]]}"
        )
        mine = [r for r in by_ref if "mine" in r]
        assert len(mine) == 1 and by_ref[mine[0]] == ["workshop"], \
            "the parent's own note still belongs to the parent"
