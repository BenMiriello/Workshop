"""An agent can read the whole of what search found, and can find out how big
the corpus is before it searches instead of after it fails.

TWO MEASURED GAPS, ONE SURFACE (finding-and-surfacing-memory-2026-08-23 sec 3.2).

1. Search returns a RANKED SLICE: passages bounded by the chunker, at most a
   couple per document. An agent that found the right paragraph could not get
   the paragraph after it, so it either answered from the slice or went hunting
   the filesystem for a path. `open_ref` is the second step, and it reads by the
   ref search already prints rather than by a second addressing scheme.

2. "Nothing was found" means opposite things over twelve documents and over
   twelve thousand, and nothing in a chat could tell those apart. `where_is`
   now reports how much of the corpus this scope reaches.

And one bound, from a real overflow rather than a worry: an unfiltered
`repeats: true` call rendered 52,565 characters and overran the caller's
tool-result limit outright, losing the whole answer. A render that stops early
and says what it left out is the fix; one that is cut off silently is the
failure, because the reader cannot tell a short list from a clipped one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import workshop_query_server as wqs  # noqa: E402
from services import refs, search_log, semantic_index as si  # noqa: E402
from wsplatform import paths  # noqa: E402


# --------------------------------------------------------------------------- #
# refs.read_ref -- the body, in parts that state what they are
# --------------------------------------------------------------------------- #

@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A workshop root with one doc in it, so `workshop:doc/<path>` resolves."""
    monkeypatch.setattr(paths, "workshop_root", lambda: tmp_path)
    (tmp_path / "docs").mkdir()
    return tmp_path


class TestReadingABodyInParts:
    def test_a_short_doc_comes_back_whole_in_one_part(self, tree):
        (tree / "docs" / "a.md").write_text("# A title\n\nthe body\n")
        got = refs.read_ref("workshop:doc/docs/a.md")
        assert got["title"] == "A title"
        assert got["parts"] == 1
        assert got["offset"] == 0
        assert "the body" in got["text"]
        assert got["total_chars"] == len("# A title\n\nthe body\n")

    def test_a_long_body_is_parted_and_every_part_states_its_range(self, tree):
        body = "# Long\n\n" + ("x" * 5000)
        (tree / "docs" / "long.md").write_text(body)
        first = refs.read_ref("workshop:doc/docs/long.md", part=1, part_chars=1000)
        assert first["parts"] == 6
        assert first["offset"] == 0
        assert len(first["text"]) == 1000
        assert first["total_chars"] == len(body)

        third = refs.read_ref("workshop:doc/docs/long.md", part=3, part_chars=1000)
        assert third["offset"] == 2000
        assert third["text"] == body[2000:3000]

    def test_the_parts_reassemble_into_the_whole_body(self, tree):
        """NOTHING IS LOST BETWEEN PARTS. A reader that follows the part numbers
        to the end has the document, not most of it."""
        body = "# Whole\n\n" + "".join(f"line {i}\n" for i in range(900))
        (tree / "docs" / "whole.md").write_text(body)
        parts = refs.read_ref("workshop:doc/docs/whole.md", part=1,
                              part_chars=700)["parts"]
        joined = "".join(
            refs.read_ref("workshop:doc/docs/whole.md", part=i, part_chars=700)["text"]
            for i in range(1, parts + 1)
        )
        assert joined == body

    def test_a_part_past_the_end_clamps_to_the_last_one(self, tree):
        (tree / "docs" / "a.md").write_text("# A\n\nshort\n")
        got = refs.read_ref("workshop:doc/docs/a.md", part=99)
        assert got["part"] == 1 and got["parts"] == 1

    def test_a_doc_that_declares_itself_superseded_says_so(self, tree):
        """The index computes supersession, not the file's own frontmatter, so
        a reader opening a retired design IN FULL gets the label a reader
        skimming one passage of it already got."""
        marker = next(iter(_superseded_samples()), None)
        if marker is None:
            pytest.skip("no supersession marker to build a sample from")
        (tree / "docs" / "old.md").write_text(marker)
        assert refs.read_ref("workshop:doc/docs/old.md")["status"] == "superseded"


def _superseded_samples() -> list[str]:
    """Bodies the index's own `doc_status` calls superseded. Derived from that
    function rather than guessed, so this test cannot drift from it."""
    candidates = [
        "# Old plan\n\n**Status:** SUPERSEDED by the newer plan.\n",
        "# Old plan\n\n> **SUPERSEDED.** This design has been replaced.\n",
        "# Old plan\n\n**Status:** HISTORICAL. Do not build against this.\n",
    ]
    return [c for c in candidates if si.doc_status(c) == "superseded"]


class TestWhatHasNoBodyIsRefused:
    def test_a_kind_with_no_body_is_not_readable(self):
        """`run`, `project`, `agent` and `app` are records, not documents, and
        `file` is an upload that get_file already reads. Refused rather than
        half-answered with a title."""
        for ref in ("workshop:run/abc", "workshop:project/x",
                    "workshop:agent/y", "workshop:app/z", "workshop:file/1"):
            assert refs.read_ref(ref) is None

    def test_the_readable_kinds_are_the_kinds_the_index_holds(self):
        assert set(refs.READABLE_KINDS) == set(si.DEFAULT_KINDS) - {"vault"}

    def test_a_ref_that_does_not_parse_is_refused(self):
        assert refs.read_ref("not a ref") is None
        assert refs.read_ref("") is None

    def test_a_doc_that_is_gone_is_refused(self, tree):
        assert refs.read_ref("workshop:doc/docs/missing.md") is None

    def test_a_path_escaping_the_repo_is_refused(self, tree):
        assert refs.read_ref("workshop:doc/../../etc/passwd") is None


# --------------------------------------------------------------------------- #
# The boundary: open_ref reaches exactly what search reaches
# --------------------------------------------------------------------------- #

class TestItCannotWalkAroundTheBoundarySearchRespected:
    """A tool that returns a document IN FULL is a way around the scope filter
    if it answers for a document the searcher was never shown. The decision is
    `_keep_mask`'s own rule, made by the same function."""

    def test_a_scope_outside_the_reach_set_is_not_readable(self):
        assert si.reachable_item("note", "development", "workshop:note/development/x",
                                 scopes={"workshop"}, from_scope="workshop") is False

    def test_a_scope_inside_the_reach_set_is_readable(self):
        assert si.reachable_item("note", "workshop", "workshop:note/workshop/x",
                                 scopes={"workshop"}, from_scope="workshop") is True

    def test_a_placeless_item_is_reachable_from_anywhere(self):
        """An unscoped doc sits in no scope, so the tree has nothing to say
        about it. Same rule `_keep_mask` applies to an unscoped chunk."""
        assert si.reachable_item("doc", "", "workshop:doc/docs/a.md",
                                 scopes={"project:x"}, from_scope="project:x") is True

    def test_no_reach_filter_means_no_filter(self):
        assert si.reachable_item("note", "development", None,
                                 scopes=None, from_scope=None) is True

    def test_it_agrees_with_the_mask_row_by_row(self):
        """THE ACTUAL GUARANTEE, checked against `_keep_mask` itself rather than
        against a restatement of it. A future edit to one that is not made to
        the other fails here."""
        meta = [
            {"kind": "note", "scope": "workshop", "ref": "shared/knowledge/a.md"},
            {"kind": "note", "scope": "development", "ref": "shared/knowledge/development/b.md"},
            {"kind": "doc", "scope": "", "ref": "docs/c.md"},
        ]
        scopes, from_scope = {"workshop"}, "workshop"
        mask = si._keep_mask(meta, None, scopes, from_scope)
        for i, m in enumerate(meta):
            resolved = si.resolve_chunk_scope(m["kind"], m["scope"])
            item_ref = si._item_ref(m["kind"], m["ref"], resolved)
            assert bool(mask[i]) == si.reachable_item(
                m["kind"], resolved, item_ref, scopes=scopes, from_scope=from_scope)

    def test_the_tool_refuses_out_of_reach_in_plain_words(self, monkeypatch):
        """Not an error and not an empty result: a refusal that says which
        boundary it is, so the model does not retry the same call."""
        monkeypatch.setenv("WORKSHOP_QUERY_SCOPE", "workshop")
        monkeypatch.setattr(refs, "read_ref", lambda *a, **k: {
            "kind": "note", "title": "T", "href": "", "status": "", "ts": "",
            "scope": "development", "item_ref": "workshop:note/development/x",
            "text": "secret", "offset": 0, "part": 1, "parts": 1,
            "total_chars": 6, "key": "development/x"})
        out = wqs._run_open_ref({"ref": "workshop:note/development/x"})
        assert "secret" not in out
        assert "outside what this chat's scope reaches" in out


# --------------------------------------------------------------------------- #
# A hit and the thing that reads it speak the same vocabulary
# --------------------------------------------------------------------------- #

class TestAHitCanBeOpened:
    """THE JOIN, and it was missing. A search hit used to print the COLLECTOR's
    raw locator: a repo-relative path for a note or a doc, a bare thread id
    behind the word "chat", a bare id for a ticket. None of those is an address
    this system reads, and for a note the two forms do not even share a prefix
    (`shared/knowledge/development/x.md` against `note/development/x`), so a
    caller holding a hit could not derive the ref and `open_ref` could not
    consume what `search` produced. The raw `ref` is unchanged (the per-source
    cap keys off it); `item_ref` is the same chunk in the one ref vocabulary."""

    def test_every_kind_gets_an_addressable_ref(self):
        assert si._item_ref("doc", "docs/a.md", "") == "workshop:doc/docs/a.md"
        assert si._item_ref("note", "shared/knowledge/development/x.md",
                            "development") == "workshop:note/development/x"
        assert si._item_ref("ticket", "abc123", "workshop") == "workshop:ticket/abc123"

    def test_the_addressable_ref_is_one_open_ref_accepts(self):
        """Not merely well-formed: parseable by the module that reads bodies,
        and of a kind that module will read."""
        for item in ("workshop:doc/docs/a.md", "workshop:note/development/x",
                     "workshop:ticket/abc123", "workshop:chat/deadbeef"):
            parsed = refs.parse_ref(item)
            assert parsed.kind in refs.READABLE_KINDS

    def test_the_raw_locator_is_still_carried(self):
        """`ref` is what the per-source cap and every existing consumer key
        off. Adding an address must not have moved it."""
        import inspect
        src = inspect.getsource(si.search)
        assert '"ref": m.get("ref")' in src

    def test_the_rendered_hit_prints_the_ref_not_the_raw_path(self, monkeypatch):
        monkeypatch.setattr(si, "search", lambda *a, **k: {
            "hits": [{"score": 0.9, "kind": "note",
                      "ref": "shared/knowledge/development/x.md",
                      "item_ref": "workshop:note/development/x",
                      "title": "A note", "scope": "development",
                      "ts": "2026-08-01", "text": "body", "status": ""}],
            "mode": "semantic", "rebuilding": False,
            "searched": 10, "corpus": 100})
        monkeypatch.setattr(search_log, "record", lambda *a, **k: None)
        monkeypatch.setenv("WORKSHOP_QUERY_SCOPE", "development")
        out = wqs._run_search({"query": "x"})
        assert "workshop:note/development/x" in out
        assert "shared/knowledge/development/x.md" not in out


# --------------------------------------------------------------------------- #
# The tool surface
# --------------------------------------------------------------------------- #

class TestTheToolIsActuallyReachable:
    def test_it_is_listed(self):
        assert wqs.OPEN_REF_TOOL_NAME in {t["name"] for t in wqs._tool_list()}

    def test_it_is_dispatchable(self):
        """A tool the server lists and cannot dispatch is worse than an absent
        one. `_BUILTIN_TOOL_NAMES` is what `_handle` gates on."""
        assert wqs.OPEN_REF_TOOL_NAME in wqs._BUILTIN_TOOL_NAMES
        resp = wqs._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": wqs.OPEN_REF_TOOL_NAME,
                                       "arguments": {}}})
        assert resp["result"]["isError"] is False
        assert "a ref is required" in resp["result"]["content"][0]["text"]

    def test_it_is_granted_wherever_search_is(self):
        """An exact-match allowlist means a tool the server exposes and the
        list omits is simply unreachable, silently. Search without it is half a
        capability, so it is granted in the same places."""
        from services import tool_registry
        oid = f"mcp__{wqs.SERVER_NAME}__{wqs.OPEN_REF_TOOL_NAME}"
        assert oid in tool_registry.WORKSHOP_TOOLKIT_IDS
        assert oid in tool_registry.GENERAL_ALLOWLIST_IDS
        assert oid in tool_registry.READ_TOOL_IDS
        assert oid in {t.id for t in tool_registry.PLATFORM_TOOLS}

    def test_search_says_the_whole_thing_is_readable(self):
        """The one sentence the ticket asks for: a caller reading only the
        search description still learns that a hit is not the document."""
        assert wqs.OPEN_REF_TOOL_NAME in wqs.SEARCH_TOOL_DESC

    def test_the_orientation_map_names_it(self):
        from services import orientation
        text = orientation.manifest_text("workshop")
        assert wqs.OPEN_REF_TOOL_NAME in text


# --------------------------------------------------------------------------- #
# How big the corpus is
# --------------------------------------------------------------------------- #

class TestTheCorpusMap:
    def test_it_counts_documents_and_passages_separately(self, monkeypatch):
        """A passage count is a fact about the chunker; a document count is a
        fact about the workshop. The question "did I see 6 of 8 or 6 of 800" is
        about documents."""
        meta = [
            {"kind": "doc", "scope": "", "ref": "docs/a.md", "ts": "2026-01-01"},
            {"kind": "doc", "scope": "", "ref": "docs/a.md", "ts": "2026-01-01"},
            {"kind": "doc", "scope": "", "ref": "docs/b.md", "ts": "2026-02-02"},
        ]
        monkeypatch.setattr(si, "load", lambda: (meta, _EMPTY_VECS))
        m = si.corpus_map()
        assert m["chunks"] == 3
        assert m["documents"] == 2
        assert m["by_kind"]["doc"] == {"passages": 3, "reachable_passages": 3,
                                       "documents": 2, "reachable_documents": 2}
        assert (m["oldest"], m["newest"]) == ("2026-01-01", "2026-02-02")

    def test_reachable_is_counted_against_the_total(self, monkeypatch):
        """Reporting only the total OVERSTATES coverage, which is the defect
        the search coverage line already fixed for hits. The same honesty here."""
        meta = [
            {"kind": "note", "scope": "workshop", "ref": "shared/knowledge/a.md", "ts": ""},
            {"kind": "note", "scope": "development",
             "ref": "shared/knowledge/development/b.md", "ts": ""},
        ]
        monkeypatch.setattr(si, "load", lambda: (meta, _EMPTY_VECS))
        m = si.corpus_map(scopes={"workshop"}, from_scope="workshop")
        assert m["documents"] == 2 and m["reachable_documents"] == 1
        assert m["chunks"] == 2 and m["reachable_chunks"] == 1

    def test_where_is_reports_it(self, monkeypatch):
        meta = [{"kind": "doc", "scope": "", "ref": "docs/a.md", "ts": "2026-01-01"}]
        monkeypatch.setattr(si, "load", lambda: (meta, _EMPTY_VECS))
        monkeypatch.setenv("WORKSHOP_QUERY_SCOPE", "workshop")
        out = wqs._run_where_is()
        assert "WHAT IS IN THE RECORD" in out
        assert "1 of 1 documents" in out

    def test_an_empty_index_says_so_rather_than_reporting_zero(self, monkeypatch):
        """Zero documents and "there is no index" are different answers, and
        only one of them means the workshop has written nothing."""
        monkeypatch.setattr(si, "load", lambda: ([], _EMPTY_VECS))
        monkeypatch.setenv("WORKSHOP_QUERY_SCOPE", "workshop")
        out = wqs._run_where_is()
        assert "Nothing is indexed yet" in out
        assert "says nothing about what has been written" in out


# --------------------------------------------------------------------------- #
# The repeats render fits in one answer, and says when it did not
# --------------------------------------------------------------------------- #

def _fake_repeat(n: int, occurrences: int) -> dict:
    return {
        # Big enough to reproduce the real shape: an unfiltered call rendered
        # 52,565 characters, which is what overran the tool-result limit.
        "text": f"statement {n} " + ("padding " * 250),
        "total": occurrences, "occasions": occurrences, "threads": 2,
        "first_ts": "2026-07-01T00:00:00", "last_ts": "2026-08-01T00:00:00",
        "provenance": [{"ts": "2026-07-01T00:00:00",
                        "ref": f"workshop:chat/thread{i}",
                        "title": f"A chat {i}"} for i in range(occurrences)],
    }


class TestARepeatsAnswerFitsInOneToolResult:
    @pytest.fixture(autouse=True)
    def stub_repetition(self, monkeypatch):
        import types
        mod = types.ModuleType("services.repetition")
        rows = [_fake_repeat(i, 30) for i in range(40)]
        mod.repeats = lambda **kw: {"repeats": rows[:kw.get("limit") or 20],
                                    "repeat_total": len(rows), "considered": 5000,
                                    "since": "", "rebuilding": False}
        mod.echoes = lambda *a, **k: []
        monkeypatch.setitem(sys.modules, "services.repetition", mod)
        monkeypatch.setattr("services.repetition", mod, raising=False)

    def test_the_output_stays_under_the_bound(self, monkeypatch):
        monkeypatch.setattr(search_log, "record", lambda *a, **k: None)
        out = wqs._render_repeats("", "", 20, None, "workshop")
        assert len(out) <= wqs._REPEATS_MAX_CHARS + 2000

    def test_it_says_how_many_it_left_out(self, monkeypatch):
        monkeypatch.setattr(search_log, "record", lambda *a, **k: None)
        out = wqs._render_repeats("", "", 20, None, "workshop")
        assert "not shown" in out
        assert "Narrow with `query`" in out

    def test_the_header_counts_what_is_actually_below_it(self, monkeypatch):
        """A header that promises twenty statements over a body holding three
        is the silent truncation this bound exists to avoid, moved up one line."""
        monkeypatch.setattr(search_log, "record", lambda *a, **k: None)
        out = wqs._render_repeats("", "", 20, None, "workshop")
        shown = out.count("--- said ")
        assert out.splitlines()[0].startswith(f"{shown} of 40 repeated statement(s)")

    def test_occurrences_are_capped_and_the_cap_is_stated(self, monkeypatch):
        monkeypatch.setattr(search_log, "record", lambda *a, **k: None)
        out = wqs._render_repeats("", "", 20, None, "workshop")
        assert "more occurrence(s), not listed" in out


# --------------------------------------------------------------------------- #
# The log: "does anyone use search" becomes a count
# --------------------------------------------------------------------------- #

class TestOneLinePerCall:
    @pytest.fixture
    def logdir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "index_dir", lambda: tmp_path)
        return tmp_path

    def test_a_call_is_appended_as_one_json_line(self, logdir):
        search_log.record("search", query="why", searched=6, corpus=800,
                          top_score=0.42, empty=False, turn="turn-1")
        lines = (logdir / search_log.LOG_NAME).read_text().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["tool"] == "search" and row["query"] == "why"
        assert row["searched"] == 6 and row["corpus"] == 800
        assert row["turn"] == "turn-1"
        assert row["ts"]

    def test_a_field_with_nothing_to_say_is_absent_not_null(self, logdir):
        """A reader counting `empty` should be counting calls that reported it,
        not calls that defaulted to it."""
        search_log.record("open_ref", ref="workshop:doc/a.md", turn=None)
        row = json.loads((logdir / search_log.LOG_NAME).read_text().strip())
        assert "turn" not in row

    def test_it_reads_back_in_order(self, logdir):
        for i in range(3):
            search_log.record("search", query=f"q{i}")
        assert [r["query"] for r in search_log.read()] == ["q0", "q1", "q2"]
        assert [r["query"] for r in search_log.read(limit=2)] == ["q1", "q2"]

    def test_a_half_written_last_line_does_not_break_the_history(self, logdir):
        """What a crash during an append looks like. One unreadable line must
        not make every earlier line unreadable."""
        search_log.record("search", query="good")
        with (logdir / search_log.LOG_NAME).open("a") as fh:
            fh.write('{"tool": "search", "query": "trunc')
        assert [r["query"] for r in search_log.read()] == ["good"]

    def test_a_logging_failure_never_fails_the_call_it_describes(self, monkeypatch):
        monkeypatch.setattr(search_log, "log_path",
                            lambda: (_ for _ in ()).throw(RuntimeError("no disk")))
        search_log.record("search", query="still fine")  # must not raise

    def test_open_ref_records_the_turn_it_was_called_under(self, logdir, monkeypatch, tree):
        """WHY THE TURN ID IS ON THE LINE. A log keyed only by chat cannot pair
        a search with the read that followed it, and "did the agent read the
        whole of what it found" is a question about one turn."""
        (tree / "docs" / "a.md").write_text("# A\n\nbody\n")
        monkeypatch.setenv("WORKSHOP_QUERY_SCOPE", "workshop")
        monkeypatch.setenv("WORKSHOP_QUERY_TURN", "turn-42")
        wqs._run_open_ref({"ref": "workshop:doc/docs/a.md"})
        rows = search_log.read()
        assert rows and rows[-1]["tool"] == wqs.OPEN_REF_TOOL_NAME
        assert rows[-1]["turn"] == "turn-42"


class TestTheTurnIdReachesTheSubprocess:
    def test_it_rides_the_launch_env_when_a_turn_is_pinned(self, monkeypatch):
        from services import app_registry
        app_registry.set_current_query_turn("turn-9")
        try:
            env = app_registry._platform_mcp_servers()["workshop"].get("env") or {}
            assert env.get("WORKSHOP_QUERY_TURN") == "turn-9"
        finally:
            app_registry.set_current_query_turn(None)

    def test_no_turn_omits_the_var_rather_than_guessing(self, monkeypatch):
        from services import app_registry
        app_registry.set_current_query_turn(None)
        env = app_registry._platform_mcp_servers()["workshop"].get("env") or {}
        assert "WORKSHOP_QUERY_TURN" not in env


import numpy as _np  # noqa: E402

_EMPTY_VECS = _np.zeros((0, 0), dtype="float32")
