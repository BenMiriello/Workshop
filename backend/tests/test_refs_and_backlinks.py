"""The ref format, resolver and backlink index (Phase 3 of
docs/platform-redesign/memory-and-documentation-model-2026-08-14.md, sec 10).

Load-bearing properties pinned here:
  - parse/serialize round-trips for every ref kind;
  - an unresolvable ref (deleted note, unknown ticket) refuses rather than
    resolving to a dead-looking chip;
  - a doc ref cannot escape docs/;
  - the backlink index updates when a note is edited to remove a ref, and
    when a note is deleted;
  - the same for tickets, at the `tasks._persist` choke point;
  - `[[Title]]` wikilinks inside a knowledge note resolve through the SAME
    resolver as `workshop:note/...` refs (wikilink convergence, sec 12
    Phase 3 / sec D).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import backlinks, chat_store, knowledge, ownership, refs, tasks  # noqa: E402


# --- parse / serialize ---------------------------------------------------------
class TestParseSerialize:
    @pytest.mark.parametrize("text", [
        "workshop:note/workshop/some-note-id",
        "workshop:doc/docs/decisions/0001-foo.md",
        "workshop:ticket/abc123",
        "workshop:chat/thread-1#msg-1",
        "workshop:chat/thread-1",
        "workshop:run/job_123",
        "workshop:project/widgets",
        "workshop:agent/reviewer",
        "workshop:app/notes",
    ])
    def test_round_trips(self, text):
        parsed = refs.parse_ref(text)
        assert refs.serialize_ref(parsed) == text

    def test_constructors_match_parse(self):
        assert refs.note_ref("workshop", "abc") == "workshop:note/workshop/abc"
        assert refs.doc_ref("docs/x.md") == "workshop:doc/docs/x.md"
        assert refs.ticket_ref("t1") == "workshop:ticket/t1"
        assert refs.chat_ref("th1", "m1") == "workshop:chat/th1#m1"
        assert refs.run_ref("j1") == "workshop:run/j1"
        assert refs.project_ref("p1") == "workshop:project/p1"
        assert refs.agent_ref("a1") == "workshop:agent/a1"
        assert refs.app_ref("notes") == "workshop:app/notes"

    @pytest.mark.parametrize("bad", [
        "", "not-a-ref", "workshop:", "workshop:bogus/x",
        "workshop:note/", "workshop:note/onlyscope",
        "workshop:chat/#no-thread",
    ])
    def test_bad_shapes_raise(self, bad):
        with pytest.raises(ValueError):
            refs.parse_ref(bad)

    def test_doc_ref_cannot_escape_docs(self):
        with pytest.raises(ValueError):
            refs.parse_ref("workshop:doc/../shared/knowledge/MEMORY.md")
        with pytest.raises(ValueError):
            refs.parse_ref("workshop:doc/docs/../../etc/passwd")


# --- extraction ------------------------------------------------------------
class TestExtractRefs:
    def test_finds_and_normalizes_workshop_refs(self):
        text = "See (workshop:note/workshop/abc) and workshop:ticket/xyz."
        found = refs.extract_refs(text)
        assert found == ["workshop:note/workshop/abc", "workshop:ticket/xyz"]

    def test_drops_unparseable_candidates(self):
        assert refs.extract_refs("workshop:bogus/nope and workshop:note/x") == []

    def test_dedupes_preserving_order(self):
        text = "workshop:ticket/a workshop:ticket/b workshop:ticket/a"
        assert refs.extract_refs(text) == ["workshop:ticket/a", "workshop:ticket/b"]

    def test_empty_text(self):
        assert refs.extract_refs("") == []
        assert refs.extract_refs(None) == []  # type: ignore[arg-type]


# --- resolution: unresolvable refuses, never a dead chip -----------------------
class TestResolveRefuses:
    def test_bad_shape_returns_none(self):
        assert refs.resolve_ref("not a ref at all") is None

    def test_unknown_kind_returns_none(self):
        assert refs.resolve_ref("workshop:spaceship/x") is None


# --- fixtures for the store-backed kinds ----------------------------------------
@pytest.fixture
def tree(tmp_path, monkeypatch):
    """An isolated workshop knowledge scope + task root under tmp_path, mirroring
    test_knowledge_index_digest.tree (tests-must-not-share-owner-storage)."""
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    monkeypatch.setenv("WORKSHOP_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(knowledge, "_root", lambda: tmp_path)
    monkeypatch.setattr(knowledge, "_runtime", lambda: tmp_path / ".runtime")
    shared = tmp_path / "shared" / "knowledge"
    shared.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(knowledge, "_shared_knowledge_root", lambda: shared)
    from wsplatform import paths
    monkeypatch.setattr(paths, "projects_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(paths, "workshop_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "runtime_dir", lambda: tmp_path / ".runtime")
    ownership.set_current_owner(4242)
    return tmp_path


def _put(scope, note_id, title, body, **kw):
    asyncio.run(knowledge.put_note(scope, note_id, title, body, **kw))


class TestNoteResolutionAndBacklinks:
    def test_resolve_real_note(self, tree):
        _put("workshop", "n1", "First note", "hello there")
        got = refs.resolve_ref("workshop:note/workshop/n1")
        assert got == {
            "kind": "note", "title": "First note",
            "href": "/memory?scope=workshop&note=n1", "status": "current",
            "preview": {"excerpt": "hello there"},
        }

    def test_resolve_deleted_note_refuses(self, tree):
        _put("workshop", "n1", "First note", "hello there")
        asyncio.run(knowledge.delete_note("workshop", "n1"))
        assert refs.resolve_ref("workshop:note/workshop/n1") is None

    def test_backlink_recorded_on_write(self, tree):
        _put("workshop", "target", "Target note", "body")
        _put("workshop", "source", "Source note",
             "see workshop:note/workshop/target for context")
        assert backlinks.referenced_by("workshop:note/workshop/target") == [
            "workshop:note/workshop/source"
        ]

    def test_backlink_removed_when_ref_edited_out(self, tree):
        _put("workshop", "target", "Target note", "body")
        _put("workshop", "source", "Source note",
             "see workshop:note/workshop/target for context")
        assert backlinks.referenced_by("workshop:note/workshop/target") == [
            "workshop:note/workshop/source"
        ]
        # Rewrite the source note with the ref removed.
        _put("workshop", "source", "Source note", "nothing here anymore")
        assert backlinks.referenced_by("workshop:note/workshop/target") == []

    def test_backlink_removed_when_source_note_deleted(self, tree):
        _put("workshop", "target", "Target note", "body")
        _put("workshop", "source", "Source note",
             "see workshop:note/workshop/target for context")
        asyncio.run(knowledge.delete_note("workshop", "source"))
        assert backlinks.referenced_by("workshop:note/workshop/target") == []

    def test_wikilink_converges_on_the_same_resolver(self, tree):
        """`[[Title]]` inside a knowledge note resolves through the SAME
        resolver as an explicit `workshop:note/...` ref -- one link language,
        not two (sec D)."""
        _put("workshop", "target", "Target Note", "body")
        _put("workshop", "source", "Source note", "see [[Target Note]] please")
        assert backlinks.referenced_by("workshop:note/workshop/target") == [
            "workshop:note/workshop/source"
        ]


class TestTicketResolutionAndBacklinks:
    @pytest.fixture
    def scoped(self, tree, monkeypatch):
        root = tree / "tickets-root"
        root.mkdir()
        from routes import tasks_all
        monkeypatch.setattr(tasks_all, "scope_roots", lambda: [("workshop", root)])
        return root

    def test_resolve_real_ticket(self, scoped):
        t = tasks.add(scoped, title="Fix the thing", body="details")
        got = refs.resolve_ref(refs.ticket_ref(t["id"]))
        assert got is not None
        assert got["kind"] == "ticket"
        assert got["title"] == "Fix the thing"
        assert got["href"] == f"/tickets/workshop/{t['id']}"

    def test_resolve_unknown_ticket_refuses(self, scoped):
        assert refs.resolve_ref("workshop:ticket/does-not-exist") is None

    def test_resolve_by_unique_id_prefix(self, scoped):
        """THE FORM EVERY HUMAN AND DOC USES HAS TO RESOLVE.

        CLAUDE.md, ticket bodies, handoff docs and chat replies all name a
        ticket by its first eight characters. Resolving by full id only meant
        an agent writing the convention its own instructions use produced a
        chip reading "Unavailable" -- five in a row on 2026-09-18."""
        t = tasks.add(scoped, title="Fix the thing", body="details")
        got = refs.resolve_ref(f"workshop:ticket/{t['id'][:8]}")
        assert got is not None
        assert got["title"] == "Fix the thing"
        # The href carries the FULL id even though the ref was a prefix.
        assert got["href"] == f"/tickets/workshop/{t['id']}"

    def test_prefix_shorter_than_eight_refuses(self, scoped):
        """A stray hex-ish word in prose must not resolve to a real ticket."""
        t = tasks.add(scoped, title="Fix the thing", body="details")
        assert refs.resolve_ref(f"workshop:ticket/{t['id'][:6]}") is None

    def test_ambiguous_prefix_resolves_to_nothing(self, scoped, monkeypatch):
        """Guessing between two matches would put the wrong title on the chip,
        which reads as confirmed and is worse than unresolved."""
        a = tasks.add(scoped, title="First", body="")
        b = tasks.add(scoped, title="Second", body="")
        shared = "abcdef0123"
        real_list = tasks.list_tasks

        def fake_list(root, **kw):
            out = []
            for t in real_list(root, **kw):
                t = dict(t)
                if t["id"] == a["id"]:
                    t["id"] = shared + "1"
                elif t["id"] == b["id"]:
                    t["id"] = shared + "2"
                out.append(t)
            return out

        monkeypatch.setattr(tasks, "list_tasks", fake_list)
        assert refs.resolve_ref(f"workshop:ticket/{shared}") is None

    def test_backlink_recorded_and_removed_on_edit(self, scoped):
        target = tasks.add(scoped, title="Target ticket", body="")
        source = tasks.add(
            scoped, title="Source ticket",
            body=f"blocked on {refs.ticket_ref(target['id'])}",
        )
        assert backlinks.referenced_by(refs.ticket_ref(target["id"])) == [
            refs.ticket_ref(source["id"])
        ]
        tasks.update(scoped, source["id"], body="no longer blocked on anything")
        assert backlinks.referenced_by(refs.ticket_ref(target["id"])) == []

    def test_backlink_removed_when_ticket_deleted(self, scoped):
        target = tasks.add(scoped, title="Target ticket", body="")
        source = tasks.add(
            scoped, title="Source ticket",
            body=f"blocked on {refs.ticket_ref(target['id'])}",
        )
        assert backlinks.referenced_by(refs.ticket_ref(target["id"])) == [
            refs.ticket_ref(source["id"])
        ]
        assert tasks.remove(scoped, source["id"])
        assert backlinks.referenced_by(refs.ticket_ref(target["id"])) == []


class TestChatResolution:
    """Stored chat messages carry no `id` field (role/content/ts/meta only),
    so a chat ref resolves on thread existence alone. The `#<message-id>`
    suffix, when given, is parse-only and becomes an href anchor -- it is
    never checked against the thread's messages."""

    def test_bare_thread_ref_resolves(self, tree):
        thread = chat_store.create_thread(context_key="general", title="Hi")
        got = refs.resolve_ref(f"workshop:chat/{thread['id']}")
        assert got is not None
        assert got["kind"] == "chat"
        assert got["title"] == "Hi"
        assert got["href"] == f"/chat/general?thread={thread['id']}"
        assert got["status"] == "active"

    def test_missing_thread_refuses(self, tree):
        assert refs.resolve_ref("workshop:chat/does-not-exist") is None

    def test_hash_message_id_form_still_parses_and_resolves(self, tree):
        thread = chat_store.create_thread(context_key="general", title="Hi")
        ref_str = refs.chat_ref(thread["id"], "msg-anything")
        parsed = refs.parse_ref(ref_str)
        assert parsed.kind == "chat"
        got = refs.resolve_ref(ref_str)
        assert got is not None
        assert got["href"] == f"/chat/general?thread={thread['id']}#msg-msg-anything"


class TestBacklinksStoreDirectly:
    def test_set_source_replaces_not_appends(self, tree):
        backlinks.set_source("workshop:ticket/a", ["workshop:note/workshop/x"])
        backlinks.set_source("workshop:ticket/a", ["workshop:note/workshop/y"])
        assert backlinks.referenced_by("workshop:note/workshop/x") == []
        assert backlinks.referenced_by("workshop:note/workshop/y") == [
            "workshop:ticket/a"
        ]

    def test_empty_target_list_drops_the_row(self, tree):
        backlinks.set_source("workshop:ticket/a", ["workshop:note/workshop/x"])
        backlinks.set_source("workshop:ticket/a", [])
        assert backlinks.referenced_by("workshop:note/workshop/x") == []

    def test_remove_source(self, tree):
        backlinks.set_source("workshop:ticket/a", ["workshop:note/workshop/x"])
        backlinks.remove_source("workshop:ticket/a")
        assert backlinks.referenced_by("workshop:note/workshop/x") == []
