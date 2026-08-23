"""A ticket's words are stored once.

`title` and `body` are canonical (010a9a4); `text` is their join, re-derived by
`_normalize` on every read. During the transition it was ALSO written to disk,
so every record carried the same words twice: measured 2026-08-15, 216 records
across 26 stores. A second copy of a canonical field is the two-sources-of-truth
failure the module warns about everywhere else, and a hand edit could put the
copy out of step with the pair it came from.

These pin the rule from both sides: nothing writes it, and everything reads it.
"""
from __future__ import annotations

import json

from services import tasks


def _on_disk(root) -> list[dict]:
    return json.loads((root / ".tasks.json").read_text())["tasks"]


def test_a_filed_ticket_does_not_store_its_text(tmp_path):
    tasks.add(tmp_path, text="Fix the flaky send\n\nObjective: sends stop dropping.")
    assert all("text" not in rec for rec in _on_disk(tmp_path))


def test_the_pair_is_what_is_stored(tmp_path):
    tasks.add(tmp_path, text="Fix the flaky send\n\nObjective: sends stop dropping.")
    rec = _on_disk(tmp_path)[0]
    assert rec["title"] == "Fix the flaky send"
    assert "Objective: sends stop dropping." in rec["body"]


def test_reading_still_projects_text(tmp_path):
    """Every caller written against `text` (the TODO.md export, reconcile, dedup,
    the shell CLI) keeps working, because the read still has it."""
    tasks.add(tmp_path, text="Fix the flaky send\n\nObjective: sends stop dropping.")
    got = tasks.list_tasks(tmp_path)[0]
    assert got["text"] == tasks.join_text(got["title"], got["body"])
    assert got["text"].startswith("Fix the flaky send")


def test_a_legacy_record_carrying_only_text_still_loads(tmp_path):
    """The stores on disk before 010a9a4 have no pair. Reading migrates them, and
    the migration must not depend on the projection being stored."""
    (tmp_path / ".tasks.json").write_text(json.dumps({
        "version": 2,
        "tasks": [{"id": "abc123", "text": "Old shape\n\nIt only had text.",
                   "status": "todo"}],
    }))
    got = tasks.list_tasks(tmp_path)[0]
    assert got["title"] == "Old shape"
    assert got["body"] == "It only had text."
    assert got["text"] == tasks.join_text("Old shape", "It only had text.")


def test_a_legacy_record_loses_the_duplicate_when_it_is_next_written(tmp_path):
    """Migration is idempotent and needs no rewrite pass: the repair lands the
    next time the store persists."""
    (tmp_path / ".tasks.json").write_text(json.dumps({
        "version": 2,
        "tasks": [{"id": "abc123", "text": "Old shape\n\nIt only had text.",
                   "status": "todo"}],
    }))
    tasks.add(tmp_path, text="Something else")
    recs = {r["id"]: r for r in _on_disk(tmp_path)}
    assert "text" not in recs["abc123"]
    assert recs["abc123"]["title"] == "Old shape"
    assert recs["abc123"]["body"] == "It only had text."


def test_text_is_named_as_a_projection(tmp_path):
    """The list is the rule's only home, so it is what the test reads."""
    assert "text" in tasks._PROJECTED_KEYS
