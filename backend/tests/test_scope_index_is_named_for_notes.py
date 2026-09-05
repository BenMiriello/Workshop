"""Memory is the place, a note is the thing, and the scope index is named for
what it indexes.

The per-scope index file was `MEMORY.md` while the page that renders it was
`Memory.tsx` exporting a component called `Notes`: the same naming defect at
both ends of the stack, mirrored. The index holds notes, so it is `INDEX.md`.

Two things are pinned here. The name, so a future writer does not reintroduce
the old one; and the one-way migration, because a missing index is not an error
anywhere in this module (`_read_index` returns {} and `list_notes` falls back to
a directory scan), so a store that never migrated would quietly serve every note
with its filename as its title and nothing would fail.
"""
from __future__ import annotations

import pytest

from services import knowledge


@pytest.fixture
def scope(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    monkeypatch.setattr(knowledge, "_root", lambda: tmp_path)
    monkeypatch.setattr(knowledge, "_runtime", lambda: tmp_path / ".runtime")
    d = tmp_path / "shared" / "knowledge"
    d.mkdir(parents=True)
    monkeypatch.setattr(knowledge, "_shared_knowledge_root", lambda: d)
    knowledge._list_notes_cache.clear()
    return "workshop"


def test_the_index_file_is_named_for_the_notes_it_indexes(scope):
    assert knowledge.INDEX_FILENAME == "INDEX.md"

    knowledge._put_note_sync(scope, "a-finding", "A finding", "body", hook="a hook")

    d = knowledge.scope_dir(scope)
    assert (d / "INDEX.md").exists()
    assert not (d / "MEMORY.md").exists()
    assert "A finding" in (d / "INDEX.md").read_text()


def test_a_store_written_before_the_rename_is_migrated_on_first_read(scope):
    """The shape of every knowledge directory on disk before 2026-09-05."""
    d = knowledge.scope_dir(scope, create=True)
    (d / "older.md").write_text("# Older note\n\nbody\n")
    (d / "MEMORY.md").write_text(
        "# Knowledge index\n\n- [Older note](older.md) - the hook it carried\n"
    )

    notes = knowledge.list_notes(scope)

    assert not (d / "MEMORY.md").exists()
    assert (d / "INDEX.md").exists()
    # The titles and hooks survived. Without the migration this note would come
    # back from the directory scan as {"title": "older", "hook": ""}.
    assert [(n["id"], n["title"], n["hook"]) for n in notes] == [
        ("older", "Older note", "the hook it carried")
    ]


def test_a_leftover_legacy_index_is_never_listed_as_a_note(scope):
    """Belt and braces: if both names are somehow present, the old one is not a
    note. It used to be excluded from the note glob by name, and that exclusion
    had to move with the rename or every scope would have grown a note called
    `MEMORY`."""
    d = knowledge.scope_dir(scope, create=True)
    (d / "INDEX.md").write_text("# Knowledge index\n\n")
    (d / "MEMORY.md").write_text("# Knowledge index\n\n")
    (d / "real.md").write_text("# Real\n\nbody\n")

    assert [n["id"] for n in knowledge.list_notes(scope)] == ["real"]
