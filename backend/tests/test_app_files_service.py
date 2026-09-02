"""Unit tests for `services/app_files.py` directly against the filesystem,
no database and no HTTP layer required -- this is where the path-containment
logic and the workspace resolution actually live, so they should be provable
on their own. The HTTP-level tests (including the wire-level escape tests) are
in `test_apps_files_api.py`.
"""
from __future__ import annotations

import json

import pytest

from services import app_files


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("# hello\n")
    (tmp_path / "src" / "main.py").write_text("x = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("noise\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "binary.dat").write_bytes(b"\x00\x01\xffnotutf8")
    return tmp_path


@pytest.fixture
def ws(root):
    """A workspace with no write restriction -- the app/project shape."""
    return app_files.workspace_at(root, "myapp", ref="test:myapp")


@pytest.fixture
def readonly_ws(root):
    """A workspace whose writes are confined to `docs/` -- the shape the
    `documents` resolver produces, and the reason `writable` is a backend
    answer rather than a frontend `path.startsWith("docs/")`."""
    (root / "docs").mkdir()
    (root / "docs" / "plan.md").write_text("# plan\n")
    return app_files.workspace_at(root, "workshop", ref="test:docs",
                                  write_prefixes=("docs",))


class TestListTree:
    def test_lists_root_dirs_first_and_filters_noise_dirs(self, ws):
        result = app_files.list_tree(ws, "")
        names = [e["name"] for e in result["entries"]]
        assert "node_modules" not in names
        assert ".git" not in names
        assert "src" in names
        assert "README.md" in names
        assert sorted(result["filtered"]) == [".git", "node_modules"]
        kinds = [e["kind"] for e in result["entries"]]
        assert kinds == sorted(kinds)  # dirs ("dir" < "file") before files

    def test_lists_a_subdirectory(self, ws):
        result = app_files.list_tree(ws, "src")
        assert result["path"] == "src"
        assert [e["name"] for e in result["entries"]] == ["main.py"]

    def test_missing_subpath_raises_file_not_found(self, ws):
        with pytest.raises(FileNotFoundError):
            app_files.list_tree(ws, "nope")

    def test_a_file_path_raises_not_a_directory(self, ws):
        with pytest.raises(NotADirectoryError):
            app_files.list_tree(ws, "README.md")

    def test_a_filtered_directory_still_lists_when_named_directly(self, ws):
        """The filter is a usability filter on listings, not a boundary: name
        `node_modules` and you get it."""
        result = app_files.list_tree(ws, "node_modules")
        assert [e["name"] for e in result["entries"]] == ["junk.js"]

    def test_ordinary_dotfiles_are_not_hidden(self, root, ws):
        (root / ".env").write_text("SECRET=1\n")
        (root / ".github").mkdir()
        names = [e["name"] for e in app_files.list_tree(ws, "")["entries"]]
        assert ".env" in names
        assert ".github" in names

    def test_root_display_name_comes_from_the_workspace(self, ws):
        assert app_files.list_tree(ws, "")["root"] == "myapp"


class TestReadFile:
    def test_reads_text(self, ws):
        result = app_files.read_file(ws, "README.md")
        assert result["content"] == "# hello\n"
        assert result["editable"] is True
        assert result["encoding"] == "utf-8"

    def test_binary_file_is_not_editable_not_an_error(self, ws):
        result = app_files.read_file(ws, "binary.dat")
        assert result["editable"] is False
        assert result["content"] == ""
        assert result["encoding"] == "binary"

    def test_oversized_text_file_is_truncated_not_an_error(self, root, ws, monkeypatch):
        monkeypatch.setattr(app_files, "_MAX_INLINE_BYTES", 10)
        big = root / "big.txt"
        big.write_text("x" * 1000)
        result = app_files.read_file(ws, "big.txt")
        assert result["editable"] is False
        assert result["truncated"] is True
        assert result["encoding"] == "utf-8"
        assert result["size"] == 1000

    def test_missing_file_raises(self, ws):
        with pytest.raises(FileNotFoundError):
            app_files.read_file(ws, "nope.txt")

    def test_directory_path_raises_is_a_directory(self, ws):
        with pytest.raises(IsADirectoryError):
            app_files.read_file(ws, "src")

    def test_a_file_inside_a_filtered_directory_still_reads(self, ws):
        result = app_files.read_file(ws, "node_modules/junk.js")
        assert result["content"] == "noise\n"


class TestWritableIsAnswered:
    def test_writable_true_when_the_workspace_has_no_restriction(self, ws):
        assert app_files.read_file(ws, "README.md")["writable"] is True

    def test_writable_false_outside_the_write_prefix(self, readonly_ws):
        result = app_files.read_file(readonly_ws, "README.md")
        assert result["writable"] is False
        # Read-only is about the LOCATION, not the bytes: it is still a
        # perfectly editable text file, just not here.
        assert result["editable"] is True

    def test_writable_true_inside_the_write_prefix(self, readonly_ws):
        assert app_files.read_file(readonly_ws, "docs/plan.md")["writable"] is True

    def test_write_is_refused_where_writable_is_false(self, readonly_ws, root):
        with pytest.raises(app_files.AppFileReadOnlyError):
            app_files.write_file(readonly_ws, "README.md", "rewritten")
        assert (root / "README.md").read_text() == "# hello\n"

    def test_write_is_allowed_inside_the_write_prefix(self, readonly_ws):
        out = app_files.write_file(readonly_ws, "docs/plan.md", "# new\n")
        assert out["path"] == "docs/plan.md"
        assert out["writable"] is True

    def test_a_prefix_lookalike_is_not_inside_it(self, root, readonly_ws):
        """`docsy/` is not `docs/`. The check is path-segment based, not a
        raw string prefix."""
        (root / "docsy").mkdir()
        (root / "docsy" / "x.md").write_text("nope\n")
        assert app_files.read_file(readonly_ws, "docsy/x.md")["writable"] is False

    def test_an_empty_prefix_tuple_is_a_wholly_read_only_workspace(self, root):
        frozen = app_files.workspace_at(root, "frozen", write_prefixes=())
        assert app_files.read_file(frozen, "README.md")["writable"] is False
        with pytest.raises(app_files.AppFileReadOnlyError):
            app_files.write_file(frozen, "anything.txt", "x")


class TestWriteFile:
    def test_write_then_read_round_trips(self, ws):
        out = app_files.write_file(ws, "src/new.py", "y = 2\n")
        assert out["path"] == "src/new.py"
        assert out["size"] == len("y = 2\n")
        assert app_files.read_file(ws, "src/new.py")["content"] == "y = 2\n"

    def test_write_is_atomic_no_tmp_file_left_behind(self, root, ws):
        app_files.write_file(ws, "atomic.txt", "hi\n")
        leftovers = list(root.glob("*.tmp"))
        assert leftovers == []

    def test_refuses_null_byte_content(self, ws):
        with pytest.raises(app_files.AppFileBinaryError):
            app_files.write_file(ws, "evil.txt", "abc\x00def")

    def test_refuses_overwriting_a_binary_file(self, ws):
        with pytest.raises(app_files.AppFileBinaryError):
            app_files.write_file(ws, "binary.dat", "now text")

    def test_refuses_writing_over_a_directory(self, ws):
        with pytest.raises(IsADirectoryError):
            app_files.write_file(ws, "src", "nope")


class TestPathContainment:
    """The one part of this feature that is a correctness requirement, not a
    nicety: `..`, an absolute path, and a symlink resolving outside the root
    must all be refused, for every one of list/read/write."""

    def test_dotdot_escape_refused_on_list(self, ws):
        with pytest.raises(app_files.AppFilePathError):
            app_files.list_tree(ws, "../")

    def test_dotdot_escape_refused_on_read(self, root, ws):
        (root.parent / "outside.txt").write_text("secret\n")
        with pytest.raises(app_files.AppFilePathError):
            app_files.read_file(ws, "../outside.txt")

    def test_dotdot_escape_refused_on_write(self, ws):
        with pytest.raises(app_files.AppFilePathError):
            app_files.write_file(ws, "../../tmp/evil.txt", "x")

    def test_absolute_path_refused_on_list(self, ws):
        with pytest.raises(app_files.AppFilePathError):
            app_files.list_tree(ws, "/etc")

    def test_absolute_path_refused_on_read(self, ws):
        with pytest.raises(app_files.AppFilePathError):
            app_files.read_file(ws, "/etc/passwd")

    def test_absolute_path_refused_on_write(self, ws):
        with pytest.raises(app_files.AppFilePathError):
            app_files.write_file(ws, "/etc/evil.txt", "x")

    def test_symlink_escape_refused_on_read(self, root, ws, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside")
        secret = outside / "secret.txt"
        secret.write_text("do not read\n")
        link = root / "escape_link.txt"
        link.symlink_to(secret)
        with pytest.raises(app_files.AppFilePathError):
            app_files.read_file(ws, "escape_link.txt")

    def test_symlink_escape_refused_on_write(self, root, ws, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside2")
        link = root / "escape_dir"
        link.symlink_to(outside)
        with pytest.raises(app_files.AppFilePathError):
            app_files.write_file(ws, "escape_dir/evil.txt", "x")
        assert not (outside / "evil.txt").exists()

    def test_symlink_escape_refused_on_list(self, root, ws, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside3")
        (outside / "secret.txt").write_text("shh\n")
        link = root / "escape_dir2"
        link.symlink_to(outside)
        with pytest.raises(app_files.AppFilePathError):
            app_files.list_tree(ws, "escape_dir2")

    def test_a_symlink_escape_cannot_forge_writability(self, root, tmp_path_factory):
        """A read-only workspace plus a symlink named `docs` is still refused
        by containment BEFORE the write rule is consulted, so a symlink can
        never dress an outside location up as a writable one."""
        outside = tmp_path_factory.mktemp("outside4")
        (root / "docs").symlink_to(outside)
        frozen = app_files.workspace_at(root, "workshop", write_prefixes=("docs",))
        with pytest.raises(app_files.AppFilePathError):
            app_files.write_file(frozen, "docs/evil.md", "x")
        assert not (outside / "evil.md").exists()

    def test_root_itself_is_not_an_escape(self, ws):
        # Sanity check the containment isn't over-eager: the root path itself
        # (path="") must resolve fine, not raise.
        result = app_files.list_tree(ws, "")
        assert result["path"] == ""


class TestResolveWorkspace:
    """The root comes from the resolver, and the resolver composes the two
    existing single-sources (`app_links.linked_project_slug` and
    `build_agents._project_dir`) rather than adding a third mapping."""

    @pytest.fixture
    def registry(self, tmp_path, monkeypatch):
        proj = tmp_path / "trees" / "widgets"
        proj.mkdir(parents=True)
        (proj / "README.md").write_text("# widgets\n")
        reg = tmp_path / "projects.json"
        reg.write_text(json.dumps({"projects": {"widgets": {"dir": str(proj)}}}))
        monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(reg))
        monkeypatch.setattr(
            "services.installed_apps.installed_app_ids", lambda: [], raising=False)
        return proj

    def test_project_ref_resolves_to_the_registered_directory(self, registry):
        ws = app_files.resolve("project:widgets")
        assert ws.root == registry
        assert ws.write_prefixes is None
        assert [e["name"] for e in app_files.list_tree(ws, "")["entries"]] == ["README.md"]

    def test_app_ref_resolves_through_the_link(self, registry):
        # `widgets` is a bare project slug with no catalog entry, which
        # `app_links.linked_project_slug` links to itself.
        ws = app_files.resolve("app:widgets")
        assert ws.root == registry

    def test_an_app_with_no_tree_anywhere_still_gets_its_own_root(
            self, registry, tmp_path, monkeypatch):
        # It used to raise "no linked project", and app_surface caught that and
        # dropped the app's Files tab, so five of eight apps had one and three
        # did not for a reason invisible on screen. The operator: "all apps have
        # files obviously". Every app resolves to the app tree's own directory,
        # made on demand, which is the root its memory already uses.
        #
        # WORKSHOP_ROOT IS PATCHED, and the assertion that it landed under
        # tmp_path is the point of the test as much as the resolve is: this is
        # the first thing in app_files that CREATES a directory, and the first
        # version of this test ran without the patch and made `apps/nosuchthing`
        # in the live repo. A test that writes outside its tmp_path is a test
        # that changes the tree it is judging.
        monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
        ws = app_files.resolve("app:nosuchthing")
        assert ws.root == tmp_path / "apps" / "nosuchthing"
        assert ws.root.is_dir()

    def test_a_project_with_no_directory_reports_why(self, registry):
        with pytest.raises(app_files.WorkspaceNotFound) as e:
            app_files.resolve("project:ghost")
        assert "no directory registered" in str(e.value)

    def test_documents_workspace_is_the_repo_with_docs_writable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
        ws = app_files.resolve("documents")
        assert ws.root == tmp_path
        assert ws.write_prefixes == ("docs",)
        assert app_files.is_writable(ws, "docs/plan.md") is True
        assert app_files.is_writable(ws, "README.md") is False

    def test_an_unknown_kind_is_refused_not_defaulted(self):
        with pytest.raises(app_files.WorkspaceNotFound):
            app_files.resolve("nonsense:whatever")

    def test_an_empty_reference_is_refused(self):
        with pytest.raises(app_files.WorkspaceNotFound):
            app_files.resolve("")
