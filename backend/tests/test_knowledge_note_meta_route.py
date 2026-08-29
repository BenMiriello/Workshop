"""`routes/knowledge.py`'s PUT/GET round-trip `author` (Phase 4 of
docs/platform-redesign/memory-and-documentation-model-2026-08-14.md sec 11).
Covers the property that matters for the editor: an edit that omits `author`
PRESERVES the stored author rather than erasing it (an edit is not a
reassignment of who wrote the note).
"""
from __future__ import annotations

import asyncio

import pytest

from routes import knowledge as knowledge_routes
from services import knowledge, ownership


@pytest.fixture
def tree(tmp_path, monkeypatch):
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
    monkeypatch.setattr(paths, "workshop_root", lambda: tmp_path)
    ownership.set_current_owner(4242)
    return tmp_path


def test_author_set_on_create_is_readable_back(tree):
    asyncio.run(knowledge_routes.put_note(
        "workshop", "n1",
        knowledge_routes.PutNoteIn(title="A note", content="body", author="operator"),
    ))
    got = asyncio.run(knowledge_routes.get_note("workshop", "n1"))
    assert got["author"] == "operator"


def test_edit_without_author_preserves_the_stored_author(tree):
    asyncio.run(knowledge_routes.put_note(
        "workshop", "n1",
        knowledge_routes.PutNoteIn(title="A note", content="body", author="agent:builder"),
    ))
    # The operator edits the body in the Memory page's editor, which does not
    # send `author` -- that must not silently reassign authorship to "".
    asyncio.run(knowledge_routes.put_note(
        "workshop", "n1",
        knowledge_routes.PutNoteIn(title="A note", content="edited body"),
    ))
    got = asyncio.run(knowledge_routes.get_note("workshop", "n1"))
    assert got["author"] == "agent:builder"
    assert got["content"] == "edited body"
