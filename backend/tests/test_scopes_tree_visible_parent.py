"""A scope whose parent has no directory attaches to its nearest VISIBLE
ancestor, not to the root.

`/notes/scopes/tree` enumerates scopes that have a knowledge DIRECTORY, while
`scope_tree.parent` answers for the whole model, so a real parent can be absent
from the listing. Dropping such a parent to None promotes the child above
scopes that genuinely contain it.

Measured 2026-08-23: when `project:<slug>` moved under `app:<slug>`
(app-is-the-entity), four projects whose app had no notes of its own jumped
from under `workshop` to the top level, as siblings of `development`.
"""
from __future__ import annotations

import asyncio

import pytest

from routes import notes_all


def _tree(monkeypatch, visible: list[str], parents: dict[str, str | None]):
    from services import scope_tree

    monkeypatch.setattr(notes_all, "_scope_roots", lambda: [(k, None) for k in visible])

    def fake_parent(key: str) -> str | None:
        if key not in parents:
            raise ValueError(key)
        return parents[key]

    monkeypatch.setattr(scope_tree, "parent", fake_parent)
    return asyncio.run(notes_all.list_scopes_tree())["scopes"]


def _parent_of(rows, key):
    return next(r["parent"] for r in rows if r["key"] == key)


def test_an_invisible_parent_is_skipped_not_dropped(monkeypatch):
    # app:budget has no knowledge dir, so it is not listed; project:budget must
    # still land under workshop rather than becoming a root.
    rows = _tree(
        monkeypatch,
        visible=["workshop", "project:budget"],
        parents={"workshop": None, "project:budget": "app:budget", "app:budget": "workshop"},
    )
    assert _parent_of(rows, "project:budget") == "workshop"
    assert [r["key"] for r in rows if r["depth"] == 0] == ["workshop"]


def test_a_visible_parent_is_used_directly(monkeypatch):
    rows = _tree(
        monkeypatch,
        visible=["workshop", "app:birds", "project:birds"],
        parents={"workshop": None, "app:birds": "workshop", "project:birds": "app:birds"},
    )
    assert _parent_of(rows, "project:birds") == "app:birds"
    assert _parent_of(rows, "app:birds") == "workshop"


def test_a_scope_with_no_visible_ancestor_is_a_root(monkeypatch):
    rows = _tree(
        monkeypatch,
        visible=["project:solo"],
        parents={"project:solo": "app:solo", "app:solo": "workshop", "workshop": None},
    )
    assert _parent_of(rows, "project:solo") is None


def test_a_cycle_in_the_override_file_cannot_hang_the_request(monkeypatch):
    # .runtime/scope-tree.json is hand- and agent-editable, so the walk must
    # terminate on a cycle rather than spin.
    rows = _tree(
        monkeypatch,
        visible=["project:a"],
        parents={"project:a": "app:a", "app:a": "project:a"},
    )
    assert _parent_of(rows, "project:a") is None
