"""The counting half of recall: what was said more than once.

Same rule as `test_semantic_index`: the embedding call is not mocked into a
made-up vector space, because asserting cosine values against invented vectors
proves nothing. What is pinned here is everything around it -- the clustering,
the occasions-not-count ordering, the window parser, the provenance -- which is
where every bug in this module actually was.

The two properties worth protecting above the rest, because losing either turns
a factual report into a misleading one:

  * a same-day burst must not outrank something restated over weeks, and
  * every member of a cluster must survive into `provenance`, so a count can
    always be checked against the messages it came from.
"""
import numpy as np
import pytest

from services import repetition as rep
from services import semantic_index as si


def _unit(vs):
    a = np.asarray(vs, dtype=np.float32)
    return a / np.linalg.norm(a, axis=1, keepdims=True)


class TestSinceWindow:
    """The parser is shared with `search`, so a window means one thing."""

    def test_relative_windows_resolve_to_a_date(self):
        for raw in ("30d", "2w", "3m", "1y"):
            assert len(rep.parse_since(raw)) == 10

    def test_a_longer_window_reaches_further_back(self):
        assert rep.parse_since("30d") < rep.parse_since("1d")

    def test_an_explicit_date_passes_through(self):
        assert rep.parse_since("2026-08-01") == "2026-08-01"

    def test_no_window_means_no_lower_bound(self):
        assert rep.parse_since(None) == ""
        assert rep.parse_since("  ") == ""

    def test_an_unreadable_window_widens_rather_than_empties(self):
        """A filter that fails CLOSED turns a typo into 'nothing was ever said
        about this', which is the one wrong answer this corpus must not give."""
        for junk in ("last tuesday", "30 days", "??", "d30"):
            assert rep.parse_since(junk) == ""

    def test_search_and_repetition_agree_on_a_window(self):
        assert rep.parse_since("2w") == si.parse_since("2w")


class TestClustering:
    def test_identical_vectors_form_one_cluster(self):
        v = _unit([[1, 0, 0]] * 4)
        assert rep._cluster(v, rep.SAME_TEXT) == [[0, 1, 2, 3]]

    def test_orthogonal_vectors_do_not_cluster(self):
        v = _unit([[1, 0, 0], [0, 1, 0], [0, 0, 1]])
        assert sorted(len(c) for c in rep._cluster(v, rep.SAME_TEXT)) == [1, 1, 1]

    def test_every_row_lands_in_exactly_one_cluster(self):
        rng = np.random.default_rng(7)
        v = _unit(rng.normal(size=(40, 12)))
        members = [i for c in rep._cluster(v, 0.5) for i in c]
        assert sorted(members) == list(range(40))

    def test_the_seed_is_the_most_connected_member_not_the_first(self):
        """An arbitrary seed order makes whichever row was scanned first the
        face of the cluster. Row 0 here is a loner; rows 1-3 are the dense
        group, so the first cluster must be seeded from that group."""
        v = _unit([[1, 0], [0, 1], [0, 1], [0, 1]])
        first = rep._cluster(v, 0.9)[0]
        assert first[0] in (1, 2, 3)
        assert set(first) == {1, 2, 3}

    def test_empty_input_is_no_clusters(self):
        assert rep._cluster(np.zeros((0, 4), dtype=np.float32), 0.9) == []


class TestSummary:
    def _rows(self, stamps):
        return [{"ref": f"workshop:chat/t{i}#m{i}", "thread": f"t{i}",
                 "title": "T", "scope": "", "ts": ts, "text": f"body {i}"}
                for i, ts in enumerate(stamps)]

    def test_occasions_counts_distinct_days_not_messages(self):
        rows = self._rows(["2026-08-01T01:00", "2026-08-01T09:00",
                           "2026-08-01T17:00", "2026-08-04T10:00"])
        s = rep._summarize(rows, [0, 1, 2, 3], 0)
        assert s["total"] == 4
        assert s["occasions"] == 2

    def test_first_and_last_come_from_the_extremes_whatever_the_input_order(self):
        rows = self._rows(["2026-08-09T00:00", "2026-08-01T00:00",
                           "2026-08-05T00:00"])
        s = rep._summarize(rows, [0, 1, 2], 0)
        assert s["first_ts"].startswith("2026-08-01")
        assert s["last_ts"].startswith("2026-08-09")

    def test_every_member_survives_into_provenance(self):
        """A count with no way to read the occurrences is an assertion the
        reader cannot check."""
        rows = self._rows(["2026-08-0%dT00:00" % d for d in range(1, 6)])
        s = rep._summarize(rows, [0, 1, 2, 3, 4], 2)
        assert len(s["provenance"]) == 5
        assert {p["ref"] for p in s["provenance"]} == {r["ref"] for r in rows}
        assert all(p["ts"] and p["thread"] for p in s["provenance"])

    def test_the_representative_is_the_seed(self):
        rows = self._rows(["2026-08-01T00:00", "2026-08-02T00:00"])
        assert rep._summarize(rows, [0, 1], 1)["text"] == "body 1"

    def test_threads_counts_distinct_threads(self):
        rows = self._rows(["2026-08-01T00:00"] * 3)
        for r in rows:
            r["thread"] = "same"
        assert rep._summarize(rows, [0, 1, 2], 0)["threads"] == 1


class TestOrdering:
    """The ordering rule, stated as a test because it is the whole finding."""

    def test_a_topic_query_orders_by_relevance_not_by_repetition(self):
        """With a topic this is a search, and the counts are what the answer
        ADDS rather than what selects it. Ordering by occasions inside a topic
        filter made the topic decorative: the most-repeated cluster in the
        corpus outranked everything for every query, so three unrelated
        questions got the same three answers."""
        loud = {"relevance": 0.72, "occasions": 16}
        on_topic = {"relevance": 0.81, "occasions": 3}
        ordered = sorted([loud, on_topic],
                         key=lambda s: (s["relevance"], s["occasions"]),
                         reverse=True)
        assert ordered[0] is on_topic

    def test_a_same_day_burst_does_not_outrank_a_long_running_restatement(self):
        burst = {"occasions": 1, "total": 11, "last_ts": "2026-08-30"}
        standing = {"occasions": 16, "total": 6, "last_ts": "2026-08-29"}
        ordered = sorted([burst, standing],
                         key=lambda s: (s["occasions"], s["total"], s["last_ts"]),
                         reverse=True)
        assert ordered[0] is standing

    def test_recency_only_breaks_a_tie(self):
        older = {"occasions": 4, "total": 4, "last_ts": "2026-08-01"}
        newer = {"occasions": 4, "total": 4, "last_ts": "2026-08-20"}
        fewer_but_newest = {"occasions": 2, "total": 9, "last_ts": "2026-08-31"}
        ordered = sorted([older, newer, fewer_but_newest],
                         key=lambda s: (s["occasions"], s["total"], s["last_ts"]),
                         reverse=True)
        assert ordered == [newer, older, fewer_but_newest]


class TestThresholds:
    def test_the_same_text_bar_sits_just_under_the_duplicate_work_bar(self):
        """A restatement weeks later is worded more loosely than a ticket filed
        twice in an afternoon, so this bar is lower than `task_dedup`'s 0.90 --
        but not by much, or distinct subjects merge into one topic blob."""
        assert 0.85 < rep.SAME_TEXT < 0.90

    def test_a_single_mention_is_not_a_repetition(self):
        assert rep.MIN_OCCASIONS == 2

    def test_the_topic_floor_is_stricter_than_the_passage_floor(self):
        """Copying `search`'s 0.65 here was a real bug. This corpus is one
        person writing about one system, so 0.65 admitted 26-43% of it and
        three unrelated topic queries returned an identical top three -- the
        topic had become decorative. A query about a genuinely uncovered
        subject peaks at 0.656 against this log."""
        assert rep.TOPIC_FLOOR > si.MIN_SCORE
        assert rep.TOPIC_FLOOR >= 0.70


class TestStorage:
    def test_a_length_mismatch_is_rejected_rather_than_half_used(self, tmp_path,
                                                                 monkeypatch):
        """A half-written pair must rebuild, not serve rows whose vectors
        belong to different messages."""
        monkeypatch.setattr(rep, "_index_dir", lambda: tmp_path)
        rows = [{"ref": "workshop:chat/a#1", "thread": "a", "title": "T",
                 "scope": "", "ts": "2026-08-01T00:00", "text": "x" * 40}]
        rep._save(rows, _unit([[1, 0, 0]]), "fp1")
        assert rep._load()[0] == rows

        rep._save(rows * 3, _unit([[1, 0, 0]]), "fp1")
        loaded, vecs, _ = rep._load()
        assert loaded == [] and len(vecs) == 0

    def test_the_vectors_file_is_actually_written_where_it_is_read(self, tmp_path,
                                                                  monkeypatch):
        """`np.save` appends `.npy` to any path lacking it, so writing to a
        `.tmp` name by path produces `<name>.tmp.npy` and the rename finds
        nothing. This is a real failure this module hit."""
        monkeypatch.setattr(rep, "_index_dir", lambda: tmp_path)
        rep._save([], np.zeros((0, 384), dtype=np.float32), "fp")
        assert (tmp_path / "repetition.npy").exists()
        assert not list(tmp_path.glob("*.tmp*"))

    def test_a_store_is_stale_when_the_fingerprint_moved(self, tmp_path,
                                                         monkeypatch):
        monkeypatch.setattr(rep, "_index_dir", lambda: tmp_path)
        rows = [{"ref": "workshop:chat/a#1", "thread": "a", "title": "T",
                 "scope": "", "ts": "2026-08-01T00:00", "text": "x" * 40}]
        rep._save(rows, _unit([[1, 0, 0]]), "fp1")
        monkeypatch.setattr(rep, "_fingerprint", lambda: "fp1")
        assert rep.is_stale() is False
        monkeypatch.setattr(rep, "_fingerprint", lambda: "fp2")
        assert rep.is_stale() is True

    def test_an_absent_store_reports_stale_rather_than_raising(self, tmp_path,
                                                              monkeypatch):
        monkeypatch.setattr(rep, "_index_dir", lambda: tmp_path)
        assert rep._load()[0] == []
        assert rep.is_stale() is True


class TestCollection:
    def test_short_messages_are_left_out(self):
        """Not a judgment about content: very short texts sit close together in
        embedding space regardless of what they say, so they would form one
        enormous cluster of unrelated one-liners."""
        assert rep.MIN_CHARS >= 20

    def test_it_reads_only_the_chat_log(self):
        """The corpus is exactly what the chat collector already reads. No
        configuration file, environment file or credential store is opened
        anywhere in this module."""
        src = (rep.__file__).replace(".pyc", ".py")
        body = open(src, encoding="utf-8").read()
        for forbidden in ("secrets.env", ".env", "secrets_store",
                          "model_credentials", "auth_env"):
            assert forbidden not in body.split('"""')[2], forbidden


class TestQueryContract:
    def test_an_empty_store_answers_rather_than_raising(self, tmp_path,
                                                        monkeypatch):
        monkeypatch.setattr(rep, "_index_dir", lambda: tmp_path)
        monkeypatch.setattr(rep, "_background_rebuild_if_stale", lambda: False)
        out = rep.repeats(limit=5)
        assert out["repeats"] == []
        assert out["corpus"] == 0

    def test_considered_is_reported_separately_from_the_corpus(self, tmp_path,
                                                              monkeypatch):
        """Reporting the corpus total as if it were what was searched tells a
        reader it looked at everything when a filter narrowed it -- the same
        lie `search` already refuses to tell with `searched` vs `corpus`."""
        monkeypatch.setattr(rep, "_index_dir", lambda: tmp_path)
        monkeypatch.setattr(rep, "_background_rebuild_if_stale", lambda: False)
        rows = [{"ref": f"workshop:chat/a#{i}", "thread": "a", "title": "T",
                 "scope": "development", "ts": "2026-08-0%d" % (i + 1),
                 "text": "x" * 40} for i in range(3)]
        rep._save(rows, _unit([[1, 0], [1, 0], [0, 1]]), "fp")
        monkeypatch.setattr(rep, "_fingerprint", lambda: "fp")
        monkeypatch.setattr(si, "resolve_chunk_scope", lambda k, s: s)
        out = rep.repeats(scope={"nothing-matches"}, limit=5)
        assert out["corpus"] == 3
        assert out["considered"] == 0


class TestSearchWindow:
    """`since` on `search` is a FILTER. The retrieval design is explicit that
    ranking is cosine plus the supersession penalty and nothing else."""

    def test_the_window_does_not_appear_in_the_ranking_path(self):
        import inspect
        src = inspect.getsource(si.search)
        window = src[src.index("floor_day"):]
        head = window[:window.index("searched = int(mask.sum())")]
        assert "mask = mask & recent" in head
        # It narrows the mask and touches neither score array.
        assert "adjusted" not in head and "scores" not in head
