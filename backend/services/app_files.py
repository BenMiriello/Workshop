"""File access for an app's linked project tree (app-is-the-entity-2026-08-20.md
sec 8: "a full file browser/editor is deliberately deferred to an existing
third-party package" -- this module is the minimum honest backend behind it,
not that package: list a directory, read a file, write a file.

An app's files ARE its linked project's working tree. This module never
invents a second root: `services/app_links.linked_project_slug` is the one
place that computes the app<->project merge (see `routes/apps.py`'s own
docstring: "NOTHING here fabricates data"), and `services/build_agents.
_project_dir` is the one place that turns a project slug into a directory,
off the same `.runtime/projects.json` registry every other per-project route
already reads. No app without both resolves to a tree -- and an app that
fails to resolve one returns None, never a fallback to another app's or the
platform's tree.

PATH CONTAINMENT IS THE WHOLE SECURITY SURFACE (same pattern as
`services/notes_vault.py:safe_path`). Every caller-supplied relative path is
refused outright if absolute, else resolved to a real, symlink-followed
absolute path and checked against the resolved root with `Path.relative_to`.
`..`, an absolute path, and a symlink resolving outside the root all end up
refused by `safe_path`, shared by every read and write below so a write can
never escape in a way a read cannot.

FILTERING. `.git`, `node_modules`, `.venv` and `__pycache__` are dropped from
directory LISTINGS by name -- never from a recursive walk (this module never
does one), and never from reads: a path inside one of these still reads and
writes fine if a caller names it directly. This is a usability filter, not a
security boundary or a hiding of real files (design instruction: "silently
hiding an app's real files is worse" than a noisy list; the ones excluded
here are non-authored/generated, not app content, and a tree that is 40,000
node_modules entries is not usable at all). Unlike `routes/projects.py:
get_tree`, which hides every dot-prefixed entry, ordinary dotfiles
(`.env`, `.gitignore`, `.github/`) are NOT filtered here -- being
dot-prefixed is not a reason to make a file invisible. Each listing reports
which of the filtered names were actually present at that level (`filtered`
in `list_tree`'s result), so the exclusion is visible to the caller rather
than silent.
"""
from __future__ import annotations

import datetime
import os
import tempfile
from pathlib import Path

from services import build_agents

__all__ = [
    "AppFilePathError", "AppFileBinaryError",
    "project_root", "list_tree", "read_file", "write_file",
]

# Noisy, non-authored directories excluded from listings by name. See module
# docstring: a usability filter, not a hiding of real files.
_FILTERED_DIR_NAMES = {".git", "node_modules", ".venv", "__pycache__"}

# Above this size a text file is reported truncated rather than inlined -- the
# caller needs a number it can act on, not a multi-megabyte JSON body.
_MAX_INLINE_BYTES = 2_000_000

# Bytes sniffed for the binary/text decision. A null byte in the first chunk
# is the same cheap heuristic git itself uses; a full-file scan buys nothing
# a truncated read of the whole file wouldn't also need.
_SNIFF_BYTES = 8000


class AppFilePathError(ValueError):
    """Raised when a request path is absolute, escapes the app's file root
    (via `..` or a symlink), or is otherwise not a usable relative path."""


class AppFileBinaryError(ValueError):
    """Raised when a write would create or overwrite a non-text file through
    this text-only endpoint."""


def project_root(project_slug: str) -> Path | None:
    """The working-tree directory for a project slug, or None if the
    registry has no directory for it. The one place this module reaches
    outside itself for "where is the tree", so it can never drift from what
    `routes/projects.py` and `services/task_scope.py` already resolve to."""
    return build_agents._project_dir(project_slug)


def _iso_mtime(ts: float) -> str:
    return datetime.datetime.fromtimestamp(
        ts, tz=datetime.timezone.utc).isoformat(timespec="seconds")


def safe_path(root: Path, raw: str) -> Path:
    """Resolve a request-supplied relative path against `root`, or raise
    `AppFilePathError`. An absolute path is refused outright (not
    reinterpreted as relative); `..` traversal and a symlink that resolves
    outside `root` are both caught by the same containment check: the
    resolved candidate must be `root` itself or sit under it. Does not
    require the target to exist."""
    root = root.resolve()
    raw = raw or ""
    if raw.startswith("/") or raw.startswith("\\"):
        raise AppFilePathError(f"absolute paths are refused: {raw!r}")
    stripped = raw.strip()
    candidate = (root / stripped).resolve() if stripped else root
    try:
        candidate.relative_to(root)
    except ValueError:
        raise AppFilePathError(f"path escapes the app root: {raw!r}")
    return candidate


def list_tree(root: Path, display_root: str, path: str) -> dict:
    """One level of `root/path`'s children, dirs-first then name.

    Raises `AppFilePathError` if `path` escapes `root`, `FileNotFoundError`
    if the resolved directory doesn't exist, `NotADirectoryError` if it
    exists but names a file. The caller (`services/apps.py`) owns the "this
    app has no root at all" case; this function only ever sees a real root.
    """
    target = safe_path(root, path)
    if not target.exists():
        raise FileNotFoundError(path)
    if not target.is_dir():
        raise NotADirectoryError(path)

    entries: list[dict] = []
    filtered_here: list[str] = []
    for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name)):
        is_dir = child.is_dir()
        if is_dir and child.name in _FILTERED_DIR_NAMES:
            filtered_here.append(child.name)
            continue
        rel = child.relative_to(root).as_posix()
        try:
            st = child.stat()
            size = None if is_dir else st.st_size
            modified = _iso_mtime(st.st_mtime)
        except OSError:
            size = None
            modified = None
        entries.append({
            "name": child.name,
            "path": rel,
            "kind": "dir" if is_dir else "file",
            "size": size,
            "modified": modified,
        })

    result = {
        "path": "" if target == root else target.relative_to(root).as_posix(),
        "root": display_root,
        "entries": entries,
    }
    if filtered_here:
        result["filtered"] = sorted(filtered_here)
    return result


def _sniff_binary(target: Path) -> bytes:
    with target.open("rb") as fh:
        return fh.read(_SNIFF_BYTES)


def read_file(root: Path, path: str) -> dict:
    """One file's content plus display metadata. A binary file or a file over
    `_MAX_INLINE_BYTES` reports `editable=False`, `content=""` and either
    `encoding="binary"` or `truncated=True` -- NEVER an error, so the caller
    can render "this file cannot be shown here" without a failed request.

    Raises `AppFilePathError` on an escaping/empty path, `FileNotFoundError`
    if the resolved file doesn't exist, `IsADirectoryError` if it names a
    directory.
    """
    if not (path or "").strip():
        raise AppFilePathError("path is required")
    target = safe_path(root, path)
    if not target.exists():
        raise FileNotFoundError(path)
    if target.is_dir():
        raise IsADirectoryError(path)

    rel = target.relative_to(root).as_posix()
    st = target.stat()
    modified = _iso_mtime(st.st_mtime)

    if st.st_size > _MAX_INLINE_BYTES:
        chunk = _sniff_binary(target)
        if b"\x00" in chunk:
            return {"path": rel, "content": "", "size": st.st_size, "modified": modified,
                    "editable": False, "truncated": False, "encoding": "binary"}
        return {"path": rel, "content": "", "size": st.st_size, "modified": modified,
                "editable": False, "truncated": True, "encoding": "utf-8"}

    raw = target.read_bytes()
    if b"\x00" in raw[:_SNIFF_BYTES]:
        return {"path": rel, "content": "", "size": st.st_size, "modified": modified,
                "editable": False, "truncated": False, "encoding": "binary"}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": rel, "content": "", "size": st.st_size, "modified": modified,
                "editable": False, "truncated": False, "encoding": "binary"}
    return {"path": rel, "content": text, "size": st.st_size, "modified": modified,
            "editable": True, "truncated": False, "encoding": "utf-8"}


def _atomic_write(target: Path, content: str) -> None:
    """Write-temp-then-rename, same pattern as `notes_vault._atomic_write`."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_file(root: Path, path: str, content: str) -> dict:
    """Overwrite (or create) one text file, atomically. Refuses to write
    embedded-null content, and refuses to overwrite a file that is already
    binary -- this is a text endpoint, not a general file upload.

    Raises `AppFilePathError` on an escaping/empty path, `IsADirectoryError`
    if the path names an existing directory, `AppFileBinaryError` if the
    write would touch a non-text file.
    """
    if not (path or "").strip():
        raise AppFilePathError("path is required")
    target = safe_path(root, path)
    if target.is_dir():
        raise IsADirectoryError(path)
    if "\x00" in content:
        raise AppFileBinaryError(
            "refusing to write null-byte content through the text file endpoint")
    if target.exists() and b"\x00" in _sniff_binary(target):
        raise AppFileBinaryError(
            "refusing to overwrite a binary file through the text file endpoint")

    _atomic_write(target, content)
    st = target.stat()
    return {
        "path": target.relative_to(root).as_posix(),
        "size": st.st_size,
        "modified": _iso_mtime(st.st_mtime),
    }
