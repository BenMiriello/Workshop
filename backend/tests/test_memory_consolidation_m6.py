"""M6 of docs/platform-redesign/memory-consolidation-2026-08-16.md sec 5 --
the five correctness bugs found on the way, each pinned by the symptom it
actually had.

Two of the five turned out not to be bugs in the way the plan guessed, and the
tests here say so explicitly rather than asserting a fix that is not there:

  B1  There was no cross-owner DISCLOSURE. `routes/notes_all._scope_roots`
      hardcoded the tracked `shared/knowledge`, but the listing reads contents
      through `knowledge.list_notes_meta(scope_key)`, which resolves per-owner.
      What was wrong was the ENUMERATION -- a second account's `workshop` and
      `development` scopes appeared, or did not, on the strength of the
      operator's disk. Both halves are pinned below.

  B2  `set_source` does not replace across sources and the note write path does
      not fail: it is keyed by source ref, and a note whose body names a ref
      lands its row. `.runtime/backlinks.json` held only chat rows because no
      note or ticket body in the live corpus contained a ref at all (measured
      2026-08-16: 107 notes and 197 tickets, zero refs, zero wikilinks). What is
      pinned here is that the write path works, and that a FAILING backlink
      write is now loud instead of a debug line.
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from services import knowledge, ownership, scope_tree


@pytest.fixture
def two_owners(tmp_path, monkeypatch):
    """An operator and a second account, each with their own knowledge layer,
    over a real (temporary) tree. No fixture substitution of `_scope_roots`:
    these tests exercise the actual disk sweep the route calls."""
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    monkeypatch.setenv("WORKSHOP_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.delenv("WORKSHOP_DEV_MODE", raising=False)

    from services import auth_store, installed_apps, agents_registry, paths
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(knowledge, "_root", lambda: tmp_path)
    monkeypatch.setattr(knowledge, "_runtime", lambda: tmp_path / ".runtime")
    monkeypatch.setattr(paths, "workshop_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "projects_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(scope_tree, "STORE", tmp_path / ".runtime" / "scope-tree.json")
    monkeypatch.setattr(installed_apps, "REGISTRY_PATH", tmp_path / "installed-apps.json")
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(agents_registry, "_user_agents_dir", lambda: agents_dir)

    auth_store.init_db()
    op = auth_store.create_user("operator-m6@example.com", "password1")
    member = auth_store.create_user("member-m6@example.com", "password2")
    auth_store.set_operator(op["id"])

    # The operator's tracked layer, with one note nobody else may see.
    ownership.set_current_owner(op["id"])
    asyncio.run(knowledge.put_note("workshop", "op-only", "Operator house style",
                                   "the operator's own cross-cutting note"))
    try:
        yield {"op": op["id"], "member": member["id"], "tmp": tmp_path}
    finally:
        ownership.clear_current_owner()


# --------------------------------------------------------------------- B1
class TestB1TheNotesListingIsPerOwner:
    def test_the_operator_sees_their_own_workshop_layer(self, two_owners):
        from routes import notes_all
        ownership.set_current_owner(two_owners["op"])
        result = asyncio.run(notes_all.list_all())
        assert {n["id"] for n in result["notes"]} == {"op-only"}

    def test_a_second_account_sees_none_of_the_operators_notes(self, two_owners):
        """The non-disclosure half. This held BEFORE the fix too (contents were
        already resolved per-owner) and the test exists so a future change to
        `_scope_roots` cannot quietly start serving the paths it enumerates."""
        from routes import notes_all
        ownership.set_current_owner(two_owners["member"])
        result = asyncio.run(notes_all.list_all())
        assert result["notes"] == []

    def test_the_enumerated_root_follows_the_owner_not_the_operators_disk(self, two_owners):
        """The half that WAS broken: `_scope_roots` answered "does the workshop
        scope exist?" about the operator's directory for every account."""
        from routes import notes_all
        tracked = (two_owners["tmp"] / "shared" / "knowledge").resolve()

        ownership.set_current_owner(two_owners["op"])
        op_roots = dict(notes_all._scope_roots())
        assert op_roots["workshop"] == tracked

        ownership.set_current_owner(two_owners["member"])
        member_roots = dict(notes_all._scope_roots())
        assert member_roots["workshop"] != tracked
        assert member_roots["workshop"] == knowledge._shared_knowledge_root().resolve()

    def test_a_fresh_account_can_still_be_offered_the_workshop_scope(self, two_owners):
        """A second account starts with no knowledge directory at all. Surfacing
        `workshop` only when its directory exists would leave them no UI path to
        write the first note, and writing the first note is what creates the
        directory."""
        from routes import notes_all
        ownership.set_current_owner(two_owners["member"])
        root = knowledge._shared_knowledge_root()
        assert not root.exists(), "fixture precondition: the member has no layer yet"
        assert "workshop" in asyncio.run(notes_all.list_scopes())["scopes"]

    def test_a_second_accounts_own_note_lists_under_workshop(self, two_owners):
        from routes import notes_all
        ownership.set_current_owner(two_owners["member"])
        asyncio.run(knowledge.put_note("workshop", "mine", "My own note", "body"))
        result = asyncio.run(notes_all.list_all())
        assert [(n["scope"], n["id"]) for n in result["notes"]] == [("workshop", "mine")]
        # ...and the operator is unaffected by the write.
        ownership.set_current_owner(two_owners["op"])
        assert {n["id"] for n in asyncio.run(notes_all.list_all())["notes"]} == {"op-only"}


# --------------------------------------------------------------------- B2
class TestB2BacklinksRecordNoteRows:
    def test_a_note_that_names_a_ref_lands_a_backlink_row(self, two_owners):
        """`set_source` is keyed by SOURCE ref, so it accumulates across
        sources. A note row and a chat row coexist."""
        from services import backlinks, paths, refs
        ownership.set_current_owner(two_owners["op"])
        backlinks.set_source("workshop:chat/t1#m1", ["workshop:note/workshop/op-only"])
        asyncio.run(knowledge.put_note(
            "workshop", "cites", "Cites the other one",
            "see workshop:note/workshop/op-only for the house style"))

        store = json.loads((paths.runtime_dir() / "backlinks.json").read_text())
        assert store["workshop:note/workshop/cites"] == ["workshop:note/workshop/op-only"]
        assert "workshop:chat/t1#m1" in store, "a note write must not evict a chat row"
        assert set(backlinks.referenced_by("workshop:note/workshop/op-only")) == {
            "workshop:note/workshop/cites", "workshop:chat/t1#m1"}
        assert refs.extract_refs("see workshop:note/workshop/op-only") == [
            "workshop:note/workshop/op-only"]

    def test_a_wikilink_in_a_note_body_also_lands(self, two_owners):
        from services import paths
        ownership.set_current_owner(two_owners["op"])
        asyncio.run(knowledge.put_note("workshop", "wikis", "Wikis",
                                       "as covered in [[Operator house style]]"))
        store = json.loads((paths.runtime_dir() / "backlinks.json").read_text())
        assert store["workshop:note/workshop/wikis"] == ["workshop:note/workshop/op-only"]

    def test_an_edit_that_drops_the_ref_drops_the_row(self, two_owners):
        from services import paths
        ownership.set_current_owner(two_owners["op"])
        asyncio.run(knowledge.put_note("workshop", "cites", "Cites",
                                       "see workshop:note/workshop/op-only"))
        asyncio.run(knowledge.put_note("workshop", "cites", "Cites", "no refs any more"))
        store = json.loads((paths.runtime_dir() / "backlinks.json").read_text())
        assert "workshop:note/workshop/cites" not in store

    def test_a_failed_backlink_write_is_logged_at_warning(self, two_owners, monkeypatch, caplog):
        """It used to land in `log.debug(..., exc_info=True)`, which is how "the
        backlink index holds no note rows" becomes a mystery. The note itself
        still writes: this stays best-effort, it just stops being silent."""
        from services import backlinks
        ownership.set_current_owner(two_owners["op"])

        def boom(source_ref, target_refs):
            raise OSError("disk full")

        monkeypatch.setattr(backlinks, "set_source", boom)
        with caplog.at_level(logging.WARNING, logger="workshop.knowledge"):
            asyncio.run(knowledge.put_note("workshop", "loud", "Loud", "body"))
        assert any(r.levelno == logging.WARNING and "backlink index update FAILED" in r.message
                   for r in caplog.records)
        assert knowledge.get_note("workshop", "loud") is not None


# --------------------------------------------------------------------- B3
class TestB3ThePersonaDescribesTheRealPrompt:
    """`routes/chat.py` unconditionally appends LEARNINGS (`:1471`), ONGOING
    WORK (`:1482`) and KNOWLEDGE (`:1500`) to the same prompt the persona sits
    in. Only AUTO-RECALL was removed. The persona said nothing was injected."""

    def test_it_no_longer_claims_nothing_is_injected(self):
        from services.chat_context import ContextProvider
        body = ContextProvider().persona()
        assert "nothing is injected into this chat" not in body

    def test_it_names_what_is_actually_injected(self):
        from services.chat_context import ContextProvider
        body = ContextProvider().persona().lower()
        assert "guardrails" in body
        assert "ongoing work" in body
        assert "titles" in body and "hooks" in body

    def test_it_keeps_the_pull_not_push_instruction(self):
        from services.chat_context import ContextProvider
        body = ContextProvider().persona()
        assert "what you have not looked up, you do not know" in body
        assert "mcp__workshop__search" in body
        # Repo copy rule: no em-dashes in copy the operator or the model reads.
        assert "—" not in body


# --------------------------------------------------------------------- B4
class TestB4RoleScopesAreListable:
    def test_a_role_scope_is_enumerated(self, two_owners):
        from routes import notes_all
        ownership.set_current_owner(two_owners["op"])
        asyncio.run(knowledge.put_note("role:reviewer", "review-evidence",
                                       "Review evidence", "how reviewing is done well"))
        assert "role:reviewer" in [k for k, _ in notes_all._scope_roots()]
        assert "role:reviewer" in asyncio.run(notes_all.list_scopes())["scopes"]

    def test_a_role_note_appears_in_the_cross_scope_listing(self, two_owners):
        from routes import notes_all
        ownership.set_current_owner(two_owners["op"])
        asyncio.run(knowledge.put_note("role:reviewer", "review-evidence",
                                       "Review evidence", "how reviewing is done well"))
        notes = asyncio.run(notes_all.list_all())["notes"]
        assert ("role:reviewer", "review-evidence") in [(n["scope"], n["id"]) for n in notes]

    def test_the_scope_tree_carries_it_as_a_root(self, two_owners):
        """The second axis has no parent and no children (scope_tree: "TWO AXES,
        NOT ONE"), so it renders at depth 0 beside workshop rather than under
        it. Surfacing it must not make it a tree node."""
        from routes import notes_all
        ownership.set_current_owner(two_owners["op"])
        asyncio.run(knowledge.put_note("role:reviewer", "review-evidence",
                                       "Review evidence", "body"))
        tree = {n["key"]: n for n in asyncio.run(notes_all.list_scopes_tree())["scopes"]}
        assert tree["role:reviewer"]["parent"] is None
        assert tree["role:reviewer"]["depth"] == 0
        assert "role:reviewer" not in scope_tree.reach("workshop")

    def test_filtering_to_the_role_scope_returns_only_it(self, two_owners):
        from routes import notes_all
        ownership.set_current_owner(two_owners["op"])
        asyncio.run(knowledge.put_note("role:reviewer", "review-evidence",
                                       "Review evidence", "body"))
        notes = asyncio.run(notes_all.list_all(scope="role:reviewer"))["notes"]
        assert [n["id"] for n in notes] == ["review-evidence"]

    def test_the_scope_it_lists_is_the_one_the_prompt_reads(self, two_owners):
        """The point of B4: what reaches a reviewer's prompt via `govern` is now
        the same key the Notes surface enumerates."""
        from routes import notes_all
        ownership.set_current_owner(two_owners["op"])
        asyncio.run(knowledge.put_note("role:reviewer", "review-evidence",
                                       "Review evidence", "body"))
        assert "role:reviewer" in scope_tree.govern("workshop", role="reviewer")
        assert "role:reviewer" in [k for k, _ in notes_all._scope_roots()]

    def test_no_role_directory_means_no_role_scope(self, two_owners):
        """Enumeration must not CREATE a scope: `knowledge.scope_dir` mkdirs on
        demand, so probing a candidate role list would mint directories."""
        from routes import notes_all
        ownership.set_current_owner(two_owners["op"])
        keys = [k for k, _ in notes_all._scope_roots()]
        assert not any(k.startswith("role:") for k in keys)
        assert not (knowledge._shared_knowledge_root() / "roles").exists()


# --------------------------------------------------------------------- B5
class TestB5TheGpuNoteIsTrue:
    """`shared/knowledge/deep-search-dies-with-the-gpu-no-lexical-fallbac-...md`
    asserted that `search()` has no non-vector path. It has had one since
    `aa457c8`, and the note's title is injected into every chat turn's KNOWLEDGE
    block, so the falsehood was consumed on every turn."""

    NOTE_ID = "deep-search-dies-with-the-gpu-no-lexical-fallbac-1a001d05335"

    def _body(self) -> str:
        from services import paths
        p = paths.workshop_root() / "shared" / "knowledge" / f"{self.NOTE_ID}.md"
        return p.read_text()

    def test_the_note_no_longer_asserts_the_two_false_claims(self):
        body = self._body()
        # The conclusion the old note drew, verbatim. It may be RECALLED as
        # something the note used to say (the memory is kept, not deleted), but
        # it may not be asserted.
        assert "the whole `mcp__workshop__search` tool 500s" not in body
        assert "CORRECTED" in body, "the correction must be visible in the note"
        for stale in ("`search()` has no non-vector path",
                      "is not wired in as a fallback"):
            for line in body.splitlines():
                if stale in line:
                    assert "used to assert" in line, (
                        f"{stale!r} is restated without being marked as past")

    def test_the_note_records_what_is_true_now(self):
        body = self._body()
        assert "BM25" in body
        assert "mode" in body
        # The failure mode itself is real and must not have been deleted with
        # the wrong conclusion.
        assert "out of memory" in body

    def test_the_index_line_matches_the_corrected_note(self):
        from services import paths
        index = (paths.workshop_root() / "shared" / "knowledge" / "MEMORY.md").read_text()
        line = next(ln for ln in index.splitlines() if self.NOTE_ID in ln)
        assert "no lexical fallback" not in line
        assert "degrades to lexical" in line

    def test_search_really_does_degrade_to_lexical(self, tmp_path, monkeypatch):
        """The claim the corrected note makes, asserted against the code rather
        than trusted: an embedder that raises produces ranked lexical hits and a
        `mode` naming the reason, not an exception."""
        import numpy as np

        from services import semantic_index

        # `_bm25_state` keys its cache off the meta file's stat, so it needs an
        # owner to resolve `index_dir()`. Point that at a temp tree: this test
        # must never touch the real index.
        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")

        meta = [
            {"kind": "note", "ref": "workshop:note/workshop/a", "title": "Gzip",
             "scope": "workshop", "ts": "", "text": "gzip middleware compresses nothing"},
            {"kind": "note", "ref": "workshop:note/workshop/b", "title": "Birds",
             "scope": "workshop", "ts": "", "text": "the yard microphone hears sparrows"},
        ]
        vecs = np.zeros((2, 4), dtype=np.float32)

        def boom(texts):
            raise RuntimeError("CUDA error: out of memory")

        monkeypatch.setattr(semantic_index, "load", lambda: (meta, vecs))
        monkeypatch.setattr(semantic_index, "embed", boom)
        monkeypatch.setattr(semantic_index, "_background_rebuild_if_stale", lambda: False)

        # The owner is a contextvar, which monkeypatch cannot unwind, so it is
        # cleared by hand: leaving it pinned leaks into whatever runs next.
        ownership.set_current_owner(4242)
        try:
            res = semantic_index.search("gzip middleware", limit=2)
        finally:
            ownership.clear_current_owner()
        assert res["mode"].startswith("lexical")
        assert "out of memory" in res["mode"]
        assert [h["title"] for h in res["hits"]] == ["Gzip"]
