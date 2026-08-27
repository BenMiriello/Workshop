"""Retitling a note rewrites the `[[Title]]` links that point at it.

THE BUG THIS PINS (memory-page-and-file-viewer-2026-08-27.md 1.6 and D11).
`refs.extract_refs` resolves `[[Title]]` to a note id by TITLE LOOKUP, at the
SOURCE's write time. A note's id never changes, so a retitle leaves every
inbound `[[old title]]` naming nothing -- and the failure is delayed and
silent, because the backlink row stores the RESOLVED ref and survives until
the source is next saved, at which point the edge disappears with no error.

It matters more now that a note's title is editable in one obvious place, in
the header of the Memory page's document viewer.
"""
import pytest

from services import backlinks, knowledge, refs


@pytest.fixture
def scope(tmp_path, monkeypatch):
    """A knowledge scope and a backlink index, both on tmp_path."""
    d = tmp_path / "knowledge"
    d.mkdir()
    monkeypatch.setattr(knowledge, "scope_dir", lambda key, create=False: d)
    monkeypatch.setattr(backlinks, "_path", lambda: tmp_path / "backlinks.json")
    backlinks._cache = None
    return "workshop"


def _write(scope_key, note_id, title, body):
    knowledge._put_note_sync(scope_key, note_id, title, body)


def test_retitle_rewrites_an_inbound_wikilink(scope):
    _write(scope, "target", "Old Title", "the target")
    _write(scope, "source", "Source", "as covered in [[Old Title]], see below")

    # The source's row must actually name the target, or the rewrite has
    # nothing to walk and this test would pass for the wrong reason.
    assert refs.note_ref(scope, "source") in backlinks.referenced_by(refs.note_ref(scope, "target"))

    _write(scope, "target", "New Title", "the target")

    body = knowledge.get_note(scope, "source")
    assert "[[New Title]]" in body
    assert "[[Old Title]]" not in body


def test_it_leaves_other_wikilinks_alone(scope):
    _write(scope, "target", "Old Title", "the target")
    _write(scope, "other", "Other", "other")
    _write(scope, "source", "Source", "[[Old Title]] and [[Other]]")

    _write(scope, "target", "New Title", "the target")

    body = knowledge.get_note(scope, "source")
    assert "[[New Title]]" in body
    assert "[[Other]]" in body


def test_a_label_and_an_anchor_survive_the_rewrite(scope):
    _write(scope, "target", "Old Title", "the target")
    _write(scope, "source", "Source", "[[Old Title|as it was called]] and [[Old Title#part]]")

    _write(scope, "target", "New Title", "the target")

    body = knowledge.get_note(scope, "source")
    assert "[[New Title|as it was called]]" in body
    assert "[[New Title#part]]" in body


def test_writing_a_note_without_changing_its_title_rewrites_nothing(scope):
    _write(scope, "target", "Same Title", "v1")
    _write(scope, "source", "Source", "[[Same Title]]")

    _write(scope, "target", "Same Title", "v2")

    assert "[[Same Title]]" in knowledge.get_note(scope, "source")


def test_a_note_nothing_links_to_retitles_without_error(scope):
    _write(scope, "lonely", "Before", "body")
    _write(scope, "lonely", "After", "body")
    assert knowledge.get_note(scope, "lonely").strip().endswith("body")
