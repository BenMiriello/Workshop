"""A knowledge READ must not create the scope it reads.

`scope_dir` used to `mkdir(parents=True, exist_ok=True)` unconditionally, and
every read path goes through it, so listing notes CREATED the directory. Two
measured consequences on 2026-08-23:

  * `list_notes("app:<slug>")` for an app that does not exist created
    `apps/<slug>/` and `apps/<slug>/knowledge/`.
  * A caller that sent the JavaScript string "undefined" left
    `.runtime/agents/undefined/knowledge/`, which then showed up in the
    operator's Memory tree as a permanent scope named "Agent: undefined".

`routes/notes_all.py::_scope_roots` enumerates scopes by which directories
exist and documents the invariant this protects: "listing never creates a
scope". These tests are that invariant.
"""
from __future__ import annotations

import asyncio

import pytest

from services import knowledge


@pytest.fixture()
def scope(tmp_path, monkeypatch):
    """A scope whose root is a tmpdir that does NOT exist yet."""
    root = tmp_path / "apps" / "ghost"
    monkeypatch.setattr(knowledge.scope_tree, "scope_root", lambda key: root)
    return "app:ghost", root


def test_scope_dir_does_not_create_by_default(scope):
    key, root = scope
    d = knowledge.scope_dir(key)
    assert d == root / "knowledge"
    assert not d.exists(), "scope_dir created the directory on a plain call"
    assert not root.exists()


def test_reads_return_empty_and_create_nothing(scope):
    key, root = scope
    assert knowledge.list_notes(key) == []
    assert knowledge.get_note(key, "anything") is None
    assert knowledge._plaintext_search(key, "query", 5) == []
    assert not root.exists(), "a read path created the scope directory"


def test_a_write_still_creates_the_scope_and_round_trips(scope):
    key, root = scope
    asyncio.run(knowledge.put_note(key, "a-note", "A note", "# body", hook="hook"))
    assert (root / "knowledge").is_dir()
    assert [n["id"] for n in knowledge.list_notes(key)] == ["a-note"]
    assert "# body" in (knowledge.get_note(key, "a-note") or "")


def test_scope_dir_creates_when_asked(scope):
    key, root = scope
    d = knowledge.scope_dir(key, create=True)
    assert d.is_dir()
