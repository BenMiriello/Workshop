"""Unit tests for `services/app_files.py` directly against the filesystem,
no database and no HTTP layer required -- this is where the path-containment
logic actually lives, so it should be provable on its own. The HTTP-level
tests (including the wire-level escape tests) are in `test_apps_files_api.py`.
"""
from __future__ import annotations

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


class TestListTree:
    def test_lists_root_dirs_first_and_filters_noise_dirs(self, root):
        result = app_files.list_tree(root, "myapp", "")
        names = [e["name"] for e in result["entries"]]
        assert "node_modules" not in names
        assert ".git" not in names
        assert "src" in names
        assert "README.md" in names
        assert sorted(result["filtered"]) == [".git", "node_modules"]
        kinds = [e["kind"] for e in result["entries"]]
        assert kinds == sorted(kinds)  # dirs ("dir" < "file") before files

    def test_lists_a_subdirectory(self, root):
        result = app_files.list_tree(root, "myapp", "src")
        assert result["path"] == "src"
        assert [e["name"] for e in result["entries"]] == ["main.py"]

    def test_missing_subpath_raises_file_not_found(self, root):
        with pytest.raises(FileNotFoundError):
            app_files.list_tree(root, "myapp", "nope")

    def test_a_file_path_raises_not_a_directory(self, root):
        with pytest.raises(NotADirectoryError):
            app_files.list_tree(root, "myapp", "README.md")


class TestReadFile:
    def test_reads_text(self, root):
        result = app_files.read_file(root, "README.md")
        assert result["content"] == "# hello\n"
        assert result["editable"] is True
        assert result["encoding"] == "utf-8"

    def test_binary_file_is_not_editable_not_an_error(self, root):
        result = app_files.read_file(root, "binary.dat")
        assert result["editable"] is False
        assert result["content"] == ""
        assert result["encoding"] == "binary"

    def test_oversized_text_file_is_truncated_not_an_error(self, root, monkeypatch):
        monkeypatch.setattr(app_files, "_MAX_INLINE_BYTES", 10)
        big = root / "big.txt"
        big.write_text("x" * 1000)
        result = app_files.read_file(root, "big.txt")
        assert result["editable"] is False
        assert result["truncated"] is True
        assert result["encoding"] == "utf-8"

    def test_missing_file_raises(self, root):
        with pytest.raises(FileNotFoundError):
            app_files.read_file(root, "nope.txt")

    def test_directory_path_raises_is_a_directory(self, root):
        with pytest.raises(IsADirectoryError):
            app_files.read_file(root, "src")


class TestWriteFile:
    def test_write_then_read_round_trips(self, root):
        out = app_files.write_file(root, "src/new.py", "y = 2\n")
        assert out["path"] == "src/new.py"
        assert out["size"] == len("y = 2\n")
        assert app_files.read_file(root, "src/new.py")["content"] == "y = 2\n"

    def test_write_is_atomic_no_tmp_file_left_behind(self, root):
        app_files.write_file(root, "atomic.txt", "hi\n")
        leftovers = list(root.glob("*.tmp"))
        assert leftovers == []

    def test_refuses_null_byte_content(self, root):
        with pytest.raises(app_files.AppFileBinaryError):
            app_files.write_file(root, "evil.txt", "abc\x00def")

    def test_refuses_overwriting_a_binary_file(self, root):
        with pytest.raises(app_files.AppFileBinaryError):
            app_files.write_file(root, "binary.dat", "now text")

    def test_refuses_writing_over_a_directory(self, root):
        with pytest.raises(IsADirectoryError):
            app_files.write_file(root, "src", "nope")


class TestPathContainment:
    """The one part of this feature that is a correctness requirement, not a
    nicety: `..`, an absolute path, and a symlink resolving outside the root
    must all be refused, for every one of list/read/write."""

    def test_dotdot_escape_refused_on_list(self, root):
        with pytest.raises(app_files.AppFilePathError):
            app_files.list_tree(root, "myapp", "../")

    def test_dotdot_escape_refused_on_read(self, root):
        (root.parent / "outside.txt").write_text("secret\n")
        with pytest.raises(app_files.AppFilePathError):
            app_files.read_file(root, "../outside.txt")

    def test_dotdot_escape_refused_on_write(self, root):
        with pytest.raises(app_files.AppFilePathError):
            app_files.write_file(root, "../../tmp/evil.txt", "x")

    def test_absolute_path_refused_on_list(self, root):
        with pytest.raises(app_files.AppFilePathError):
            app_files.list_tree(root, "myapp", "/etc")

    def test_absolute_path_refused_on_read(self, root):
        with pytest.raises(app_files.AppFilePathError):
            app_files.read_file(root, "/etc/passwd")

    def test_absolute_path_refused_on_write(self, root):
        with pytest.raises(app_files.AppFilePathError):
            app_files.write_file(root, "/etc/evil.txt", "x")

    def test_symlink_escape_refused_on_read(self, root, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside")
        secret = outside / "secret.txt"
        secret.write_text("do not read\n")
        link = root / "escape_link.txt"
        link.symlink_to(secret)
        with pytest.raises(app_files.AppFilePathError):
            app_files.read_file(root, "escape_link.txt")

    def test_symlink_escape_refused_on_write(self, root, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside2")
        link = root / "escape_dir"
        link.symlink_to(outside)
        with pytest.raises(app_files.AppFilePathError):
            app_files.write_file(root, "escape_dir/evil.txt", "x")
        assert not (outside / "evil.txt").exists()

    def test_symlink_escape_refused_on_list(self, root, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside3")
        (outside / "secret.txt").write_text("shh\n")
        link = root / "escape_dir2"
        link.symlink_to(outside)
        with pytest.raises(app_files.AppFilePathError):
            app_files.list_tree(root, "myapp", "escape_dir2")

    def test_root_itself_is_not_an_escape(self, root):
        # Sanity check the containment isn't over-eager: the root path itself
        # (path="") must resolve fine, not raise.
        result = app_files.list_tree(root, "myapp", "")
        assert result["path"] == ""
