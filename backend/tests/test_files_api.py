"""The `/files*` surface: one file API over a resolved workspace root
(docs/platform-redesign/file-viewer-and-editor-2026-08-22.md sec 6).

No database: a workspace resolves off the file registries and the filesystem,
which is the point -- the tree an app or a project owns is addressable without
Postgres being up.

What has to be true at the wire:

  - a workspace reference lists a root and a subdirectory, reads a file and
    writes one back, reporting what the noise filter excluded;
  - `..`, an absolute path and a symlink resolving outside the root are all
    refused with a 400, for list, read AND write;
  - an unresolvable workspace is a 404 carrying the reason, never a fallback
    to another workspace's tree;
  - `writable` is served per path, and a write to a read-only path is a 403;
  - `/files/raw` serves the bytes with a media type, under the same
    containment rule, and an SVG cannot execute as a same-origin page.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def tree(tmp_path, monkeypatch):
    proj = tmp_path / "trees" / "widgets"
    (proj / "src").mkdir(parents=True)
    (proj / "README.md").write_text("# widgets\n")
    (proj / "src" / "main.py").write_text("x = 1\n")
    (proj / "node_modules").mkdir()
    (proj / "binary.dat").write_bytes(b"\x00\x01\xffnope")
    (proj / "docs").mkdir()
    (proj / "docs" / "plan.md").write_text("# plan\n")

    reg = tmp_path / "projects.json"
    reg.write_text(json.dumps({"projects": {"widgets": {"dir": str(proj)}}}))
    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(reg))
    monkeypatch.setenv("WORKSHOP_ROOT", str(proj))
    return proj


@pytest.fixture
def client(tree):
    from routes import files as files_routes

    app = FastAPI()
    app.include_router(files_routes.router)
    return TestClient(app)


class TestTree:
    def test_lists_a_workspace_root_and_reports_what_it_filtered(self, client):
        r = client.get("/files/tree", params={"workspace": "project:widgets"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["workspace"] == "project:widgets"
        assert body["root"] == "widgets"
        names = [e["name"] for e in body["entries"]]
        assert "node_modules" not in names
        assert "README.md" in names
        assert body["filtered"] == ["node_modules"]

    def test_lists_a_subdirectory(self, client):
        r = client.get("/files/tree",
                       params={"workspace": "project:widgets", "path": "src"})
        assert r.status_code == 200
        assert [e["name"] for e in r.json()["entries"]] == ["main.py"]

    def test_unknown_workspace_is_404_with_the_reason(self, client):
        r = client.get("/files/tree", params={"workspace": "project:ghost"})
        assert r.status_code == 404
        assert "no directory registered" in r.json()["detail"]

    def test_unknown_workspace_kind_is_404(self, client):
        r = client.get("/files/tree", params={"workspace": "nonsense:x"})
        assert r.status_code == 404

    def test_escape_is_400(self, client):
        for path in ("../", "/etc"):
            r = client.get("/files/tree",
                           params={"workspace": "project:widgets", "path": path})
            assert r.status_code == 400, (path, r.text)

    def test_symlink_escape_is_400(self, client, tree, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside")
        (outside / "secret.txt").write_text("shh\n")
        (tree / "escape").symlink_to(outside)
        r = client.get("/files/tree",
                       params={"workspace": "project:widgets", "path": "escape"})
        assert r.status_code == 400


class TestContent:
    def test_reads_a_text_file_and_says_it_is_writable(self, client):
        r = client.get("/files/content",
                       params={"workspace": "project:widgets", "path": "README.md"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["content"] == "# widgets\n"
        assert body["editable"] is True
        assert body["writable"] is True
        assert body["encoding"] == "utf-8"

    def test_binary_reports_binary_rather_than_bytes_as_text(self, client):
        r = client.get("/files/content",
                       params={"workspace": "project:widgets", "path": "binary.dat"})
        assert r.status_code == 200
        body = r.json()
        assert body["encoding"] == "binary"
        assert body["editable"] is False
        assert body["content"] == ""

    def test_a_file_in_a_filtered_directory_still_reads(self, client, tree):
        (tree / "node_modules" / "junk.js").write_text("noise\n")
        r = client.get("/files/content",
                       params={"workspace": "project:widgets",
                               "path": "node_modules/junk.js"})
        assert r.status_code == 200
        assert r.json()["content"] == "noise\n"

    def test_escape_is_400(self, client):
        r = client.get("/files/content",
                       params={"workspace": "project:widgets", "path": "../outside.txt"})
        assert r.status_code == 400

    def test_missing_file_is_404(self, client):
        r = client.get("/files/content",
                       params={"workspace": "project:widgets", "path": "nope.txt"})
        assert r.status_code == 404


class TestRaw:
    """`/files/raw` is the door an `<img>` and a PDF frame need: the BYTES,
    with the media type, because `/content` correctly reports a binary file's
    content as absent. Containment is the same `safe_path`, so the escape
    cases have to hold here exactly as they do for a text read."""

    def test_serves_the_bytes_with_the_media_type(self, client, tree):
        (tree / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n binary")
        r = client.get("/files/raw",
                       params={"workspace": "project:widgets", "path": "shot.png"})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/png"
        assert r.content == b"\x89PNG\r\n\x1a\n binary"

    def test_an_svg_is_typed_but_cannot_run_as_a_page(self, client, tree):
        # An SVG is a document that can carry script, and it is served from the
        # app's own origin. `default-src 'none'` is what makes a tab opened
        # straight at this URL inert; `nosniff` stops a re-type.
        (tree / "diagram.svg").write_text("<svg><rect /></svg>")
        r = client.get("/files/raw",
                       params={"workspace": "project:widgets", "path": "diagram.svg"})
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/svg+xml"
        assert r.headers["content-security-policy"].startswith("default-src 'none'")
        assert r.headers["x-content-type-options"] == "nosniff"

    def test_an_unknown_extension_is_a_byte_stream_rather_than_a_guess(self, client, tree):
        (tree / "thing.zzz").write_bytes(b"\x00\x01")
        r = client.get("/files/raw",
                       params={"workspace": "project:widgets", "path": "thing.zzz"})
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/octet-stream"

    def test_escape_is_400(self, client):
        r = client.get("/files/raw",
                       params={"workspace": "project:widgets", "path": "../outside.txt"})
        assert r.status_code == 400

    def test_absolute_path_is_400(self, client):
        r = client.get("/files/raw",
                       params={"workspace": "project:widgets", "path": "/etc/passwd"})
        assert r.status_code == 400

    def test_a_directory_is_400(self, client):
        r = client.get("/files/raw",
                       params={"workspace": "project:widgets", "path": "src"})
        assert r.status_code == 400

    def test_missing_file_is_404(self, client):
        r = client.get("/files/raw",
                       params={"workspace": "project:widgets", "path": "nope.png"})
        assert r.status_code == 404

    def test_unknown_workspace_is_404(self, client):
        r = client.get("/files/raw",
                       params={"workspace": "project:ghost", "path": "a.png"})
        assert r.status_code == 404


class TestWrite:
    def test_write_then_read_round_trips(self, client):
        r = client.put("/files/content", json={
            "workspace": "project:widgets", "path": "src/new.py", "content": "y = 2\n"})
        assert r.status_code == 200, r.text
        assert r.json()["path"] == "src/new.py"
        back = client.get("/files/content",
                          params={"workspace": "project:widgets", "path": "src/new.py"})
        assert back.json()["content"] == "y = 2\n"

    def test_escape_is_refused(self, client, tmp_path):
        r = client.put("/files/content", json={
            "workspace": "project:widgets", "path": "../../evil.txt", "content": "x"})
        assert r.status_code == 400
        assert not (tmp_path / "evil.txt").exists()

    def test_binary_overwrite_is_refused(self, client):
        r = client.put("/files/content", json={
            "workspace": "project:widgets", "path": "binary.dat", "content": "text"})
        assert r.status_code == 400


class TestWritableIsTheBackendsAnswer:
    """The `documents` workspace reads the whole repo but writes only under
    `docs/` -- the rule that used to be `doc.path.startsWith("docs/")` in the
    frontend."""

    def test_a_doc_is_writable(self, client):
        r = client.get("/files/content",
                       params={"workspace": "documents", "path": "docs/plan.md"})
        assert r.status_code == 200, r.text
        assert r.json()["writable"] is True

    def test_a_file_outside_docs_is_readable_but_not_writable(self, client):
        r = client.get("/files/content",
                       params={"workspace": "documents", "path": "README.md"})
        assert r.status_code == 200
        body = r.json()
        assert body["content"] == "# widgets\n"
        assert body["writable"] is False

    def test_writing_a_read_only_path_is_403(self, client, tree):
        r = client.put("/files/content", json={
            "workspace": "documents", "path": "README.md", "content": "rewritten"})
        assert r.status_code == 403
        assert (tree / "README.md").read_text() == "# widgets\n"
