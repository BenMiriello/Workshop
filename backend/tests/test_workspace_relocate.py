"""The operator's knowledge notes and the workshop queue export live under the
runtime root, and an existing install's in-source copies are carried across
once without overwriting anything."""
from __future__ import annotations

import asyncio
import logging

import pytest

from services import knowledge, ownership, workspace_relocate
from wsplatform import paths


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(workspace_relocate, "_queue_dest",
                        lambda: paths.operator_workspace("queue"))
    ownership.clear_current_owner()
    return tmp_path


def _legacy_knowledge(repo):
    old = repo / "shared" / "knowledge"
    (old / "system").mkdir(parents=True)
    (old / "roles" / "reviewer").mkdir(parents=True)
    (old / "INDEX.md").write_text(
        "# Knowledge index\n\n- [Ports are squatted](system/ports.md) - 8012 lies\n")
    (old / "system" / "ports.md").write_text("8012 fakes healthy.\n")
    (old / "roles" / "reviewer" / "INDEX.md").write_text(
        "# role:reviewer memory index\n\n- [Ask for evidence](evidence.md)\n")
    (old / "roles" / "reviewer" / "evidence.md").write_text("Cite a command.\n")
    return old


def test_the_operators_layer_resolves_under_the_runtime_root(repo):
    assert knowledge.scope_dir("workshop") == repo / ".runtime" / "workspace" / "knowledge"
    assert knowledge.scope_dir("role:reviewer") == (
        repo / ".runtime" / "workspace" / "knowledge" / "roles" / "reviewer")


def test_the_workshop_queue_resolves_under_the_runtime_root():
    from services import task_scope
    assert not task_scope.WORKSHOP_SCOPE_DIR.is_relative_to(
        paths.workshop_root() / "shared")


def test_existing_notes_are_copied_and_read_back(repo, caplog):
    _legacy_knowledge(repo)
    with caplog.at_level(logging.INFO, logger="workshop.workspace_relocate"):
        lines = workspace_relocate.run_once()

    new = paths.operator_workspace("knowledge")
    assert (new / "system" / "ports.md").read_text() == "8012 fakes healthy.\n"
    assert (new / "roles" / "reviewer" / "evidence.md").exists()
    assert any(ln.startswith("knowledge: copied 4 file(s)") for ln in lines)
    assert "workspace relocated: knowledge" in caplog.text
    # The old copies are left in place; only git stops tracking them.
    assert (repo / "shared" / "knowledge" / "system" / "ports.md").exists()

    text, used = knowledge.layered_index_block("workshop")
    assert "Ports are squatted" in text
    assert "workshop" in used


def test_a_file_already_at_the_destination_is_never_overwritten(repo):
    _legacy_knowledge(repo)
    new = paths.operator_workspace("knowledge")
    (new / "system").mkdir(parents=True)
    (new / "system" / "ports.md").write_text("edited after the upgrade\n")

    lines = workspace_relocate.run_once()

    assert (new / "system" / "ports.md").read_text() == "edited after the upgrade\n"
    assert (new / "INDEX.md").exists()
    assert any("copied 3 file(s)" in ln and "kept 1 already present" in ln
               for ln in lines)


def test_it_runs_once_and_does_not_resurrect_a_deleted_note(repo):
    _legacy_knowledge(repo)
    assert workspace_relocate.run_once()
    new = paths.operator_workspace("knowledge")
    (new / "system" / "ports.md").unlink()

    assert workspace_relocate.run_once() == []
    assert not (new / "system" / "ports.md").exists()


def test_the_queue_carries_its_export_and_not_the_backup_store(repo):
    old = repo / "shared" / "queue"
    old.mkdir(parents=True)
    (old / "TODO.md").write_text("# TODO\n\n- [ ] one\n")
    (old / ".tasks.json").write_text('{"tasks": []}')

    workspace_relocate.run_once()

    new = paths.operator_workspace("queue")
    assert (new / "TODO.md").read_text() == "# TODO\n\n- [ ] one\n"
    assert not (new / ".tasks.json").exists()


def test_a_fresh_install_with_no_tracked_files_works(repo):
    assert not (repo / "shared").exists()
    assert workspace_relocate.run_once() == []
    assert workspace_relocate.marker_path("knowledge").exists()
    assert not (repo / "shared").exists()

    asyncio.run(knowledge.put_note("workshop", "first", "First note", "body\n"))
    text, _ = knowledge.layered_index_block("workshop")
    assert "First note" in text
    assert (paths.operator_workspace("knowledge") / "first.md").exists()
    assert not (repo / "shared").exists()
