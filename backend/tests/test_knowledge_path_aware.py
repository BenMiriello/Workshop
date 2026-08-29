"""Ticket 1ce4b3c: knowledge store must be path-aware.

Before this fix, `list_notes` used a non-recursive glob so any note filed under
`system/` or `user/` was silently unlistable, unreadable, and never reached a
prompt.  The fix: recursive glob, relative path as note_id, `:path` parameter
in the API routes.
"""
from __future__ import annotations

import asyncio

import pytest

from services import knowledge, ownership


@pytest.fixture
def scope(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    monkeypatch.setattr(knowledge, "_root", lambda: tmp_path)
    monkeypatch.setattr(knowledge, "_runtime", lambda: tmp_path / ".runtime")
    shared = tmp_path / "shared" / "knowledge"
    shared.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(knowledge, "_shared_knowledge_root", lambda: shared)
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")
    ownership.set_current_owner(1)
    return tmp_path


class TestSubdirectoryNotesAreAddressable:
    def test_note_in_user_subdir_is_listable(self, scope):
        asyncio.run(knowledge.put_note("workshop", "user/my-note", "My note", "body", author="operator"))
        ids = {n["id"] for n in knowledge.list_notes("workshop")}
        assert "user/my-note" in ids

    def test_note_in_system_subdir_is_listable(self, scope):
        asyncio.run(knowledge.put_note("workshop", "system/sys-note", "Sys note", "body", author="self"))
        ids = {n["id"] for n in knowledge.list_notes("workshop")}
        assert "system/sys-note" in ids

    def test_flat_note_still_works(self, scope):
        asyncio.run(knowledge.put_note("workshop", "flat", "Flat", "body"))
        ids = {n["id"] for n in knowledge.list_notes("workshop")}
        assert "flat" in ids

    def test_all_three_coexist(self, scope):
        async def seed():
            await knowledge.put_note("workshop", "flat", "Flat", "f", author="operator")
            await knowledge.put_note("workshop", "user/u1", "U1", "u", author="operator")
            await knowledge.put_note("workshop", "system/s1", "S1", "s", author="self")
        asyncio.run(seed())
        ids = {n["id"] for n in knowledge.list_notes("workshop")}
        assert ids >= {"flat", "user/u1", "system/s1"}

    def test_get_note_in_subdir_returns_body(self, scope):
        asyncio.run(knowledge.put_note("workshop", "user/u1", "U1", "hello world", author="operator"))
        body = knowledge.get_note("workshop", "user/u1")
        assert body is not None and "hello world" in body

    def test_note_meta_works_for_subdir_note(self, scope):
        asyncio.run(knowledge.put_note("workshop", "user/u1", "U1", "body", author="operator"))
        meta = knowledge.note_meta("workshop", "user/u1")
        assert meta["author"] == "operator"

    def test_delete_note_in_subdir(self, scope):
        asyncio.run(knowledge.put_note("workshop", "user/u1", "U1", "body"))
        result = asyncio.run(knowledge.delete_note("workshop", "user/u1"))
        assert result["existed"] is True
        assert knowledge.get_note("workshop", "user/u1") is None

    def test_archive_subdir_is_excluded_from_listing(self, scope):
        d = knowledge.scope_dir("workshop")
        d.mkdir(parents=True, exist_ok=True)
        arch = d / "archive" / "old-note.md"
        arch.parent.mkdir(parents=True, exist_ok=True)
        arch.write_text("archived content")
        ids = {n["id"] for n in knowledge.list_notes("workshop")}
        assert "archive/old-note" not in ids

    def test_invalid_path_id_is_rejected(self, scope):
        with pytest.raises(ValueError):
            asyncio.run(knowledge.put_note("workshop", "a/b/c", "Bad", "body"))

    def test_absolute_path_id_is_rejected(self, scope):
        with pytest.raises(ValueError):
            asyncio.run(knowledge.put_note("workshop", "/etc/passwd", "Bad", "body"))
