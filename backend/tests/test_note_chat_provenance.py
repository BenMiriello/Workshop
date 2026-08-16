"""A note filed from a chat records WHICH CHAT (docs/platform-redesign/memory-
and-documentation-model-2026-08-14.md sec 5.2: "some retention of the chat that
it came from ... so that at least that is reconstructable later"). Structured
metadata on the same marker line Phase 0 introduced (`<!-- author: X; type: Y;
pinned: true -->`), extended with a `chat` field, not a sidecar file and not a
second mechanism.

Covers:
  - a note with no marker, or an author-only marker, still parses (the property
    Phase 0's own tests already pin, re-checked here against the extended
    regex);
  - `put_note(..., chat_id=...)` records it and it round-trips through
    `note_meta` and `list_notes_meta`;
  - an edit that omits `chat_id` preserves the stored value, exactly like
    `author`;
  - `task_scope.apply_note_block(..., thread_id=...)` threads it through to the
    written note, which is the real write path (build_streaming's finalize
    passes the turn's `thread_id`).
"""
from __future__ import annotations

import asyncio

import pytest

from services import knowledge, ownership, task_scope


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
    from services import paths
    monkeypatch.setattr(paths, "projects_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(paths, "workshop_root", lambda: tmp_path)
    ownership.set_current_owner(4242)
    return tmp_path


def _put(scope, note_id, title, body, **kw):
    return asyncio.run(knowledge.put_note(scope, note_id, title, body, **kw))


# --- backward compatibility: the extended regex must not break old notes ----

def test_a_note_with_no_marker_still_parses():
    author, ntype, pinned, chat, body = knowledge._parse_meta_line("Just prose, no marker.")
    assert (author, ntype, pinned, chat) == ("", "", None, "")
    assert body == "Just prose, no marker."
    assert knowledge.note_chat("Just prose, no marker.") == ""


def test_an_author_only_marker_still_parses():
    raw = "<!-- author: operator -->\nThe body.\n"
    author, ntype, pinned, chat, body = knowledge._parse_meta_line(raw)
    assert author == "operator"
    assert ntype == ""
    assert pinned is None
    assert chat == ""
    assert body == "The body.\n"


def test_an_author_type_pinned_marker_still_parses_with_no_chat():
    raw = "<!-- author: operator; type: decision; pinned: true -->\nThe body.\n"
    author, ntype, pinned, chat, body = knowledge._parse_meta_line(raw)
    assert (author, ntype, pinned, chat) == ("operator", "decision", True, "")
    assert body == "The body.\n"


# --- the new field ------------------------------------------------------------

def test_put_note_with_chat_id_records_it_in_the_marker(tree):
    _put("workshop", "n1", "A note", "body", author="operator", chat_id="abc123")
    raw = (tree / "shared" / "knowledge" / "n1.md").read_text()
    assert raw.startswith("<!-- author: operator")
    assert "chat: abc123" in raw.splitlines()[0]


def test_chat_id_round_trips_through_note_meta(tree):
    _put("workshop", "n1", "A note", "body", author="operator", chat_id="thread-42")
    meta = knowledge.note_meta("workshop", "n1")
    assert meta["chat"] == "thread-42"


def test_chat_id_round_trips_through_list_notes_meta(tree):
    _put("workshop", "n1", "A note", "body", author="operator", chat_id="thread-42")
    rows = knowledge.list_notes_meta("workshop")
    assert len(rows) == 1
    assert rows[0]["chat"] == "thread-42"


def test_a_note_with_no_chat_id_reports_empty_not_fabricated(tree):
    _put("workshop", "n1", "A note", "body", author="operator")
    meta = knowledge.note_meta("workshop", "n1")
    assert meta["chat"] == ""


def test_an_edit_that_omits_chat_id_preserves_the_stored_value(tree):
    _put("workshop", "n1", "A note", "body", author="operator", chat_id="thread-1")
    # A later edit (the operator's own editor, or a re-save) sends no chat_id.
    _put("workshop", "n1", "A note", "edited body", author="operator")
    meta = knowledge.note_meta("workshop", "n1")
    assert meta["chat"] == "thread-1"
    assert knowledge.get_note("workshop", "n1") == "edited body"


def test_an_untouched_old_note_stays_byte_for_byte_the_same(tree):
    """No author, default type, unpinned, no chat: the marker stays omitted
    entirely, exactly as it did before this field existed."""
    _put("workshop", "n1", "A note", "plain body")
    raw = (tree / "shared" / "knowledge" / "n1.md").read_text()
    assert raw == "plain body"


# --- the real write path: apply_note_block(thread_id=...) -------------------

@pytest.fixture()
def chat_root(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    (tmp_path / "shared" / "knowledge").mkdir(parents=True)
    from services import auth_store, ownership as _ownership
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
    auth_store.init_db()
    op = auth_store.create_user("operator@example.com", "password1")
    auth_store.set_operator(op["id"])
    _ownership.set_current_owner(op["id"])
    try:
        yield tmp_path
    finally:
        _ownership.clear_current_owner()


def _block(payload: str) -> str:
    return f"Here you go.\n\n```note\n{payload}\n```\n"


def test_apply_note_block_threads_the_thread_id_into_the_note(chat_root):
    cleaned, labels, refs = asyncio.run(task_scope.apply_note_block(
        "workshop",
        _block('{"title": "Ports are squatted", "body": "8012 fakes healthy.", '
               '"scope": "workshop"}'),
        thread_id="thread-abc",
    ))
    assert len(refs) == 1
    ref = refs[0]
    written = chat_root / "shared" / "knowledge" / f"{ref['id']}.md"
    assert written.exists()
    from services import knowledge as _knowledge
    assert _knowledge.note_chat(written.read_text()) == "thread-abc"


def test_apply_note_block_with_no_thread_id_records_no_chat(chat_root):
    cleaned, labels, refs = asyncio.run(task_scope.apply_note_block(
        "workshop",
        _block('{"title": "No chat here", "body": "body text.", "scope": "workshop"}'),
    ))
    ref = refs[0]
    written = chat_root / "shared" / "knowledge" / f"{ref['id']}.md"
    from services import knowledge as _knowledge
    assert _knowledge.note_chat(written.read_text()) == ""
