"""Layer 0 (managed-file-references-and-path-scoping-2026-08-18.md): `scope_root`
is the single mapping every per-scope path derives from, and the four evicted
state files resolve under the runtime root.

The refactor of `knowledge.scope_dir` MUST be byte-for-byte behavior-preserving:
a project/app/agent knowledge directory has to land at exactly the path it did
before, now spelled `scope_root(scope)/knowledge` instead of the parent literal.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def isolated_root(monkeypatch, tmp_path):
    """Point every root at a throwaway tree and clear the per-path overrides, so
    these assertions are about the CODE and not about the ambient shell (this
    deployment exports WORKSHOP_PROJECTS_ROOT_NEW-adjacent vars and a real
    WORKSHOP_RUNTIME could exist)."""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("WORKSHOP_ROOT", str(root))
    for override in (
        "WORKSHOP_RUNTIME", "WORKSHOP_PROJECTS_ROOT_NEW", "WORKSHOP_PROJECTS_ROOT",
        "WORKSHOP_GRANTS_PATH", "WORKSHOP_RESOURCE_GRANTS_PATH",
        "WORKSHOP_APPS_MANIFEST", "WORKSHOP_STORE_POLICIES_PATH",
    ):
        monkeypatch.delenv(override, raising=False)
    from wsplatform import paths
    return importlib.reload(paths)


def test_scope_root_is_the_documented_mapping(isolated_root):
    paths = isolated_root
    root = paths.workshop_root()
    assert paths.scope_root("project:alpha") == paths.projects_root() / "alpha"
    assert paths.scope_root("project:alpha") == root / "projects" / "alpha"
    assert paths.scope_root("app:ledger") == root / "apps" / "ledger"
    assert paths.scope_root("agent:triager") == paths.runtime_dir() / "agents" / "triager"
    assert paths.scope_root("agent:triager") == root / ".runtime" / "agents" / "triager"


def test_scope_root_refuses_the_non_tree_scopes(isolated_root):
    paths = isolated_root
    for key in ("workshop", "development", "role:reviewer"):
        with pytest.raises(ValueError):
            paths.scope_root(key)


def test_scope_dir_is_identical_before_and_after_for_tree_scopes(isolated_root):
    """The behavior-preserving assertion: for project/app/agent, `scope_dir`
    returns `scope_root(scope)/knowledge`, which is exactly the parent it spelled
    by hand before this refactor."""
    paths = isolated_root
    from services import knowledge

    root = paths.workshop_root()
    expected = {
        "project:alpha": root / "projects" / "alpha" / "knowledge",
        "app:ledger": root / "apps" / "ledger" / "knowledge",
        "agent:triager": root / ".runtime" / "agents" / "triager" / "knowledge",
    }
    for key, want in expected.items():
        got = knowledge.scope_dir(key)
        assert got == want, f"{key}: {got} != {want}"
        # And it is genuinely derived from the single mapping, not a coincidence.
        assert got == paths.scope_root(key) / "knowledge"
        # A plain call is a READ and must not create the scope it names
        # (test_knowledge_scope_dir_is_read_only.py is that rule in full);
        # creating is opt-in, for the paths that are about to write.
        assert not got.exists()
        assert knowledge.scope_dir(key, create=True).is_dir()


def test_state_file_helpers_resolve_under_runtime(isolated_root):
    paths = isolated_root
    rt = paths.runtime_dir()
    assert paths.grants_path() == rt / "grants.json"
    assert paths.resource_grants_path() == rt / "resource-grants.json"
    assert paths.apps_manifest_path() == rt / "apps.json"
    assert paths.store_policies_path() == rt / "store-policies.json"
    for p in (paths.grants_path(), paths.resource_grants_path(),
              paths.apps_manifest_path(), paths.store_policies_path()):
        assert str(p).startswith(str(rt))


def test_state_file_env_override_wins(isolated_root, monkeypatch, tmp_path):
    paths = isolated_root
    custom = tmp_path / "custom-grants.json"
    monkeypatch.setenv("WORKSHOP_GRANTS_PATH", str(custom))
    assert paths.grants_path() == custom


def test_state_file_falls_back_to_old_in_source_before_migration(isolated_root):
    """Until the startup relocation runs, a read must still find the old
    in-source file when the new runtime path is absent."""
    paths = isolated_root
    old = paths.workshop_root() / "backend" / "grants.json"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text('{"grants": [], "guests": []}', encoding="utf-8")
    # new (runtime) path does not exist yet -> fallback to the old location.
    assert paths.grants_path() == old
    # Once the new path exists, the fallback is never taken again.
    new = paths.runtime_dir() / "grants.json"
    new.parent.mkdir(parents=True, exist_ok=True)
    new.write_text("{}", encoding="utf-8")
    assert paths.grants_path() == new


def test_relocations_map_old_source_to_new_runtime(isolated_root):
    paths = isolated_root
    rt = paths.runtime_dir()
    backend = paths.workshop_root() / "backend"
    relocations = dict(paths.state_file_relocations())
    for name in ("grants.json", "resource-grants.json", "apps.json",
                 "store-policies.json"):
        assert (backend / name) in relocations
        assert relocations[backend / name] == rt / name


def test_relocate_moves_old_to_new_and_conserves(isolated_root):
    paths = isolated_root
    from services import state_file_relocate

    old = paths.workshop_root() / "backend" / "resource-grants.json"
    old.parent.mkdir(parents=True, exist_ok=True)
    payload = '{"grants": [{"id": "a"}, {"id": "b"}]}'
    old.write_text(payload, encoding="utf-8")
    new = paths.runtime_dir() / "resource-grants.json"

    moved = state_file_relocate.run_once()

    assert not old.exists()
    assert new.exists()
    assert new.read_text(encoding="utf-8") == payload  # byte-for-byte
    assert any("resource-grants.json" in line for line in moved)
    # Idempotent: a second pass with the file already relocated moves nothing.
    assert state_file_relocate.run_once() == []
