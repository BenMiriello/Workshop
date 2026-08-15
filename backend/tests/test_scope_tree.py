"""services/scope_tree.py -- the single owner of the scope model (Phase 1 of
docs/platform-redesign/memory-and-documentation-model-2026-08-14.md).

Four properties are load-bearing and pinned here (design doc sec 7.4):

  - REACH is downward only: `development` reaches `workshop`, `workshop` does
    NOT reach `development`, and siblings are mutually opaque in both
    directions.
  - A non-dev build has no `development` node; `workshop` is the root.
  - GOVERN is byte-identical to what `knowledge.read_layers` returned before
    this migration, for a table of representative inputs -- the regression
    test that proves the migration changed nothing.
  - Cross-scope recall (`knowledge.recall`) now succeeds where `reach` allows
    it and returns `[]` where it does not, which it could not do at all before
    this phase (`permissions.can_recall` never existed).
"""
from __future__ import annotations

import asyncio

import pytest

from services import app_runner, knowledge, ownership, scope_tree


# --- parse ---------------------------------------------------------------------
class TestParse:
    def test_workshop(self):
        s = scope_tree.parse("workshop")
        assert (s.kind, s.slug) == ("workshop", "")

    def test_project(self):
        s = scope_tree.parse("project:birds")
        assert (s.kind, s.slug) == ("project", "birds")

    def test_role_is_a_valid_shape_but_not_a_tree_node(self):
        s = scope_tree.parse("role:reviewer")
        assert (s.kind, s.slug) == ("role", "reviewer")

    def test_unknown_shape_raises(self):
        with pytest.raises(ValueError):
            scope_tree.parse("nonsense")

    def test_development_raises_when_dev_mode_off(self, monkeypatch):
        monkeypatch.delenv(app_runner.DEV_MODE_ENV, raising=False)
        with pytest.raises(ValueError, match="developer mode is off"):
            scope_tree.parse("development")

    def test_development_parses_when_dev_mode_on(self, monkeypatch):
        monkeypatch.setenv(app_runner.DEV_MODE_ENV, "1")
        s = scope_tree.parse("development")
        assert (s.kind, s.slug) == ("development", "")


# --- reach: direction and siblings ---------------------------------------------
@pytest.fixture
def fixture_tree(monkeypatch):
    """Three concrete scopes (`project:a`, `project:b`, `agent:solo`), all
    parented to `workshop` by default (no override file). Substituting
    `_existing_scopes` directly is the seam this module documents for tests:
    it sidesteps the cache rather than fighting its invalidation key."""
    monkeypatch.setattr(scope_tree, "_existing_scopes",
                        lambda: ["project:a", "project:b", "agent:solo"])
    return None


class TestReachDirection:
    def test_development_reaches_workshop(self, monkeypatch, fixture_tree):
        monkeypatch.setenv(app_runner.DEV_MODE_ENV, "1")
        assert "workshop" in scope_tree.reach("development")

    def test_workshop_does_not_reach_development(self, monkeypatch, fixture_tree):
        monkeypatch.setenv(app_runner.DEV_MODE_ENV, "1")
        assert "development" not in scope_tree.reach("workshop")

    def test_development_reaches_everything(self, monkeypatch, fixture_tree):
        monkeypatch.setenv(app_runner.DEV_MODE_ENV, "1")
        r = scope_tree.reach("development")
        assert set(r) == {"development", "workshop", "project:a", "project:b", "agent:solo"}

    def test_workshop_reaches_its_projects_and_agents(self, monkeypatch, fixture_tree):
        monkeypatch.delenv(app_runner.DEV_MODE_ENV, raising=False)
        r = scope_tree.reach("workshop")
        assert set(r) == {"workshop", "project:a", "project:b", "agent:solo"}

    def test_siblings_are_mutually_unreachable(self, monkeypatch, fixture_tree):
        monkeypatch.delenv(app_runner.DEV_MODE_ENV, raising=False)
        assert "project:b" not in scope_tree.reach("project:a")
        assert "project:a" not in scope_tree.reach("project:b")

    def test_a_scope_always_reaches_itself(self, monkeypatch, fixture_tree):
        monkeypatch.delenv(app_runner.DEV_MODE_ENV, raising=False)
        assert scope_tree.reach("project:a") == ["project:a"]

    def test_no_development_node_when_dev_mode_is_off(self, monkeypatch, fixture_tree):
        monkeypatch.delenv(app_runner.DEV_MODE_ENV, raising=False)
        with pytest.raises(ValueError):
            scope_tree.parse("development")
        assert scope_tree.parent("workshop") is None  # workshop is the root
        assert scope_tree.children("workshop") == ["agent:solo", "project:a", "project:b"]


# --- the stored override file --------------------------------------------------
class TestStoredOverride:
    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        p = tmp_path / "scope-tree.json"
        monkeypatch.setattr(scope_tree, "STORE", p)
        monkeypatch.setattr(scope_tree, "_existing_scopes",
                            lambda: ["project:parent-proj", "app:bound-app"])
        return p

    def test_absent_file_yields_the_full_derived_tree(self, store):
        # No file at all: every scope sits where its KIND implies (both
        # unattached, both children of workshop).
        assert not store.exists()
        assert scope_tree.parent("app:bound-app") == "workshop"
        assert set(scope_tree.children("workshop")) == {"project:parent-proj", "app:bound-app"}

    def test_empty_file_yields_the_same_tree(self, store):
        store.write_text("{}")
        assert scope_tree.parent("app:bound-app") == "workshop"

    def test_an_explicit_entry_overrides_the_default(self, store):
        import json
        store.write_text(json.dumps({"app:bound-app": "project:parent-proj"}))
        assert scope_tree.parent("app:bound-app") == "project:parent-proj"
        assert "app:bound-app" in scope_tree.children("project:parent-proj")
        assert "app:bound-app" not in scope_tree.children("workshop")

    def test_a_malformed_override_target_is_ignored(self, store):
        import json
        store.write_text(json.dumps({"app:bound-app": "not a real scope"}))
        assert scope_tree.parent("app:bound-app") == "workshop"


# --- govern / read_layers equivalence -------------------------------------------
# (scope_key, role, private) -> expected widest-first layer list, reconstructed
# by hand from `knowledge.read_layers`'s pre-migration algorithm. Both `govern`
# and `read_layers` must reproduce this exactly.
GOVERN_TABLE = [
    ("workshop", None, None, ["workshop"]),
    ("project:birds", None, None, ["workshop", "project:birds"]),
    ("project:birds", "reviewer", None, ["workshop", "role:reviewer", "project:birds"]),
    ("agent:scribe", "reviewer", "agent:scribe",
     ["workshop", "role:reviewer", "agent:scribe"]),
    ("workshop", "reviewer", "agent:scribe",
     ["workshop", "role:reviewer", "agent:scribe"]),
    ("development", None, None, ["workshop", "development"]),
    ("", None, None, ["workshop"]),
    ("project:x", "not a valid role!", None, ["workshop", "project:x"]),
]


class TestGovernMatchesReadLayers:
    @pytest.mark.parametrize("scope_key,role,private,expected", GOVERN_TABLE)
    def test_govern(self, scope_key, role, private, expected):
        assert scope_tree.govern(scope_key, role=role, private=private) == expected

    @pytest.mark.parametrize("scope_key,role,private,expected", GOVERN_TABLE)
    def test_read_layers_delegates_to_govern(self, scope_key, role, private, expected):
        assert knowledge.read_layers(scope_key, role=role, private=private) == expected
        assert (knowledge.read_layers(scope_key, role=role, private=private)
                == scope_tree.govern(scope_key, role=role, private=private))


# --- cross-scope recall ---------------------------------------------------------
@pytest.fixture
def notes_tree(tmp_path, monkeypatch):
    """A real, isolated knowledge tree with two sibling projects and an
    unattached agent, all children of `workshop` (no override file), so
    `knowledge.recall` exercises the real `scope_tree.reach` rather than a
    fixture.

    `agent:solo` needs a real registry entry, not just a knowledge note:
    `scope_tree` now enumerates agents off `agents_registry.list_agents`
    (never off which knowledge directories happen to exist), so a note with
    no matching definition file is a note in an orphaned scope, unreachable
    from anywhere but itself -- exactly the class of bug this fixture is
    meant to catch, not accidentally paper over."""
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    monkeypatch.setenv("WORKSHOP_PROJECTS_ROOT", str(tmp_path / "projects"))
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
    (agents_dir / "solo.md").write_text(
        "---\nname: Solo\nruntime: assistant\n---\n\nA standalone agent.\n")
    monkeypatch.setattr(installed_apps, "REGISTRY_PATH", tmp_path / "installed-apps.json")

    ownership.set_current_owner(4242)

    async def seed():
        await knowledge.put_note("workshop", "w1", "Workshop note", "house style")
        await knowledge.put_note("project:alpha", "a1", "Alpha note", "alpha body")
        await knowledge.put_note("project:beta", "b1", "Beta note", "beta body")
        await knowledge.put_note("agent:solo", "s1", "Solo note", "solo body")

    asyncio.run(seed())
    return tmp_path


class TestCrossScopeRecall:
    def test_same_scope_always_recalls(self, notes_tree):
        out = asyncio.run(knowledge.recall("project:alpha", "project:alpha", "alpha"))
        assert out and out[0]["note_id"] == "a1"

    def test_workshop_recalls_into_a_child_project(self, notes_tree):
        out = asyncio.run(knowledge.recall("workshop", "project:alpha", "alpha"))
        assert out and out[0]["note_id"] == "a1"

    def test_workshop_recalls_into_an_unattached_agent(self, notes_tree):
        out = asyncio.run(knowledge.recall("workshop", "agent:solo", "solo"))
        assert out and out[0]["note_id"] == "s1"

    def test_a_project_cannot_recall_upward_into_workshop(self, notes_tree):
        out = asyncio.run(knowledge.recall("project:alpha", "workshop", "house"))
        assert out == []

    def test_sibling_projects_cannot_recall_each_other(self, notes_tree):
        out = asyncio.run(knowledge.recall("project:alpha", "project:beta", "beta"))
        assert out == []
        out2 = asyncio.run(knowledge.recall("project:beta", "project:alpha", "alpha"))
        assert out2 == []

    def test_development_recalls_into_every_scope(self, notes_tree, monkeypatch):
        monkeypatch.setenv(app_runner.DEV_MODE_ENV, "1")
        out = asyncio.run(knowledge.recall("development", "project:alpha", "alpha"))
        assert out and out[0]["note_id"] == "a1"
        out2 = asyncio.run(knowledge.recall("development", "agent:solo", "solo"))
        assert out2 and out2[0]["note_id"] == "s1"


# --- apps and agents come from their registries, never from disk ---------------
@pytest.fixture
def registry_tree(tmp_path, monkeypatch):
    """A real, isolated instance with NO knowledge notes written anywhere --
    the fixture for the class of bug the coordinator caught: `children` and
    `reach` used to enumerate `app:<name>` and `agent:<slug>` off which
    knowledge directories happened to exist, so an installed app or a
    registered agent that had never been written to was invisible to the
    tree, and would have looked like a search bug in Phase 2 rather than a
    tree bug here."""
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    monkeypatch.setenv("WORKSHOP_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(knowledge, "_root", lambda: tmp_path)
    monkeypatch.setattr(knowledge, "_runtime", lambda: tmp_path / ".runtime")
    shared = tmp_path / "shared" / "knowledge"
    monkeypatch.setattr(knowledge, "_shared_knowledge_root", lambda: shared)
    from services import paths
    monkeypatch.setattr(paths, "projects_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(paths, "workshop_root", lambda: tmp_path)
    monkeypatch.setattr(scope_tree, "STORE", tmp_path / ".runtime" / "scope-tree.json")

    from services import agents_registry, installed_apps
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(agents_registry, "_user_agents_dir", lambda: agents_dir)
    monkeypatch.setattr(installed_apps, "REGISTRY_PATH", tmp_path / "installed-apps.json")

    ownership.set_current_owner(4242)
    return tmp_path, agents_dir


def _write_agent(agents_dir, slug, extra_frontmatter=""):
    (agents_dir / f"{slug}.md").write_text(
        f"---\nname: {slug}\nruntime: assistant\n{extra_frontmatter}---\n\nProbe.\n")


class TestAppsAndAgentsAreRegistryDriven:
    def test_a_newly_installed_app_appears_with_no_knowledge_dir(self, registry_tree):
        tmp_path, _agents_dir = registry_tree
        from services import installed_apps
        # "birds" is a real catalog id (app_registry._CATALOG_FACTORIES),
        # independent of whether any note was ever written for it.
        installed_apps.REGISTRY_PATH.write_text('{"installed": ["birds"]}')

        assert not (tmp_path / "shared" / "knowledge").exists()
        assert not (tmp_path / "apps" / "birds").exists()

        assert "app:birds" in scope_tree.children("workshop")
        assert "app:birds" in scope_tree.reach("workshop")
        # Reading the tree must not itself have created a directory.
        assert not (tmp_path / "shared" / "knowledge").exists()
        assert not (tmp_path / "apps" / "birds").exists()

    def test_a_newly_registered_unattached_agent_appears_with_no_knowledge_dir(self, registry_tree):
        tmp_path, agents_dir = registry_tree
        _write_agent(agents_dir, "scribe")

        assert not (tmp_path / ".runtime" / "agents").exists()

        assert "agent:scribe" in scope_tree.children("workshop")
        assert "agent:scribe" in scope_tree.reach("workshop")
        assert not (tmp_path / ".runtime" / "agents").exists()

    def test_an_agent_bound_to_a_project_is_that_projects_child(self, registry_tree):
        _tmp_path, agents_dir = registry_tree
        _write_agent(agents_dir, "keeper", extra_frontmatter="project_slug: birdwatch\n")

        assert scope_tree.parent("agent:keeper") == "project:birdwatch"
        assert "agent:keeper" in scope_tree.children("project:birdwatch")
        assert "agent:keeper" not in scope_tree.children("workshop")
        assert "agent:keeper" in scope_tree.reach("project:birdwatch")
        # A sibling project cannot see a project-bound agent.
        assert "agent:keeper" not in scope_tree.reach("project:other")

    def test_a_retired_agent_does_not_appear(self, registry_tree):
        _tmp_path, agents_dir = registry_tree
        _write_agent(agents_dir, "active")
        _write_agent(agents_dir, "gone", extra_frontmatter="retired: true\n")

        kids = scope_tree.children("workshop")
        assert "agent:active" in kids
        assert "agent:gone" not in kids
        assert "agent:gone" not in scope_tree.reach("workshop")

    def test_development_reaches_registry_scopes_too(self, registry_tree, monkeypatch):
        tmp_path, agents_dir = registry_tree
        monkeypatch.setenv(app_runner.DEV_MODE_ENV, "1")
        from services import installed_apps
        installed_apps.REGISTRY_PATH.write_text('{"installed": ["birds"]}')
        _write_agent(agents_dir, "scribe")

        r = scope_tree.reach("development")
        assert {"workshop", "app:birds", "agent:scribe"} <= set(r)


# --- label -----------------------------------------------------------------------
class TestLabel:
    def test_workshop(self):
        assert scope_tree.label("workshop") == "Workshop"

    def test_development(self, monkeypatch):
        monkeypatch.setenv(app_runner.DEV_MODE_ENV, "1")
        assert scope_tree.label("development") == "Development"

    def test_project(self):
        assert scope_tree.label("project:birds") == "Project: birds"

    def test_empty(self):
        assert scope_tree.label("") == "unknown"

    def test_unparseable_falls_back_to_the_raw_key(self):
        assert scope_tree.label("nonsense") == "nonsense"
