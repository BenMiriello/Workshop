"""The docs write path (Phase 4 of docs/platform-redesign/
memory-and-documentation-model-2026-08-14.md sec 11): `PUT /docs/file` lets the
operator edit a doc from the Memory page. Docs were read-only before this;
`routes/docs_index.py` had `GET ""` and `GET /file` and no PUT.

Two properties are load-bearing:

  - a write to a real `.md` file under `docs/` lands on disk and reads back;
  - the SAME containment check the read route already enforces rejects a path
    that tries to escape `docs/`, exactly as it does for the read.

No git commit happens here or in the route: the write lands in the working
tree and the operator commits it on their own schedule (design doc sec 11).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from routes import docs_index


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A repo root with a docs/ tree under tmp_path."""
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "existing.md").write_text("# Existing\n\nOriginal body.\n")
    (tmp_path / "secret.md").write_text("outside docs/\n")
    from wsplatform import paths
    monkeypatch.setattr(paths, "workshop_root", lambda: tmp_path)
    return tmp_path


class TestWriteRoundTrips:
    def test_write_then_read_back(self, tree):
        docs_index.write_doc(docs_index.PutDocIn(
            path="docs/existing.md", content="# Existing\n\nEdited body.\n"))
        got = docs_index.read_doc(path="docs/existing.md")
        assert got["content"] == "# Existing\n\nEdited body.\n"
        assert got["path"] == "docs/existing.md"
        # Lands on disk, not just in a cache: a fresh read of the file agrees.
        assert (tree / "docs" / "existing.md").read_text() == "# Existing\n\nEdited body.\n"

    def test_write_does_not_commit(self, tree):
        # No git plumbing is invoked at all: writing the file directly, with no
        # git repo initialized in the fixture, must not raise.
        docs_index.write_doc(docs_index.PutDocIn(
            path="docs/existing.md", content="still uncommitted\n"))
        assert (tree / "docs" / "existing.md").read_text() == "still uncommitted\n"


class TestContainment:
    def test_traversal_outside_docs_is_rejected(self, tree):
        with pytest.raises(HTTPException) as exc:
            docs_index.write_doc(docs_index.PutDocIn(
                path="docs/../secret.md", content="pwned\n"))
        assert exc.value.status_code == 400
        # And the file outside docs/ was never touched.
        assert (tree / "secret.md").read_text() == "outside docs/\n"

    def test_absolute_path_outside_repo_is_rejected(self, tree):
        with pytest.raises(HTTPException) as exc:
            docs_index.write_doc(docs_index.PutDocIn(path="/etc/passwd", content="pwned\n"))
        assert exc.value.status_code == 400

    def test_non_markdown_is_rejected(self, tree):
        (tree / "docs" / "notes.txt").write_text("not markdown\n")
        with pytest.raises(HTTPException) as exc:
            docs_index.write_doc(docs_index.PutDocIn(path="docs/notes.txt", content="x\n"))
        assert exc.value.status_code == 400

    def test_cannot_create_a_new_file(self, tree):
        # Deliberate: the write path edits an existing doc, it does not create
        # one (see write_doc's docstring for why).
        with pytest.raises(HTTPException) as exc:
            docs_index.write_doc(docs_index.PutDocIn(path="docs/brand-new.md", content="x\n"))
        assert exc.value.status_code == 404

    def test_read_route_rejects_the_same_traversal(self, tree):
        # The read route's own containment check, unchanged by this phase --
        # pinned here so the two routes are proven to agree.
        with pytest.raises(HTTPException) as exc:
            docs_index.read_doc(path="docs/../secret.md")
        assert exc.value.status_code == 400
