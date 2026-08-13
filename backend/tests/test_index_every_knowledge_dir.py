"""Every knowledge scope is indexed, and a write does not need a scheduler.

TWO BUGS, ONE FILE. `semantic_index.collect_notes` said "every scope directory
that exists" and walked the shared root plus its immediate subdirectories only,
so `projects/*/knowledge`, `apps/*/knowledge`, `.runtime/agents/*/knowledge` and
`roles/<role>/` were never indexed at all. And nothing rebuilt after a write, so
a note written today was unfindable until somebody ran the build by hand.

The rebuild trigger is a marker file plus a check on the next search. There is
deliberately no cron entry: the operator turned cron off, and a scheduler both
rebuilds when nothing changed and lags when something did.
"""
import asyncio

import pytest

from services import knowledge, ownership, semantic_index as si


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A whole workshop under tmp_path, with an owner pinned into it.

    `ownership._RUNTIME` is redirected too. A test that pins a real owner and
    leaves that pointing at the live tree writes into the operator's actual
    index directory.
    """
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    monkeypatch.setenv("WORKSHOP_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(knowledge, "_root", lambda: tmp_path)
    monkeypatch.setattr(knowledge, "_runtime", lambda: tmp_path / ".runtime")
    monkeypatch.setattr(knowledge, "_shared_knowledge_root",
                        lambda: _mk(tmp_path / "shared" / "knowledge"))
    from services import paths
    monkeypatch.setattr(paths, "projects_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(paths, "workshop_root", lambda: tmp_path)
    monkeypatch.setattr(si.paths, "workshop_root", lambda: tmp_path)
    ownership.set_current_owner(4242)
    return tmp_path


def _mk(p):
    p.mkdir(parents=True, exist_ok=True)
    return p


def _note(d, name, body):
    _mk(d)
    (d / f"{name}.md").write_text(body)


class TestEveryScopeIsEnumerated:
    def test_the_four_directories_that_were_never_reached(self, tree):
        _note(tree / "shared" / "knowledge", "a", "# Shared\n\nshared body")
        _note(tree / "shared" / "knowledge" / "development", "b", "# Dev\n\ndev body")
        _note(tree / "shared" / "knowledge" / "roles" / "reviewer", "c", "# Role\n\nrole body")
        _note(tree / "projects" / "birds" / "knowledge", "d", "# Proj\n\nproject body")
        _note(tree / "apps" / "budget" / "knowledge", "e", "# App\n\napp body")
        _note(tree / ".runtime" / "agents" / "scribe" / "knowledge", "f", "# Agent\n\nagent body")

        found = dict(knowledge.all_scope_dirs())
        assert set(found) >= {
            "workshop", "development", "role:reviewer",
            "project:birds", "app:budget", "agent:scribe",
        }

    def test_the_keys_are_real_scope_keys_that_scope_dir_round_trips(self, tree):
        _note(tree / "projects" / "birds" / "knowledge", "d", "body")
        _note(tree / "shared" / "knowledge" / "roles" / "keeper", "c", "body")
        for key, d in knowledge.all_scope_dirs():
            if ":" not in key and key not in ("workshop", "development"):
                continue  # an unkeyed leftover subdirectory, read but not named
            assert knowledge.scope_dir(key).resolve() == d.resolve(), key

    def test_it_creates_nothing(self, tree):
        """`scope_dir` mkdirs what it is asked for, so enumerating by asking it
        about every installed app would CREATE a knowledge directory inside each
        one, in the tracked tree, just by looking."""
        _mk(tree / "apps" / "budget")
        _mk(tree / "projects" / "birds")
        knowledge.all_scope_dirs()
        assert not (tree / "apps" / "budget" / "knowledge").exists()
        assert not (tree / "projects" / "birds" / "knowledge").exists()

    def test_archive_is_excluded(self, tree):
        """`enforce_index_cap` demotes notes there specifically to get them out
        of the always-loaded index AND out of the search corpus."""
        _note(tree / "shared" / "knowledge" / "archive", "old", "archived body")
        assert not [d for k, d in knowledge.all_scope_dirs() if d.name == "archive"]

    def test_a_missing_directory_is_absent_rather_than_an_error(self, tree):
        assert dict(knowledge.all_scope_dirs()).get("project:nope") is None


class TestCollectNotes:
    def test_notes_outside_the_shared_root_are_now_chunked(self, tree):
        _note(tree / "shared" / "knowledge", "a", "# Shared\n\nshared body")
        _note(tree / "projects" / "birds" / "knowledge", "d", "# Proj\n\nproject body")
        _note(tree / "shared" / "knowledge" / "roles" / "reviewer", "c", "# Role\n\nrole body")
        scopes = {c.scope for c in si.collect_notes()}
        assert scopes == {"workshop", "project:birds", "role:reviewer"}

    def test_the_body_is_what_gets_indexed(self, tree):
        _note(tree / "projects" / "birds" / "knowledge", "d",
              "<!-- author: operator -->\n# Proj\n\nthe finding")
        chunk = [c for c in si.collect_notes() if c.scope == "project:birds"][0]
        assert "the finding" in chunk.text
        assert "author:" not in chunk.text
        assert chunk.title == "Proj"


class TestStaleness:
    def test_a_fresh_index_is_not_stale(self, tree):
        assert si.is_stale() is False

    def test_writing_a_note_marks_it_stale(self, tree):
        asyncio.run(knowledge.put_note("workshop", "a-note", "A note", "the body"))
        assert si.is_stale() is True

    def test_deleting_a_note_marks_it_stale(self, tree):
        asyncio.run(knowledge.put_note("workshop", "a-note", "A note", "the body"))
        si._clear_stale()
        asyncio.run(knowledge.delete_note("workshop", "a-note"))
        assert si.is_stale() is True

    def test_deleting_a_note_that_was_never_there_does_not(self, tree):
        asyncio.run(knowledge.delete_note("workshop", "never-existed"))
        assert si.is_stale() is False

    def test_the_marker_is_per_owner(self, tree):
        si.mark_stale("x")
        assert si.is_stale() is True
        ownership.set_current_owner(4243)
        assert si.is_stale() is False

    def test_rebuild_is_a_no_op_when_nothing_changed(self, tree, monkeypatch):
        called = []
        monkeypatch.setattr(si, "build", lambda *a, **k: called.append(a) or {"ok": True})
        assert si.rebuild_if_stale() is None
        assert called == []

    def test_a_stale_index_rebuilds_on_the_next_search(self, tree, monkeypatch):
        si.mark_stale("a note landed")
        called = []

        def fake_build(kinds=si.DEFAULT_KINDS, **kw):
            called.append(tuple(kinds))
            return {"ok": True, "chunks": 0}

        monkeypatch.setattr(si, "build", fake_build)
        si.search("anything")
        assert called, "a stale index was searched without rebuilding"

    def test_the_rebuild_never_narrows_the_corpus_to_the_query(self, tree):
        """THE TRAP. `build` REPLACES the index with what it collected, so
        rebuilding with the caller's `kinds` filter would delete every doc and
        chat from the index as a side effect of one note-only search."""
        assert set(si.rebuild_kinds()) >= set(si.DEFAULT_KINDS)

    def test_an_opted_in_kind_survives_a_rebuild_it_did_not_ask_for(self, tree, monkeypatch):
        monkeypatch.setattr(si, "load", lambda: ([{"kind": "vault"}], None))
        assert "vault" in si.rebuild_kinds()

    def test_a_failed_rebuild_leaves_the_marker_so_the_next_search_retries(
            self, tree, monkeypatch):
        si.mark_stale("a note landed")
        monkeypatch.setattr(si, "available", lambda: (False, "ollama is down"))
        si.rebuild_if_stale()
        assert si.is_stale() is True

    def test_a_raising_rebuild_still_answers_from_what_is_on_disk(self, tree, monkeypatch):
        si.mark_stale("a note landed")
        monkeypatch.setattr(si, "build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert si.search("anything") == []  # no index yet, but no raise either

    def test_a_partial_build_does_not_clear_a_note_write_marker(self, tree, monkeypatch):
        """`build(("chat",))` re-collects no notes. Letting it clear the marker
        would lose the note until some unrelated write set it again."""
        si.mark_stale("a note landed")
        monkeypatch.setattr(si, "available", lambda: (True, ""))
        monkeypatch.setattr(si, "_COLLECTORS", {"chat": lambda: []})
        si.build(("chat",))
        assert si.is_stale() is True

    def test_a_full_build_clears_it(self, tree, monkeypatch):
        si.mark_stale("a note landed")
        monkeypatch.setattr(si, "available", lambda: (True, ""))
        monkeypatch.setattr(si, "_COLLECTORS", {k: (lambda: []) for k in si.KINDS})
        si.build(si.DEFAULT_KINDS)
        assert si.is_stale() is False
