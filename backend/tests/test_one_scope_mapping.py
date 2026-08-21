"""One context_key -> scope mapping, and every site delegates to it (T3).

THE BUG THIS PINS. On 2026-08-16 there were FIVE independent implementations of
`context_key -> scope` in the backend, plus one in the frontend, and they
disagreed:

    chat_store._scope_from_context_key    `development` -> `app:development`
    knowledge.scope_for_context           `development` -> `development`
    chat_repo._thread_fields              the same rule "in one line", by hand
    chat_work.task_scope_for              the context_key, unmapped
    mentions.home_scope                   its own three-prefix allowlist

`app:development` is an address no other module recognises: `scope_tree.reach`
mints it for nobody (there is no installed app named `development`), so the 27
live threads stamped with it fell out of every reach-filtered surface, including
the chat list on `Memory.tsx`, and labelled themselves "App: development".

The rule now lives in `scope_tree.scope_for_context` and nowhere else. These
tests call each site by its own name and compare the answers, so they fail the
moment a sixth copy appears.
"""
from __future__ import annotations

import json

import pytest

from db import chat_repo
from services import (
    chat_list_pg,
    chat_store,
    chat_work,
    knowledge,
    mentions,
    ownership,
    scope_tree,
)
from wsplatform import paths


# Every context_key FORM the system can hold, and the ONE scope each resolves to.
#
# `zzz-not-an-app` is the load-bearing row: an unknown bare key resolves to
# `workshop`, NOT to `app:zzz-not-an-app`. `knowledge.scope_dir` mkdirs on
# demand, so minting an app scope for an unrecognised name created
# `apps/zzz-not-an-app/knowledge/` inside the tracked tree just by opening the
# chat. `test_fence_scope_containment.py` asserts the same thing from the other
# end; it is restated here so this table is total.
#
# `budget` is the bare-installed-app form and `bare-app` stands in for it under
# the stubbed catalog below, so the row does not depend on which apps this
# instance happens to have.
KEY_FORMS: list[tuple[str, str]] = [
    ("", "workshop"),
    ("general", "workshop"),
    ("workshop", "workshop"),
    ("new-project", "workshop"),
    ("development", "development"),
    ("project:birds", "project:birds"),
    ("app:budget", "app:budget"),
    ("agent:scribe", "agent:scribe"),
    # `build:<slug>` was a second spelling of `agent:<slug>` and is DELETED, so
    # it is now an unrecognised kind like any other and resolves to the root.
    ("build:unlinked", "workshop"),
    ("bare-app", "app:bare-app"),
    ("zzz-not-an-app", "workshop"),
    ("role:maker", "workshop"),
    ("project:", "workshop"),
]

# The scopes above that a chat gets no task queue for. `workshop` HAS a queue
# (`shared/queue`) but a chat is not put in front of the root backlog just for
# being unbound, and an app dock has no queue at all.
QUEUELESS = {"workshop"}


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    """A deterministic app catalog and project tree for the table.

    `bare-app` is the only installed app; `has-a-project` is the only project on
    disk. Both are read through the real seams (`app_registry.installed_app_ids`
    and `paths.projects_root`), so the mapping is exercised, not bypassed.

    `services.app_links` (what `chat_work.task_scope_for` asks for an
    `app:<slug>` key, app-is-the-entity-2026-08-20.md sec 5.1) reads the raw
    per-instance registry directly rather than through the catalog stub
    above, so it is isolated a second time here -- otherwise it falls through
    to this host's real `.runtime/installed-apps.json` and
    `.runtime/projects.json`, and the table stops being deterministic.
    Neither `bare-app` nor `budget` names `has-a-project`, so both stay
    unlinked and queueless, same as before this fixture existed.
    """
    from services import app_registry, installed_apps

    projects = tmp_path / "projects"
    (projects / "has-a-project").mkdir(parents=True)
    monkeypatch.setattr(paths, "projects_root", lambda: projects)
    monkeypatch.setattr(app_registry, "installed_app_ids",
                        lambda: ["bare-app", "budget"])
    # Only `bare-app` needs to be here: `app_links.linked_project_slug`
    # answers None for anything not ALSO a key of the projects registry
    # below (neither `bare-app` nor the app-form-tested `budget` name
    # `has-a-project`), so the KEY_FORMS row for `budget` is unaffected by
    # whether it appears in this particular list.
    monkeypatch.setattr(installed_apps, "installed_app_ids",
                        lambda: ["bare-app"])
    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({
        "projects": {"has-a-project": {"dir": str(projects / "has-a-project")}}}))
    monkeypatch.setattr(paths, "projects_registry", lambda: registry)
    return tmp_path


# --- the canonical answer -------------------------------------------------------
class TestTheCanonicalMapping:
    @pytest.mark.parametrize("key,scope", KEY_FORMS)
    def test_every_key_form(self, catalog, key, scope):
        assert scope_tree.scope_for_context(key) == scope

    def test_none_is_the_unbound_chat(self, catalog):
        assert scope_tree.scope_for_context(None) == "workshop"

    def test_it_is_not_gated_on_developer_mode(self, catalog, monkeypatch):
        """`parse("development")` raises with the flag off, deliberately. WHERE a
        chat is bound is a different question from whether that scope resolves
        to a working tree, and answering `workshop` here would silently re-home
        every development chat onto the root queue."""
        from wsplatform import dev_mode
        monkeypatch.delenv(dev_mode.DEV_MODE_ENV, raising=False)
        assert scope_tree.scope_for_context("development") == "development"

    def test_development_is_never_an_app(self, catalog):
        """The exact value that damaged 27 threads. `development` is the platform
        repo and its own root (ADR 0014), not an installed app."""
        assert scope_tree.scope_for_context("development") != "app:development"


# --- every site gives the SAME answer -------------------------------------------
class TestEverySiteAgrees:
    @pytest.mark.parametrize("key,scope", KEY_FORMS)
    def test_knowledge_scope_for_context(self, catalog, key, scope):
        assert knowledge.scope_for_context(key) == scope

    @pytest.mark.parametrize("key,scope", KEY_FORMS)
    def test_chat_store_document(self, catalog, key, scope):
        """The thread document a caller is handed. This is the site that used to
        say `app:development`."""
        t = chat_store._document({"id": "x", "context_key": key}, [], [])
        assert t["scope"] == scope

    @pytest.mark.parametrize("key,scope", KEY_FORMS)
    def test_chat_repo_thread_fields(self, catalog, key, scope):
        """The Postgres mirror's column value."""
        assert chat_repo._thread_fields({"context_key": key})["scope"] == scope

    @pytest.mark.parametrize("key,scope", KEY_FORMS)
    def test_chat_list_pg_shape_row(self, catalog, key, scope):
        """The PG read path's thread-list row, which must match the file fold."""
        row = {"external_id": "x", "context_key": key, "title": "t"}
        assert chat_list_pg._shape_row(row)["scope"] == scope

    @pytest.mark.parametrize("key,scope", KEY_FORMS)
    def test_mentions_home_scope(self, catalog, key, scope):
        """An agent's home, from its registry record. A project link wins; with
        none, the record's own `scope` goes through the same mapping."""
        assert mentions.home_scope({"scope": key}) == scope

    @pytest.mark.parametrize("key,scope", KEY_FORMS)
    def test_chat_work_task_scope_for(self, catalog, key, scope):
        """The task-store scope, which is the canonical scope or None. The ONLY
        thing this site decides is which scopes a chat gets a queue for; it may
        never answer with a different scope."""
        got = chat_work.task_scope_for(key)
        if scope in QUEUELESS or scope.startswith("app:"):
            assert got is None
        else:
            assert got == scope

    def test_no_site_still_knows_the_deleted_build_kind(self, catalog):
        """`build:<slug>` is gone, and gone means every site treats it as the
        junk key it now is rather than one of them quietly keeping the
        translation. An unrecognised kind resolves to the root, and the root is
        queueless for a chat."""
        for key in ("build:unlinked", "build:has-a-project"):
            assert scope_tree.scope_for_context(key) == "workshop"
            assert knowledge.scope_for_context(key) == "workshop"
            assert chat_work.task_scope_for(key) is None
            assert chat_repo._thread_fields({"context_key": key})["scope"] == "workshop"

    def test_a_mentionable_agent_is_local_to_its_own_development_chat(self, catalog):
        """The concrete consequence of site 5 disagreeing. `home_scope` fell to
        `workshop` for anything outside its three-prefix allowlist, so an agent
        declaring `development` was homed in the root: mentionable from every
        chat in the install, and never `local` to the one it belongs to."""
        chat = knowledge.scope_for_context("development")
        assert mentions.home_scope({"scope": "development"}) == chat


# --- a rescope leaves `scope` correct -------------------------------------------
class TestScopeSurvivesARescope:
    @pytest.fixture
    def owned(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
        ownership.set_current_owner(1)
        try:
            yield
        finally:
            ownership.clear_current_owner()

    def test_rebinding_the_context_key_moves_the_scope(self, catalog, owned):
        """`rescope_thread` writes `context_key` and nothing else. `scope` used
        to be back-filled only when ABSENT, so a rebound chat kept reporting the
        scope it was created in for the rest of its life. It is derived on every
        read now, so it cannot lag."""
        t = chat_store.create_thread("general")
        assert chat_store.get_thread(t["id"])["scope"] == "workshop"

        chat_store.update_thread(t["id"], context_key="development")
        assert chat_store.get_thread(t["id"])["scope"] == "development"

        chat_store.update_thread(t["id"], context_key="project:birds")
        assert chat_store.get_thread(t["id"])["scope"] == "project:birds"

    def test_the_list_row_moves_with_it(self, catalog, owned):
        """The sidebar reads a different code path from the thread document, and
        both have to say the same thing."""
        t = chat_store.create_thread("general")
        chat_store.update_thread(t["id"], context_key="development")
        row = chat_store._summary_row(t["id"])
        assert row["scope"] == "development"

    def test_a_saved_document_does_not_persist_the_derivation(self, catalog, owned):
        """A round-trip through `save_thread` must not write the derived value
        back into meta. A stored copy is exactly what went stale."""
        t = chat_store.create_thread("general")
        doc = chat_store.get_thread(t["id"])
        doc["title"] = "renamed"
        chat_store.save_thread(doc)

        from services import chat_log
        assert "scope" not in chat_log.read_meta(t["id"])
        assert chat_store.get_thread(t["id"])["scope"] == "workshop"


# --- no live thread shape can produce `app:development` -------------------------
class TestNoThreadCanCarryAppDevelopment:
    def test_no_context_key_maps_to_it(self, catalog):
        """Exhaustive over the key forms plus the exact key that produced it."""
        for key, _ in KEY_FORMS:
            assert scope_tree.scope_for_context(key) != "app:development"
        assert scope_tree.scope_for_context("development") == "development"

    def test_not_even_when_development_is_an_installed_app_id(self, tmp_path,
                                                              monkeypatch):
        """`development` is matched BEFORE the app catalog, so a catalog that
        somehow listed it could not resurrect the address."""
        from services import app_registry
        monkeypatch.setattr(paths, "projects_root", lambda: tmp_path / "p")
        monkeypatch.setattr(app_registry, "installed_app_ids",
                            lambda: ["development"])
        assert scope_tree.scope_for_context("development") == "development"

    def test_the_stored_field_cannot_resurrect_it(self, catalog):
        """The 27 damaged threads carried it in meta.json. Every read derives
        now, so even a thread still holding the bad value reports the right one
        -- which is what makes the data repair a cleanup rather than the fix."""
        meta = {"id": "x", "context_key": "development", "scope": "app:development"}
        assert chat_store._document(dict(meta), [], [])["scope"] == "development"
        assert chat_repo._thread_fields(meta)["scope"] == "development"
        assert chat_list_pg._shape_row({
            "external_id": "x", "context_key": "development",
            "scope": "app:development", "title": "t",
        })["scope"] == "development"

    def test_it_is_not_a_scope_the_tree_can_reach(self, catalog, monkeypatch):
        """Why the value was damage and not just an odd spelling: nothing mints
        it, so a surface filtering the chat list against `reach()` (Memory.tsx)
        can never match a thread stamped with it."""
        monkeypatch.setattr(scope_tree, "_existing_scopes",
                            lambda: ["project:birds", "app:bare-app"])
        monkeypatch.setattr(scope_tree, "_agent_bound_project", lambda _slug: None)
        assert "app:development" not in scope_tree.reach("workshop")
