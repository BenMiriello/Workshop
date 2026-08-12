"""Vault access layer for the Notes app (native Workshop build).

The vault is a plain folder of UTF-8 Markdown files (`.md`) with Obsidian /
SilverBullet semantics: `[[wikilinks]]`, arbitrary subfolders, no lock-in. This
module is the only place that touches the filesystem; `routes/notes.py` is a thin
HTTP surface over these functions.

Every path that comes from a request is contained to the vault root (resolve +
`is_relative_to`, `.md`-only, no dot-dirs, no traversal) so a caller can never
read or write outside the vault. Writes are atomic (temp file + `os.replace`) so
an external editor never observes a half-written file.

The operator's vault lives at `$WORKSHOP_NOTES_VAULT` (default `~/notes`) —
durable user data outside the (gitignored) repo working tree, independently
backup-able, and openable in real Obsidian untouched. Every OTHER owner gets
their own vault under `.runtime/users/<owner_id>/notes/`; see `vault_root`.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path


def operator_vault_root() -> Path:
    """The operator's own vault: `$WORKSHOP_NOTES_VAULT`, default `~/notes`.

    Kept as a plain folder in the operator's home, outside `.runtime`, because
    real Obsidian is pointed at it — moving it under the per-owner runtime tree
    would break an editor this module deliberately does not own.
    """
    env = os.environ.get("WORKSHOP_NOTES_VAULT")
    base = Path(env) if env else Path.home() / "notes"
    root = base.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def vault_root() -> Path:
    """The current owner's vault directory, created on demand.

    The single chokepoint: every read, write, search, rename and delete in this
    module resolves through here, so partitioning here partitions the whole app.
    It used to return one global directory for everyone, and `routes/notes.py`
    mounts under nothing but `require_user` (`app_registry.py`) — so any
    authenticated account could read, overwrite, rename and delete every note in
    the operator's vault. Not a bypass of a weak check; there was no check.

    - operator (or no owner pinned: a background pass or a test, which
      `ownership.py` already defines as the operator's context) -> the vault
      above, unmoved.
    - any other owner -> `.runtime/users/<owner_id>/notes/`, the same partition
      `chat_store` and the other per-owner stores use.

    Note this is where the vault DIVERGES from `permissions.NOTES_VAULT_ROOT`,
    which names the operator's vault specifically for the instance-level Store
    declaration (`services/stores.py`). See
    docs/security-audit-2026-08-11-scope-separation.md section 3.
    """
    from services import ownership
    if ownership.caller_is_operator() or ownership.owner_or_none() is None:
        return operator_vault_root()
    root = (ownership.user_runtime_dir() / "notes").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


# Target only: `[[target]]`, `[[target|alias]]`, `[[target#heading]]`.
_WIKILINK = re.compile(r"\[\[([^\]\|#\n]+)(?:#[^\]\|\n]*)?(?:\|[^\]\n]*)?\]\]")
# Full form with heading/alias captured, for link rewriting on rename.
_WIKILINK_FULL = re.compile(r"\[\[([^\]\|#\n]+)(#[^\]\|\n]*)?(\|[^\]\n]*)?\]\]")


class VaultPathError(ValueError):
    """Raised when a request path escapes the vault or is otherwise unsafe."""


@dataclass
class Link:
    raw: str              # target text as written in the [[...]]
    target: str           # normalized vault-relative target the raw resolves against
    resolved: str | None  # vault-relative .md path if it exists, else None


def _is_hidden(rel: Path) -> bool:
    """True if any path component is dot-prefixed (`.obsidian/`, `.git/`, ...)."""
    return any(part.startswith(".") for part in rel.parts)


def safe_path(raw: str, root: Path | None = None) -> Path:
    """Resolve a request-supplied note path to an absolute path inside the vault.

    Appends `.md` when missing; rejects absolute paths, traversal, non-`.md`
    extensions, and dot-dir components. Does not require the file to exist.
    """
    root = root or vault_root()
    if raw is None:
        raise VaultPathError("empty note path")
    candidate = raw.strip().lstrip("/")
    if not candidate:
        raise VaultPathError("empty note path")
    if not candidate.endswith(".md"):
        candidate = f"{candidate}.md"
    abs_path = (root / candidate).resolve()
    if not abs_path.is_relative_to(root):
        raise VaultPathError(f"path escapes vault: {raw}")
    rel = abs_path.relative_to(root)
    if _is_hidden(rel):
        raise VaultPathError(f"hidden path not allowed: {raw}")
    if abs_path.suffix != ".md":
        raise VaultPathError(f"only .md notes are allowed: {raw}")
    return abs_path


def _iter_notes(root: Path):
    """Yield (abs_path, rel_posix) for every non-hidden `.md` file in the vault."""
    for abs_path in root.rglob("*.md"):
        if not abs_path.is_file():
            continue
        rel = abs_path.relative_to(root)
        if _is_hidden(rel):
            continue
        yield abs_path, rel.as_posix()


_FRONTMATTER = re.compile(r"\A﻿?---\n.*?\n---\n", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block (`--- … ---`). It is metadata
    (dates, etc.), never part of the note's visible body or its name."""
    m = _FRONTMATTER.match(text)
    return text[m.end():] if m else text


def _title_of(text: str, rel_posix: str) -> str:
    """A note's name is its first non-empty line (after any frontmatter), with a
    leading `#` heading marker and wrapping emphasis stripped. Empty note falls
    back to the filename stem. This is the single source of the name."""
    for line in strip_frontmatter(text).splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^#{1,6}\s+", "", s)              # a leading heading marker
        s = re.sub(r"^[*_~`]+|[*_~`]+$", "", s).strip()  # wrapping emphasis
        if s:
            return s[:120]
    return Path(rel_posix).stem


def _sanitize_name(name: str) -> str:
    """Turn a note name into a safe single filename component (no extension)."""
    name = (name or "").strip()
    name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]", " ", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return (name or "Untitled")[:120]


def list_notes(root: Path | None = None) -> list[dict]:
    """All notes with cheap metadata, sorted by path."""
    root = root or vault_root()
    out = []
    for abs_path, rel in _iter_notes(root):
        try:
            text = abs_path.read_text(encoding="utf-8")
        except OSError:
            continue
        st = abs_path.stat()
        out.append(
            {"path": rel, "title": _title_of(text, rel), "size": st.st_size, "modified": st.st_mtime}
        )
    out.sort(key=lambda n: n["path"])
    return out


def _resolve_target(raw_target: str, root: Path, by_basename: dict[str, list[str]]) -> str | None:
    """Resolve a wikilink target to a vault-relative `.md` path, or None.

    Exact vault-relative match first (with or without `.md`); else a unique
    case-insensitive basename match anywhere in the vault. Ambiguous/missing => None.
    """
    target = raw_target.strip()
    if not target:
        return None
    for candidate in (target, f"{target}.md"):
        try:
            abs_path = (root / candidate).resolve()
        except (OSError, ValueError):
            continue
        if abs_path.is_relative_to(root) and abs_path.is_file() and abs_path.suffix == ".md":
            rel = abs_path.relative_to(root)
            if not _is_hidden(rel):
                return rel.as_posix()
    stem = Path(target).name.lower()
    if stem.endswith(".md"):
        stem = stem[:-3]
    matches = by_basename.get(stem)
    if matches and len(matches) == 1:
        return matches[0]
    return None


def _basename_index(root: Path) -> dict[str, list[str]]:
    """lowercase-stem -> list of vault-relative paths, for basename link resolution."""
    index: dict[str, list[str]] = {}
    for _abs, rel in _iter_notes(root):
        index.setdefault(Path(rel).stem.lower(), []).append(rel)
    return index


def outgoing_links(text: str, root: Path, index: dict[str, list[str]] | None = None) -> list[Link]:
    """Parse and resolve every wikilink in `text`."""
    root = root or vault_root()
    index = index if index is not None else _basename_index(root)
    links = []
    for match in _WIKILINK.finditer(text):
        raw = match.group(1).strip()
        links.append(Link(raw=raw, target=raw, resolved=_resolve_target(raw, root, index)))
    return links


def read_note(note_path: str, root: Path | None = None) -> dict:
    """Read one note plus its resolved outgoing links. FileNotFoundError if absent."""
    root = root or vault_root()
    abs_path = safe_path(note_path, root)
    if not abs_path.is_file():
        raise FileNotFoundError(note_path)
    text = abs_path.read_text(encoding="utf-8")
    rel = abs_path.relative_to(root).as_posix()
    st = abs_path.stat()
    links = outgoing_links(text, root)
    return {
        "path": rel,
        "title": _title_of(text, rel),
        "content": text,
        "modified": st.st_mtime,
        "links": [{"raw": link.raw, "target": link.target, "resolved": link.resolved} for link in links],
    }


def _atomic_write(abs_path: Path, content: str) -> None:
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=abs_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, abs_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_result(abs_path: Path, root: Path, content: str) -> dict:
    rel = abs_path.relative_to(root).as_posix()
    return {"path": rel, "title": _title_of(content, rel), "modified": abs_path.stat().st_mtime}


def create_note(note_path: str, content: str, root: Path | None = None) -> dict:
    """Create a new note. FileExistsError if it already exists."""
    root = root or vault_root()
    abs_path = safe_path(note_path, root)
    if abs_path.exists():
        raise FileExistsError(note_path)
    _atomic_write(abs_path, content)
    return _write_result(abs_path, root, content)


def update_note(note_path: str, content: str, root: Path | None = None) -> dict:
    """Overwrite an existing note, then sync its filename to its first-line name.

    The name lives in one place: the note's first line. When editing it changes
    the name, the file is renamed to match (in the same folder, de-colliding with
    a numeric suffix) and referring wikilinks are rewritten. FileNotFoundError if
    the note does not exist.
    """
    root = root or vault_root()
    abs_path = safe_path(note_path, root)
    if not abs_path.is_file():
        raise FileNotFoundError(note_path)
    _atomic_write(abs_path, content)

    target = _name_synced_path(abs_path, content, root)
    if target is not None and target != abs_path:
        result = _move_and_rewrite(abs_path, target, root)
        return {"path": result["path"], "title": result["title"], "modified": result["modified"]}
    return _write_result(abs_path, root, content)


def _name_synced_path(abs_path: Path, content: str, root: Path) -> Path | None:
    """The path whose stem matches the note's first-line name, in the same folder.
    None when the current filename already matches (no rename needed)."""
    rel = abs_path.relative_to(root).as_posix()
    desired_stem = _sanitize_name(_title_of(content, rel))
    if desired_stem == abs_path.stem:
        return None
    parent = abs_path.parent
    candidate = parent / f"{desired_stem}.md"
    n = 2
    while candidate.exists() and candidate != abs_path:
        candidate = parent / f"{desired_stem} {n}.md"
        n += 1
    return candidate


def _prune_empty_dirs(start: Path, root: Path) -> None:
    """Remove now-empty directories from `start` up to (not including) `root`."""
    d = start
    while d != root and root in d.parents:
        if not d.is_dir() or any(d.iterdir()):
            break
        try:
            d.rmdir()
        except OSError:
            break
        d = d.parent


def delete_note(note_path: str, root: Path | None = None) -> dict:
    """Delete a note and prune emptied parent dirs. FileNotFoundError if absent."""
    root = root or vault_root()
    abs_path = safe_path(note_path, root)
    if not abs_path.is_file():
        raise FileNotFoundError(note_path)
    rel = abs_path.relative_to(root).as_posix()
    os.remove(abs_path)
    _prune_empty_dirs(abs_path.parent, root)
    return {"path": rel, "deleted": True}


def rename_note(old_path: str, new_path: str, root: Path | None = None) -> dict:
    """Move a note and rewrite every wikilink that resolved to it.

    FileNotFoundError if the source is missing; FileExistsError if the destination
    exists. Referring `[[targets]]` are rewritten to the new vault-relative path
    (sans `.md`), preserving any `#heading` / `|alias`. Returns the write result
    plus `links_updated` and `notes_rewritten` counts.
    """
    root = root or vault_root()
    old_abs = safe_path(old_path, root)
    new_abs = safe_path(new_path, root)
    if not old_abs.is_file():
        raise FileNotFoundError(old_path)
    if new_abs.exists():
        raise FileExistsError(new_path)
    return _move_and_rewrite(old_abs, new_abs, root)


def _move_and_rewrite(old_abs: Path, new_abs: Path, root: Path) -> dict:
    """Move old_abs -> new_abs and rewrite wikilinks that resolved to it. The
    caller owns existence/collision checks. Preserves file mtime across the move."""
    old_rel = old_abs.relative_to(root).as_posix()
    new_rel = new_abs.relative_to(root).as_posix()
    new_target_text = new_rel[:-3] if new_rel.endswith(".md") else new_rel
    prior = old_abs.stat()

    # Resolve referrers against the CURRENT state, before the move.
    index = _basename_index(root)
    pending: list[tuple[Path, str]] = []
    links_updated = 0
    for abs_path, rel in _iter_notes(root):
        if rel == old_rel:
            continue
        try:
            text = abs_path.read_text(encoding="utf-8")
        except OSError:
            continue
        hits = [0]

        def _repl(m: re.Match) -> str:
            target = m.group(1).strip()
            if _resolve_target(target, root, index) == old_rel:
                hits[0] += 1
                heading = m.group(2) or ""
                alias = m.group(3) or ""
                return f"[[{new_target_text}{heading}{alias}]]"
            return m.group(0)

        new_text = _WIKILINK_FULL.sub(_repl, text)
        if hits[0]:
            pending.append((abs_path, new_text))
            links_updated += hits[0]

    new_abs.parent.mkdir(parents=True, exist_ok=True)
    os.replace(old_abs, new_abs)
    os.utime(new_abs, (prior.st_atime, prior.st_mtime))  # keep the real edited date
    _prune_empty_dirs(old_abs.parent, root)
    for abs_path, new_text in pending:
        _atomic_write(abs_path, new_text)

    content = new_abs.read_text(encoding="utf-8")
    return {
        "path": new_rel,
        "title": _title_of(content, new_rel),
        "modified": new_abs.stat().st_mtime,
        "links_updated": links_updated,
        "notes_rewritten": len(pending),
    }


def search(query: str, limit: int = 50, root: Path | None = None) -> list[dict]:
    """Case-insensitive full-text search; one result per matching line."""
    root = root or vault_root()
    needle = query.lower()
    results = []
    for abs_path, rel in _iter_notes(root):
        try:
            text = abs_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                results.append({"path": rel, "line": lineno, "snippet": line.strip()[:200]})
                if len(results) >= limit:
                    return results
    return results


def backlinks(note_path: str, root: Path | None = None) -> list[dict]:
    """Notes whose wikilinks resolve to `note_path`. FileNotFoundError if absent."""
    root = root or vault_root()
    target_abs = safe_path(note_path, root)
    if not target_abs.is_file():
        raise FileNotFoundError(note_path)
    target_rel = target_abs.relative_to(root).as_posix()
    index = _basename_index(root)
    out = []
    for abs_path, rel in _iter_notes(root):
        if rel == target_rel:
            continue
        try:
            text = abs_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _WIKILINK.finditer(line):
                raw = match.group(1).strip()
                if _resolve_target(raw, root, index) == target_rel:
                    out.append({"path": rel, "line": lineno, "context": line.strip()[:200]})
                    break
    out.sort(key=lambda b: (b["path"], b["line"]))
    return out
