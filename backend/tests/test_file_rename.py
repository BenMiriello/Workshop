"""Renaming a file keeps the refs that name it.

A `workshop:doc/<path>` ref is a repo-relative path, and the backlink index
stores the RESOLVED target while all three of its mutators are keyed by the
SOURCE. So nothing rewrote a target when the file it named moved, and the edge
disappeared silently the next time the referring note was saved. Same defect as
the note retitle, other half of the same store.
"""
import pytest

from services import app_files, backlinks, doc_refs, knowledge, refs


@pytest.fixture
def ws(tmp_path):
    """A writable workspace whose `docs/` is the only writable prefix, which is
    exactly the shape of the real `documents` workspace."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "vendor").mkdir()
    return app_files.workspace_at(
        tmp_path, display="workshop", ref="documents", write_prefixes=("docs",),
    )


def test_it_renames_within_the_directory(ws):
    (ws.root / "docs" / "old.md").write_text("body", encoding="utf-8")
    row = app_files.rename_file(ws, "docs/old.md", "new.md")
    assert row["path"] == "docs/new.md"
    assert (ws.root / "docs" / "new.md").read_text(encoding="utf-8") == "body"
    assert not (ws.root / "docs" / "old.md").exists()


def test_a_name_is_a_name_and_not_a_path(ws):
    """A rename is not a move. A separator would change what contains the file,
    which is a different operation with a different blast radius."""
    (ws.root / "docs" / "a.md").write_text("x", encoding="utf-8")
    for bad in ("sub/b.md", "../b.md", "..", "."):
        with pytest.raises(app_files.AppFilePathError):
            app_files.rename_file(ws, "docs/a.md", bad)
    assert (ws.root / "docs" / "a.md").exists()


def test_it_refuses_to_clobber(ws):
    (ws.root / "docs" / "a.md").write_text("a", encoding="utf-8")
    (ws.root / "docs" / "b.md").write_text("b", encoding="utf-8")
    with pytest.raises(FileExistsError):
        app_files.rename_file(ws, "docs/a.md", "b.md")
    assert (ws.root / "docs" / "a.md").read_text(encoding="utf-8") == "a"
    assert (ws.root / "docs" / "b.md").read_text(encoding="utf-8") == "b"


def test_a_read_only_location_is_refused(ws):
    (ws.root / "vendor" / "x.md").write_text("x", encoding="utf-8")
    with pytest.raises(app_files.AppFileReadOnlyError):
        app_files.rename_file(ws, "vendor/x.md", "y.md")
    assert (ws.root / "vendor" / "x.md").exists()


def test_renaming_to_its_own_name_is_a_no_op_not_a_failure(ws):
    """An idempotent retry must not look like a broken one."""
    (ws.root / "docs" / "a.md").write_text("a", encoding="utf-8")
    row = app_files.rename_file(ws, "docs/a.md", "a.md")
    assert row["path"] == "docs/a.md"


def test_a_missing_file_is_not_found(ws):
    with pytest.raises(FileNotFoundError):
        app_files.rename_file(ws, "docs/nope.md", "x.md")


# ---- the refs half ---------------------------------------------------------


@pytest.fixture
def notes(tmp_path, monkeypatch):
    d = tmp_path / "knowledge"
    d.mkdir()
    monkeypatch.setattr(knowledge, "scope_dir", lambda key, create=False: d)
    monkeypatch.setattr(backlinks, "_path", lambda: tmp_path / "backlinks.json")
    backlinks._cache = None
    return "workshop"


def test_a_note_that_names_the_doc_is_repointed(notes):
    knowledge._put_note_sync(
        notes, "source", "Source",
        "see workshop:doc/docs/old.md for the detail",
    )
    # The index must actually name the doc, or repoint has nothing to walk and
    # this test would pass for the wrong reason.
    assert refs.note_ref(notes, "source") in backlinks.referenced_by(
        refs.doc_ref("docs/old.md")
    )

    rewrote, stale = doc_refs.repoint("docs/old.md", "docs/new.md")

    body = knowledge.get_note(notes, "source")
    assert "workshop:doc/docs/new.md" in body
    assert "workshop:doc/docs/old.md" not in body
    assert rewrote == [refs.note_ref(notes, "source")]
    assert stale == []


def test_a_markdown_link_to_the_path_is_repointed_too(notes):
    knowledge._put_note_sync(
        notes, "source", "Source",
        "the [design](docs/old.md) doc, and workshop:doc/docs/old.md",
    )
    doc_refs.repoint("docs/old.md", "docs/new.md")
    body = knowledge.get_note(notes, "source")
    assert "[design](docs/new.md)" in body
    assert "docs/old.md" not in body


def test_a_doc_nothing_names_repoints_nothing(notes):
    rewrote, stale = doc_refs.repoint("docs/lonely.md", "docs/renamed.md")
    assert rewrote == []
    assert stale == []
