"""The one file API: list a directory, read a file, write a file, under a
RESOLVED WORKSPACE ROOT (file-viewer-and-editor-2026-08-22.md sec 6 -- "one
file API, reached through a workspace root rather than an app id, built by
generalizing services/app_files.py"). It is the minimum honest backend behind
a file browser/editor, not the editor package itself.

WHAT A WORKSPACE IS. A `Workspace` is a root directory plus the rule for
which paths under it may be WRITTEN. `resolve` turns a caller-supplied
reference (`app:<slug>`, `project:<slug>`, `documents`) into one, or raises
`WorkspaceNotFound` carrying the reason -- never a fallback to another
workspace's or the platform's tree. Adding a kind is a row in `_RESOLVERS`,
not an `if` in a route.

THIS MODULE STILL NEVER INVENTS A SECOND ROOT. `services/app_links.
linked_project_slug` remains the one place that computes the app<->project
merge (see `routes/apps.py`'s own docstring: "NOTHING here fabricates data"),
and `services/build_agents._project_dir` remains the one place that turns a
project slug into a directory, off the same `.runtime/projects.json` registry
every other per-project route already reads. The `app:` resolver composes
those two; it does not re-derive either.

PATH CONTAINMENT IS THE WHOLE SECURITY SURFACE (same pattern as
`services/notes_vault.py:safe_path`). Every caller-supplied relative path is
refused outright if absolute, else resolved to a real, symlink-followed
absolute path and checked against the resolved root with `Path.relative_to`.
`..`, an absolute path, and a symlink resolving outside the root all end up
refused by `safe_path`, shared by every read and write below so a write can
never escape in a way a read cannot.

WRITABILITY IS DECIDED HERE, PER PATH, AND SERVED. A read reports `writable`
so the frontend stops guessing -- the `path.startsWith("docs/")` rule that
lived in `frontend/src/pages/Memory.tsx` is a workspace's `write_prefixes`
now, not a frontend fact. It is not decoration: `write_file` enforces the
same predicate and raises `AppFileReadOnlyError`, so a client that ignores
`writable` is refused rather than obeyed. Writability is computed from the
CONTAINED relative path (post-`safe_path`), never from the raw request
string, so a symlink cannot dress a read-only location up as a writable one.

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
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from services import build_agents

__all__ = [
    "AppFilePathError", "AppFileBinaryError", "AppFileReadOnlyError",
    "WorkspaceNotFound", "Workspace",
    "resolve", "workspace_at", "is_writable",
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
    """Raised when a request path is absolute, escapes the workspace root
    (via `..` or a symlink), or is otherwise not a usable relative path."""


class AppFileBinaryError(ValueError):
    """Raised when a write would create or overwrite a non-text file through
    this text-only endpoint."""


class AppFileReadOnlyError(PermissionError):
    """Raised when a write targets a contained path the workspace marks
    read-only -- the same predicate a read reports as `writable: false`."""


class WorkspaceNotFound(LookupError):
    """Raised when a workspace reference names nothing this instance has.
    Carries the human reason, because "this app has no linked project" and
    "there is no such workspace kind" are different answers and the caller
    renders them differently."""


# --- workspaces ---------------------------------------------------------------

@dataclass(frozen=True)
class Workspace:
    """A resolved file root plus its write rule.

    `write_prefixes` is None when everything under `root` is writable, and a
    tuple of root-relative directory prefixes when only part of it is. An
    EMPTY tuple means the whole workspace is read-only -- that is a real
    state, not a mistake, so there is no separate flag to keep in sync."""
    ref: str
    root: Path
    display: str
    write_prefixes: tuple[str, ...] | None = None


def workspace_at(root: Path, display: str = "", ref: str = "",
                 write_prefixes: tuple[str, ...] | None = None) -> Workspace:
    """A workspace over an already-known directory. For callers that have
    resolved a root by other means (and for tests); `resolve` is how a
    reference from the wire becomes one."""
    root = Path(root)
    return Workspace(ref=ref or f"path:{root}", root=root,
                     display=display or root.name,
                     write_prefixes=write_prefixes)


def project_root(project_slug: str) -> Path | None:
    """The working-tree directory for a project slug, or None if the
    registry has no directory for it. The one place this module reaches
    outside itself for "where is the tree", so it can never drift from what
    `routes/projects.py` and `services/task_scope.py` already resolve to."""
    return build_agents._project_dir(project_slug)


def _resolve_project(slug: str, ref: str) -> Workspace:
    if not slug:
        return _fail(f"{ref!r} names no project")
    root = project_root(slug)
    if root is None:
        return _fail(f"the project {slug!r} has no directory registered")
    if not root.is_dir():
        return _fail(f"the project directory does not exist on disk: {root}")
    return Workspace(ref=ref, root=root, display=root.name)


def _resolve_app(slug: str, ref: str) -> Workspace:
    """The app's own tree when it declares one, else its linked project's.

    THE APP DECLARES, THE PLATFORM READS (CLAUDE.md: "An app declares what it
    is and the platform reads the declaration"). Two candidate directories
    exist for one app while the registries are still unmerged
    (app-is-the-entity-2026-08-20.md sec 9): `apps/<slug>`, which
    `paths.scope_root` names and the knowledge store already uses, and
    `projects/<slug>`, the build tree. Which one holds the app is NOT uniform
    and cannot be guessed, measured 2026-08-23 by counting both trees:

        birds      apps/ 30150 files   projects/    41   (a scaffold)
        gengui     apps/     0         projects/  1754
        notes-app  apps/     0         projects/    24
        budget     apps/     0         projects/     9
        location   apps/     0         projects/     8
        commute    apps/     3         projects/    10   (split across both)

    So this used to say "an app's files ARE its linked project's working tree",
    which is right for five of six and left the operator's largest app -- 2.2 GB
    of it -- unreachable from its own Files tab, and from anywhere in the UI.
    An emptiness or existence heuristic gets `commute` wrong in the other
    direction, so there is no rule to infer: the app has to say.

    It says it with a manifest in its own tree, read through
    `app_runner._read_manifest_file`, which is the ONE reader of that file and
    already owns the filename set and the jsonc tolerance. A manifest there
    means "this directory is the app". No manifest means the previous answer,
    unchanged, so nothing that worked before moves.
    """
    from services import app_links

    if not slug:
        return _fail(f"{ref!r} names no app")

    own = _app_declared_root(slug)
    if own is not None:
        return Workspace(ref=ref, root=own, display=own.name)

    project_slug = app_links.linked_project_slug(slug)
    if not project_slug:
        return _fail("this app has no linked project, so it has no file tree")
    root = project_root(project_slug)
    if root is None:
        return _fail(
            f"the app's linked project {project_slug!r} has no directory registered")
    if not root.is_dir():
        return _fail(
            f"the app's linked project directory does not exist on disk: {root}")
    return Workspace(ref=ref, root=root, display=root.name)



def _app_declared_root(slug: str) -> Path | None:
    """`apps/<slug>` when the app declares itself there with a manifest, else
    None. Never invents a root: a directory with no manifest is not a claim."""
    from services import app_runner
    from wsplatform import paths

    try:
        d = paths.scope_root(f"app:{slug}")
    except (ValueError, OSError):
        return None
    if not d.is_dir():
        return None
    try:
        manifest = app_runner._read_manifest_file(d)
    except Exception:
        # A malformed manifest is app_runner's error to raise where it is
        # deploying; here it must not take out file browsing for every app.
        return None
    return d if manifest is not None else None


def _resolve_documents(_arg: str, ref: str) -> Workspace:
    """The committed repo tree: readable anywhere, writable only under
    `docs/`. That asymmetry is `routes/docs_index.py`'s existing contract
    ("a write can escape `docs/` in exactly the ways a read cannot -- which
    is none"), stated once here as data instead of twice as code."""
    from wsplatform import paths

    return Workspace(ref=ref, root=paths.workshop_root(),
                     display="workshop", write_prefixes=("docs",))


def _fail(reason: str) -> NoReturn:
    raise WorkspaceNotFound(reason)


# ref kind -> resolver. A new workspace kind is a row here; no route grows an
# `if`, and nothing outside this table decides what a root is.
_RESOLVERS = {
    "app": _resolve_app,
    "project": _resolve_project,
    "documents": _resolve_documents,
}


def resolve(ref: str) -> Workspace:
    """`"<kind>"` or `"<kind>:<arg>"` -> `Workspace`, else `WorkspaceNotFound`
    carrying the reason. Never falls back to another workspace's root."""
    ref = (ref or "").strip()
    if not ref:
        raise WorkspaceNotFound("a workspace reference is required")
    kind, _, arg = ref.partition(":")
    resolver = _RESOLVERS.get(kind.strip())
    if resolver is None:
        raise WorkspaceNotFound(f"no such workspace kind: {kind!r}")
    return resolver(arg.strip(), ref)


def is_writable(ws: Workspace, rel: str) -> bool:
    """Whether `rel` (a root-relative, already-contained posix path) may be
    written in `ws`. The backend's answer to the question the frontend used
    to guess at."""
    if ws.write_prefixes is None:
        return True
    rel = (rel or "").strip("/")
    for prefix in ws.write_prefixes:
        prefix = prefix.strip("/")
        if not prefix:
            return True
        if rel == prefix or rel.startswith(prefix + "/"):
            return True
    return False


# --- the file operations ------------------------------------------------------

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
        raise AppFilePathError(f"path escapes the workspace root: {raw!r}")
    return candidate


def list_tree(ws: Workspace, path: str) -> dict:
    """One level of the workspace's `path`'s children, dirs-first then name.

    Raises `AppFilePathError` if `path` escapes the root, `FileNotFoundError`
    if the resolved directory doesn't exist, `NotADirectoryError` if it
    exists but names a file. The caller owns the "there is no workspace at
    all" case (`resolve` raises `WorkspaceNotFound` for it); this function
    only ever sees a real root.
    """
    root = ws.root.resolve()
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
        "root": ws.display,
        "entries": entries,
    }
    if filtered_here:
        result["filtered"] = sorted(filtered_here)
    return result


def _sniff_binary(target: Path) -> bytes:
    with target.open("rb") as fh:
        return fh.read(_SNIFF_BYTES)


def read_file(ws: Workspace, path: str) -> dict:
    """One file's content plus display metadata, including whether this
    workspace allows writing it back (`writable`). A binary file or a file
    over `_MAX_INLINE_BYTES` reports `editable=False`, `content=""` and
    either `encoding="binary"` or `truncated=True` -- NEVER an error, so the
    caller can render "this file cannot be shown here" without a failed
    request.

    `editable` and `writable` are different questions and both are answered:
    `editable` is about the BYTES (is this text small enough to put in an
    editor), `writable` is about the LOCATION (does this workspace permit a
    write here at all). A read-only markdown file is editable and not
    writable; a 4 MB source file in a writable tree is the other way round.

    Raises `AppFilePathError` on an escaping/empty path, `FileNotFoundError`
    if the resolved file doesn't exist, `IsADirectoryError` if it names a
    directory.
    """
    if not (path or "").strip():
        raise AppFilePathError("path is required")
    root = ws.root.resolve()
    target = safe_path(root, path)
    if not target.exists():
        raise FileNotFoundError(path)
    if target.is_dir():
        raise IsADirectoryError(path)

    rel = target.relative_to(root).as_posix()
    writable = is_writable(ws, rel)
    st = target.stat()
    modified = _iso_mtime(st.st_mtime)
    base = {"path": rel, "size": st.st_size, "modified": modified,
            "writable": writable}

    if st.st_size > _MAX_INLINE_BYTES:
        chunk = _sniff_binary(target)
        if b"\x00" in chunk:
            return {**base, "content": "", "editable": False,
                    "truncated": False, "encoding": "binary"}
        return {**base, "content": "", "editable": False,
                "truncated": True, "encoding": "utf-8"}

    raw = target.read_bytes()
    if b"\x00" in raw[:_SNIFF_BYTES]:
        return {**base, "content": "", "editable": False,
                "truncated": False, "encoding": "binary"}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {**base, "content": "", "editable": False,
                "truncated": False, "encoding": "binary"}
    return {**base, "content": text, "editable": True,
            "truncated": False, "encoding": "utf-8"}


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


def write_file(ws: Workspace, path: str, content: str) -> dict:
    """Overwrite (or create) one text file, atomically. Refuses a path the
    workspace marks read-only, refuses to write embedded-null content, and
    refuses to overwrite a file that is already binary -- this is a text
    endpoint, not a general file upload.

    Raises `AppFilePathError` on an escaping/empty path,
    `AppFileReadOnlyError` if the workspace does not permit writing there,
    `IsADirectoryError` if the path names an existing directory,
    `AppFileBinaryError` if the write would touch a non-text file.
    """
    if not (path or "").strip():
        raise AppFilePathError("path is required")
    root = ws.root.resolve()
    target = safe_path(root, path)
    rel = target.relative_to(root).as_posix()
    if not is_writable(ws, rel):
        raise AppFileReadOnlyError(
            f"{rel!r} is read-only in workspace {ws.ref!r}")
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
        "path": rel,
        "size": st.st_size,
        "modified": _iso_mtime(st.st_mtime),
        "writable": is_writable(ws, rel),
    }
