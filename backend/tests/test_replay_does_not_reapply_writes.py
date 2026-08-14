"""A replayed turn must not file its tickets and write its notes a second time.

After a restart, an interrupted turn's reply is re-derived from its journal --
and the journal still carries the ``task-patch``, ``learning-note`` and ``note``
blocks the turn emitted. Those blocks are mediated WRITES: applying them again
files a duplicate ticket and writes a duplicate note, silently and with no way
for the operator to tell which one was real.

`_commit_turn` records `blocks_applied` in the turn's own journal meta the moment
those writes complete, and a replay that sees it strips the blocks instead of
re-applying them, restoring the stored receipts so the reply still reports (and
links to) the effects it actually had.

These tests redirect `knowledge._root`, NOT just `ownership._RUNTIME`. The
`workshop` scope resolves through `paths.workshop_root()`, so a test that
redirects only the runtime writes real notes into the operator's tracked
`shared/knowledge/` and pollutes MEMORY.md. That happened while writing this
file; the fixture is the fix.
"""
from __future__ import annotations

import asyncio

import pytest

from services import knowledge, ownership, task_scope


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "_root", lambda: tmp_path)
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")
    return tmp_path / "shared" / "knowledge"


NOTE_REPLY = (
    "Here is the answer.\n\n"
    '```note\n{"title": "Finding", "body": "Body text", "scope": "workshop"}\n```\n\n'
    "Trailing sentence."
)


def _notes(store):
    return sorted(p.name for p in store.glob("*.md") if p.name != "MEMORY.md")


def test_applying_twice_writes_twice(store):
    """The defect a replay causes, demonstrated rather than asserted.

    Asserts that the note is WRITTEN a second time, not that a second file
    appears: the generated id is time-derived, so two writes in the same tick
    collide onto one filename and the duplicate shows up as an overwrite instead.
    Both outcomes are the same bug (nothing dedupes by content), and only the
    "it wrote again" half is deterministic -- asserting the file count made this
    test pass alone and fail in a full run.
    """
    first = asyncio.run(task_scope.apply_note_block("workshop", NOTE_REPLY))
    second = asyncio.run(task_scope.apply_note_block("workshop", NOTE_REPLY))

    assert first[2], "first application should report a written note"
    assert second[2], "a replay re-applies the block and writes the note again"


def test_the_replay_path_writes_nothing(store):
    """What a turn with `blocks_applied` already set does instead.

    Snapshots file contents, not just names, so an overwrite of the same
    time-derived filename cannot pass as "unchanged"."""
    asyncio.run(task_scope.apply_note_block("workshop", NOTE_REPLY))
    before = {p.name: p.read_text() for p in store.glob("*.md")}

    cleaned = task_scope.strip_all_blocks(NOTE_REPLY)

    after = {p.name: p.read_text() for p in store.glob("*.md")}
    assert after == before, "a replay must not touch the note store at all"
    assert "```note" not in cleaned
    assert "Finding" not in cleaned


def test_the_replayed_reply_still_reads_correctly(store):
    """Skipping and cleaning are the same act. A block left in the reply renders
    as raw JSON and reads like work that just happened."""
    cleaned = task_scope.strip_all_blocks(NOTE_REPLY)
    assert cleaned.startswith("Here is the answer.")
    assert cleaned.endswith("Trailing sentence.")


def test_all_three_block_kinds_are_stripped():
    reply = (
        "text\n\n"
        '```task-patch\n{"add": []}\n```\n\n'
        '```learning-note\n{"note": "x"}\n```\n\n'
        '```note\n{"title": "t", "body": "b"}\n```\n\n'
        "end"
    )
    cleaned = task_scope.strip_all_blocks(reply)
    for fence in ("```task-patch", "```learning-note", "```note"):
        assert fence not in cleaned
    assert cleaned.startswith("text")
    assert cleaned.endswith("end")


def test_a_reply_with_no_blocks_is_untouched_apart_from_trim():
    reply = "just an ordinary answer"
    assert task_scope.strip_all_blocks(reply) == reply
