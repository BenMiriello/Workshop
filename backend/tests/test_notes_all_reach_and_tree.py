"""Phase 4 additions to `routes/notes_all.py` (docs/platform-redesign/
memory-and-documentation-model-2026-08-14.md sec 11):

  - `GET /notes?scope=X` filters to X AND EVERYTHING IT REACHES
    (`scope_tree.reach`), not an exact match on X -- "its own and everything
    else below it" (sec 4.2).
  - `GET /notes/scopes/tree` shapes the flat scope list as a tree (parent +
    depth per entry), so a picker can show containment by indentation.
  - `GET /notes` (and therefore the Memory page's row list) now carries each
    note's `author` / `type` / `pinned`, not just id/title/hook/updated.
"""
from __future__ import annotations

import asyncio

import pytest

from services import knowledge, ownership, scope_tree


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """`workshop` with two children (`project:alpha`, `project:beta`) and an
    unattached `agent:solo`, each holding one note. Real scope_tree, real
    notes_all._scope_roots (disk-swept), no fixture substitution -- this
    exercises the actual reach computation the route now calls."""
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    monkeypatch.setenv("WORKSHOP_PROJECTS_ROOT", str(tmp_path / "projects"))
    # This box runs with WORKSHOP_DEV_MODE=1 in the real environment; pin it
    # off so `development` does not appear as workshop's parent and the tree
    # shape asserted below is deterministic regardless of where this runs.
    monkeypatch.delenv("WORKSHOP_DEV_MODE", raising=False)
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(knowledge, "_root", lambda: tmp_path)
    monkeypatch.setattr(knowledge, "_runtime", lambda: tmp_path / ".runtime")
    shared = tmp_path / "shared" / "knowledge"
    shared.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(knowledge, "_shared_knowledge_root", lambda: shared)
    from services import paths
    monkeypatch.setattr(paths, "projects_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(paths, "workshop_root", lambda: tmp_path)
    monkeypatch.setattr(scope_tree, "STORE", tmp_path / ".runtime" / "scope-tree.json")

    from services import agents_registry, installed_apps
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(agents_registry, "_user_agents_dir", lambda: agents_dir)
    (agents_dir / "solo.md").write_text("---\nname: Solo\n---\n\nProbe.\n")
    monkeypatch.setattr(installed_apps, "REGISTRY_PATH", tmp_path / "installed-apps.json")

    ownership.set_current_owner(4242)

    async def seed():
        await knowledge.put_note("workshop", "w1", "Workshop note", "house style",
                                  author="operator", note_type="preference", pinned=True)
        await knowledge.put_note("project:alpha", "a1", "Alpha note", "alpha body",
                                  author="agent:builder")
        await knowledge.put_note("project:beta", "b1", "Beta note", "beta body")
        await knowledge.put_note("agent:solo", "s1", "Solo note", "solo body")
    asyncio.run(seed())
    return tmp_path


class TestReachBasedScopeFilter:
    def test_no_scope_returns_everything(self, tree):
        from routes import notes_all
        result = asyncio.run(notes_all.list_all())
        ids = {n["id"] for n in result["notes"]}
        assert ids == {"w1", "a1", "b1", "s1"}

    def test_workshop_reaches_its_children(self, tree):
        # sec 4.2: selecting workshop shows workshop AND everything below it --
        # its projects and unattached agents -- not just workshop's own notes.
        from routes import notes_all
        result = asyncio.run(notes_all.list_all(scope="workshop"))
        ids = {n["id"] for n in result["notes"]}
        assert ids == {"w1", "a1", "b1", "s1"}

    def test_a_project_does_not_reach_its_sibling(self, tree):
        from routes import notes_all
        result = asyncio.run(notes_all.list_all(scope="project:alpha"))
        ids = {n["id"] for n in result["notes"]}
        assert ids == {"a1"}
        assert "b1" not in ids

    def test_an_unparseable_scope_falls_back_to_exact_match_not_500(self, tree):
        from routes import notes_all
        result = asyncio.run(notes_all.list_all(scope="not-a-real-scope"))
        assert result["notes"] == []


class TestMetaOnEveryRow:
    def test_author_type_pinned_are_present(self, tree):
        from routes import notes_all
        result = asyncio.run(notes_all.list_all(scope="workshop"))
        w1 = next(n for n in result["notes"] if n["id"] == "w1")
        assert w1["author"] == "operator"
        assert w1["type"] == "preference"
        assert w1["pinned"] is True
        a1 = next(n for n in result["notes"] if n["id"] == "a1")
        assert a1["author"] == "agent:builder"
        assert a1["type"] == "fact"  # default
        assert a1["pinned"] is False


class TestScopeTree:
    def test_workshop_is_a_root_and_its_children_are_nested(self, tree):
        from routes import notes_all
        result = asyncio.run(notes_all.list_scopes_tree())
        by_key = {s["key"]: s for s in result["scopes"]}
        assert by_key["workshop"]["parent"] is None
        assert by_key["workshop"]["depth"] == 0
        assert by_key["project:alpha"]["parent"] == "workshop"
        assert by_key["project:alpha"]["depth"] == 1
        assert by_key["agent:solo"]["parent"] == "workshop"
        assert by_key["agent:solo"]["depth"] == 1

    def test_parents_render_before_their_children(self, tree):
        # Root-first depth-first order: a picker can render this list top to
        # bottom with indentation and never show a child before its parent.
        from routes import notes_all
        result = asyncio.run(notes_all.list_scopes_tree())
        order = [s["key"] for s in result["scopes"]]
        assert order.index("workshop") < order.index("project:alpha")
        assert order.index("workshop") < order.index("project:beta")
        assert order.index("workshop") < order.index("agent:solo")
