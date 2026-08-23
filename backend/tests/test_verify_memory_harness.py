"""scripts/verify_memory.py -- the memory acceptance harness itself.

DEFECT 5, 2026-08-23, and the reason the other four survived. The harness's
check list claimed "search reaches chats, tickets and docs", and its
implementation called `si.search(q, limit=N)` with no `from_scope`.
`semantic_index._keep_mask` skips the entire `share` policy layer when
`from_scope` is absent, so the harness asserted reachability on a code path no
agent takes: it reported PASS while every real (scoped) search in the system
returned zero documents. It was also wired into no gate at all.

Loaded from its file path the same way test_doc_status.py loads its sibling
script.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load():
    spec = importlib.util.spec_from_file_location(
        "verify_memory", _SCRIPTS / "verify_memory.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def harness():
    mod = _load()
    mod._results.clear()
    return mod


def _statuses(mod) -> list[str]:
    return [status for status, _name, _detail in mod._results]


class TestItTestsThePathAgentsTake:
    def test_every_search_check_passes_a_from_scope(self, harness, monkeypatch):
        """The defect, stated directly. A search check that omits `from_scope`
        is not testing what any agent gets."""
        from services import semantic_index as si

        seen: list[dict] = []

        def fake_search(query, **kw):
            seen.append(kw)
            return {"hits": [{"kind": "doc", "ref": "docs/x.md", "score": 0.8},
                             {"kind": "chat", "ref": "t1", "score": 0.8},
                             {"kind": "ticket", "ref": "T-1", "score": 0.8}],
                    "mode": "semantic", "rebuilding": False,
                    "searched": 3, "corpus": 10}

        monkeypatch.setattr(si, "search", fake_search)
        for _name, fn in harness.CHECKS:
            if fn.__name__.startswith("c_search") or "reachable" in fn.__name__:
                harness._try(_name, fn)

        assert seen, "no search check ran"
        scoped = [kw for kw in seen if kw.get("from_scope")]
        assert len(scoped) == len(seen), (
            f"{len(seen) - len(scoped)} of {len(seen)} harness searches ran with no "
            "from_scope, which skips the whole share-policy layer")

    def test_the_doc_check_is_in_the_check_list(self, harness):
        names = [name for name, _fn in harness.CHECKS]
        assert any("doc" in n and "scoped" in n for n in names), names

    def test_the_gated_subset_exists_and_is_a_real_subset(self, harness):
        names = {name for name, _fn in harness.CHECKS}
        assert harness.RETRIEVAL_CHECKS <= names, (
            "RETRIEVAL_CHECKS names a check that is not in CHECKS, so the gate "
            "would silently run fewer checks than it thinks")
        assert harness.RETRIEVAL_CHECKS < names, "the gated subset is the whole list"

    def test_the_doc_reachability_check_is_one_of_the_gated_ones(self, harness):
        """The whole point of wiring this in. If the check that would have
        caught 2026-08-23 is outside the gated subset, the gate is decoration."""
        assert "docs are reachable from a scoped search" in harness.RETRIEVAL_CHECKS


class TestItFailsWhenDocsAreUnreachable:
    def test_a_scoped_search_that_returns_no_doc_is_a_failure(self, harness, monkeypatch):
        """The exact live state of 2026-08-23: documents came back from the
        scopeless path and from nowhere else. The harness has to call that a
        failure, and say which of the two shapes it is."""
        from services import semantic_index as si

        def fake_search(query, **kw):
            if kw.get("from_scope"):
                return {"hits": [{"kind": "chat", "ref": "t1", "score": 0.77}],
                        "mode": "semantic", "rebuilding": False,
                        "searched": 2, "corpus": 6089}
            return {"hits": [{"kind": "doc", "score": 0.787,
                              "ref": "docs/platform-redesign/app-is-the-entity-2026-08-20.md"}],
                    "mode": "semantic", "rebuilding": False,
                    "searched": 6089, "corpus": 6089}

        monkeypatch.setattr(si, "search", fake_search)
        harness.c_docs_are_reachable_from_a_scope("docs reachable")
        assert _statuses(harness) == [harness.FAIL]
        detail = harness._results[0][2]
        assert "erasing" in detail and "app-is-the-entity" in detail

    def test_an_empty_doc_corpus_is_reported_as_a_different_failure(self, harness, monkeypatch):
        """"The policy ate the docs" and "there are no docs in the index" need
        different fixes, so they must not report as the same finding."""
        from services import semantic_index as si

        monkeypatch.setattr(si, "search", lambda query, **kw: {
            "hits": [], "mode": "semantic", "rebuilding": False,
            "searched": 0, "corpus": 0})
        harness.c_docs_are_reachable_from_a_scope("docs reachable")
        assert _statuses(harness) == [harness.FAIL]
        assert "missing from the index" in harness._results[0][2]

    def test_a_doc_from_a_real_scope_passes(self, harness, monkeypatch):
        from services import semantic_index as si

        monkeypatch.setattr(si, "search", lambda query, **kw: {
            "hits": [{"kind": "doc", "ref": "docs/x.md", "score": 0.8}],
            "mode": "semantic", "rebuilding": False, "searched": 10, "corpus": 10})
        harness.c_docs_are_reachable_from_a_scope("docs reachable")
        assert _statuses(harness) == [harness.PASS]


class TestItIsWiredIntoTheGate:
    """A harness nobody runs reports whatever it likes. reviewer.sh ran
    check_one_definition, check_no_app_names and field_occupancy and never ran
    this one."""

    def test_reviewer_runs_it_and_can_fail_on_it(self):
        src = (_SCRIPTS / "reviewer.sh").read_text()
        assert "verify_memory.py" in src, "reviewer.sh never runs the memory harness"
        assert "--retrieval" in src, \
            "the gate must run the retrieval subset, not the whole harness"
        assert 'MEMORY_REACHABLE_CHECK="fail"' in src
        assert "memory_acceptance_failed" in src, \
            "a failing memory harness must add a reason and block completion"
        assert '"memory_reachable"' in src, \
            "the result has to appear in the reviewer's JSON, not just its log"
