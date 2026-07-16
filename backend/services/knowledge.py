"""KnowledgeStore — per-scope notes with plaintext markdown as the source of truth.

Every write lands on disk (git-inspectable); recall is a plaintext substring
search over the scope's notes. The Letta vector-archive layer that used to derive
a semantic index from these files was retired in the Stage 9 Letta teardown; the
plaintext store was always authoritative and its search fallback is now the only
path.

Scope model (see `scope_for_context`): one of `workshop`, `project:<slug>`,
`app:<name>`, `agent:<id>`. Each scope maps to a directory holding `MEMORY.md`
(the index) plus one `<note-id>.md` per note.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger("workshop.knowledge")

# App-provider context keys that map onto an `app:<key>` scope.
APP_CONTEXT_KEYS = {"budget", "finance", "commute", "location"}

# note_id: kebab/hex slug, lowercase, no path separators or dots -> no traversal.
_NOTE_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SLUG_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", re.IGNORECASE)

# MEMORY.md index line: "- [Title](note-id.md) - one-line hook"
_INDEX_RE = re.compile(
    r"^-\s+\[(?P<title>.*)\]\((?P<id>[a-z0-9-]+)\.md\)(?:\s+-\s+(?P<hook>.*))?\s*$"
)


# --- roots (env-overridable so tests can point at a temp tree) ---------------
def _root() -> Path:
    return Path(os.environ.get("WORKSHOP_ROOT", "/home/clawd-a/workshop"))


def _runtime() -> Path:
    return Path(os.environ.get("WORKSHOP_RUNTIME", str(_root() / ".runtime")))


# --- scope mapping -----------------------------------------------------------
def _project_exists(slug: str) -> bool:
    return (_root() / "projects" / slug / "knowledge").parent.exists() or (
        _root() / "projects" / slug
    ).exists()


def scope_for_context(context_key: str) -> str:
    """Map a chat context_key to a knowledge scope_key.

    - `general`, `new-project` -> `workshop`
    - `budget`/`finance`/`commute`/`location` -> `app:<key>`
    - `project:<slug>` -> `project:<slug>`
    - `build:<slug>` -> `project:<slug>` if that project exists, else `agent:<slug>`
      (a build agent linked to a project shares the project scope; a freestanding
      one gets its own agent scope)
    - already-scoped `app:<name>` / `agent:<id>` -> passed through
    - any other bare key -> `app:<key>` (treated as an app provider)
    """
    ck = (context_key or "general").strip()
    if ck in ("general", "new-project"):
        return "workshop"
    if ck in APP_CONTEXT_KEYS:
        return f"app:{ck}"
    if ":" in ck:
        prefix, _, slug = ck.partition(":")
        if prefix == "project":
            return f"project:{slug}"
        if prefix == "build":
            return f"project:{slug}" if _project_exists(slug) else f"agent:{slug}"
        if prefix in ("app", "agent"):
            return ck
    return f"app:{ck}"


def _parse_scope(scope_key: str) -> tuple[str, str]:
    """Return (kind, slug). Raises ValueError on an unknown shape."""
    if scope_key == "workshop":
        return ("workshop", "")
    if ":" in scope_key:
        kind, _, slug = scope_key.partition(":")
        if kind in ("project", "app", "agent") and slug and _SLUG_RE.fullmatch(slug):
            return (kind, slug)
    raise ValueError(f"unknown scope_key: {scope_key!r}")


def scope_dir(scope_key: str) -> Path:
    """The knowledge directory for a scope, created on demand.

    - `workshop`       -> shared/knowledge/
    - `project:<slug>` -> projects/<slug>/knowledge/
    - `app:<name>`     -> apps/<name>/knowledge/
    - `agent:<id>`     -> .runtime/agents/<id>/knowledge/
    """
    kind, slug = _parse_scope(scope_key)
    root = _root()
    if kind == "workshop":
        d = root / "shared" / "knowledge"
    elif kind == "project":
        d = root / "projects" / slug / "knowledge"
    elif kind == "app":
        d = root / "apps" / slug / "knowledge"
    else:  # agent
        d = _runtime() / "agents" / slug / "knowledge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _valid_note_id(note_id: str) -> str:
    if not note_id or not _NOTE_ID_RE.fullmatch(note_id):
        raise ValueError(f"invalid note_id: {note_id!r} (kebab/hex slug required)")
    return note_id


# --- atomic file + map IO ----------------------------------------------------
def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


# --- MEMORY.md index ---------------------------------------------------------
def _index_path(scope_key: str) -> Path:
    return scope_dir(scope_key) / "MEMORY.md"


def _read_index(scope_key: str) -> dict:
    """note_id -> {title, hook}. Empty if MEMORY.md is missing."""
    p = _index_path(scope_key)
    out: dict = {}
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        m = _INDEX_RE.match(line)
        if m:
            out[m.group("id")] = {
                "title": m.group("title").strip(),
                "hook": (m.group("hook") or "").strip(),
            }
    return out


def _index_line(note_id: str, title: str, hook: str) -> str:
    base = f"- [{title}]({note_id}.md)"
    return f"{base} - {hook}" if hook else base


def _write_index(scope_key: str, entries: dict) -> None:
    lines = ["# Knowledge index", ""]
    for note_id in sorted(entries):
        e = entries[note_id]
        lines.append(_index_line(note_id, e.get("title") or note_id, e.get("hook") or ""))
    _atomic_write(_index_path(scope_key), "\n".join(lines) + "\n")


# --- index cap (Claude Code 200-line always-loaded rule) ---------------------
# The MEMORY.md index is the always-loaded layer: a fresh worker reads it into
# its stable prefix every turn, and Claude Code loads only the first 200 lines of
# a MEMORY.md, so an unbounded index silently truncates (and bloats the cached
# prefix). enforce_index_cap keeps it bounded by demoting the least-recently-
# referenced notes into a linked archive: their bodies move under an `archive/`
# subdir (out of the top-level note glob, so they leave the ambient index and the
# search corpus) and a single `memory-archive` note catalogs them with links, so
# nothing is lost and the detail stays one hop away (progressive disclosure).
# Purely additive: it adds a function and one best-effort call in put_note, and
# changes no existing signature or search/recall path.
ARCHIVE_NOTE_ID = "memory-archive"
_INDEX_HEADER_LINES = 2  # "# Knowledge index" + blank line, per _write_index


def enforce_index_cap(scope_key: str, max_lines: int = 200) -> dict:
    """Bound a scope's MEMORY.md index to ``max_lines`` total lines. When it is
    over, demote the least-recently-referenced notes (oldest file mtime first)
    into the ``archive/`` subdir and catalog them in the ``memory-archive`` note,
    trimming the index so the always-loaded layer stays bounded. Returns
    {"scope", "demoted", "index_lines"}. A cheap no-op while under the cap."""
    _parse_scope(scope_key)
    entries = _read_index(scope_key)
    total = _INDEX_HEADER_LINES + len(entries)
    if total <= max_lines:
        return {"scope": scope_key, "demoted": 0, "index_lines": total}

    d = scope_dir(scope_key)

    def _recency(note_id: str) -> float:
        f = d / f"{note_id}.md"
        try:
            return f.stat().st_mtime
        except OSError:
            return 0.0

    # The archive note itself is never demoted; leave room for its own index line.
    demotable = sorted((nid for nid in entries if nid != ARCHIVE_NOTE_ID), key=_recency)
    keep = max(0, max_lines - _INDEX_HEADER_LINES - 1)
    to_demote = demotable[: max(0, len(demotable) - keep)]
    if not to_demote:
        return {"scope": scope_key, "demoted": 0, "index_lines": total}

    archive_dir = d / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    catalog_lines: list[str] = []
    for nid in to_demote:
        meta = entries.get(nid) or {}
        title = meta.get("title") or nid
        hook = meta.get("hook") or ""
        src = d / f"{nid}.md"
        if src.exists():
            os.replace(src, archive_dir / f"{nid}.md")
        line = f"- [{title}](archive/{nid}.md)"
        catalog_lines.append(f"{line} - {hook}" if hook else line)
        entries.pop(nid, None)

    # Append (never rewrite) the demoted lines into the archive note body.
    archive_path = d / f"{ARCHIVE_NOTE_ID}.md"
    if archive_path.exists():
        body = archive_path.read_text().rstrip("\n") + "\n"
    else:
        body = ("# Demoted memory archive\n\n"
                "Notes demoted from the always-loaded index to keep it bounded. "
                "Bodies live under archive/; recall them by title when needed.\n\n")
    body += "\n".join(catalog_lines) + "\n"
    _atomic_write(archive_path, body)

    entries[ARCHIVE_NOTE_ID] = {
        "title": "Demoted memory archive",
        "hook": "notes demoted from the index (progressive disclosure)",
    }
    _write_index(scope_key, entries)
    return {"scope": scope_key, "demoted": len(to_demote),
            "index_lines": _INDEX_HEADER_LINES + len(entries)}


# --- note read helpers -------------------------------------------------------
def list_notes(scope_key: str) -> list[dict]:
    """[{id, title, hook, updated}] for the scope. Parses MEMORY.md, falls back
    to a directory scan for any `<id>.md` the index does not mention."""
    _parse_scope(scope_key)
    d = scope_dir(scope_key)
    idx = _read_index(scope_key)
    out: list[dict] = []
    seen: set = set()
    for note_id, meta in idx.items():
        f = d / f"{note_id}.md"
        out.append({
            "id": note_id,
            "title": meta["title"] or note_id,
            "hook": meta["hook"],
            "updated": _mtime_iso(f),
        })
        seen.add(note_id)
    for f in sorted(d.glob("*.md")):
        note_id = f.stem
        if note_id == "MEMORY" or note_id in seen:
            continue
        if not _NOTE_ID_RE.fullmatch(note_id):
            continue
        out.append({"id": note_id, "title": note_id, "hook": "", "updated": _mtime_iso(f)})
    out.sort(key=lambda n: n["id"])
    return out


def _mtime_iso(f: Path) -> str | None:
    if not f.exists():
        return None
    import datetime
    return datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")


def index_block(scope_key: str) -> str:
    """A compact ambient index of the scope's notes for the chat prompt, or ""
    when the scope has no notes. Titles + hooks only (the bodies load on recall),
    so it stays small and cache-stable."""
    try:
        notes = list_notes(scope_key)
    except Exception:
        return ""
    if not notes:
        return ""
    lines = []
    for n in notes:
        hook = f": {n['hook']}" if n.get("hook") else ""
        lines.append(f"- {n['title']} ({n['id']}){hook}")
    return "\n".join(lines)


def get_note(scope_key: str, note_id: str) -> str | None:
    """The body of `<note-id>.md`, or None if absent."""
    _parse_scope(scope_key)
    _valid_note_id(note_id)
    f = scope_dir(scope_key) / f"{note_id}.md"
    return f.read_text() if f.exists() else None


# --- writes ------------------------------------------------------------------
async def put_note(scope_key: str, note_id: str, title: str, content: str, hook: str = "") -> dict:
    """Write a note (source of truth on disk).

    1. write `<note-id>.md` atomically,
    2. upsert the MEMORY.md index line (kept sorted).
    Returns {scope, note_id}.
    """
    _parse_scope(scope_key)
    _valid_note_id(note_id)
    d = scope_dir(scope_key)

    _atomic_write(d / f"{note_id}.md", content)

    idx = _read_index(scope_key)
    idx[note_id] = {"title": (title or note_id).strip(), "hook": (hook or "").strip()}
    _write_index(scope_key, idx)

    # Keep the always-loaded index bounded (Claude Code 200-line rule). Best-effort:
    # a demotion failure must never fail the note write that just succeeded.
    try:
        enforce_index_cap(scope_key)
    except Exception:
        log.debug("index cap enforcement skipped for %s", scope_key, exc_info=True)

    return {"scope": scope_key, "note_id": note_id}


async def delete_note(scope_key: str, note_id: str) -> dict:
    """Remove the note file and its index line."""
    _parse_scope(scope_key)
    _valid_note_id(note_id)
    d = scope_dir(scope_key)

    f = d / f"{note_id}.md"
    existed = f.exists()
    with contextlib.suppress(FileNotFoundError):
        f.unlink()

    idx = _read_index(scope_key)
    if idx.pop(note_id, None) is not None:
        _write_index(scope_key, idx)

    return {"scope": scope_key, "note_id": note_id, "existed": existed}


# --- search + recall ---------------------------------------------------------
async def search(scope_key: str, query: str, limit: int = 5) -> list[dict]:
    """Plaintext substring search within the scope's notes. Each result carries a
    `debug` field ("fallback") for shape-compat with older callers."""
    _parse_scope(scope_key)
    return _plaintext_search(scope_key, query, limit)


def _plaintext_search(scope_key: str, query: str, limit: int) -> list[dict]:
    d = scope_dir(scope_key)
    terms = [t for t in re.split(r"\s+", (query or "").lower()) if t]
    scored: list[tuple[int, dict]] = []
    for note in list_notes(scope_key):
        note_id = note["id"]
        f = d / f"{note_id}.md"
        body = f.read_text() if f.exists() else ""
        hay = f"{note['title']} {note['hook']} {body}".lower()
        score = sum(hay.count(t) for t in terms) if terms else 0
        if score > 0 or (query and query.lower() in hay):
            scored.append((score, {
                "note_id": note_id,
                "text": (f"{note['title']}\n\n{body}").strip(),
                "score": None,
                "debug": "fallback",
            }))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [item for _, item in scored[:limit]]


def _can_recall(from_scope: str, target_scope: str) -> bool:
    """Same scope is always allowed. Cross-scope defers to
    permissions.can_recall when the grants layer provides it; until then,
    cross-scope auto-recall is denied (same-scope-only)."""
    if from_scope == target_scope:
        return True
    try:
        from services import permissions
    except ImportError:
        return False
    fn = getattr(permissions, "can_recall", None)
    if callable(fn):
        return bool(fn(from_scope, target_scope))
    return False


async def recall(from_scope: str, target_scope: str, query: str, limit: int = 5) -> list[dict]:
    """Cross-scope recall, gated by `_can_recall`. Same-scope always allowed;
    otherwise the grants layer decides. Returns [] when denied."""
    _parse_scope(from_scope)
    _parse_scope(target_scope)
    if not _can_recall(from_scope, target_scope):
        return []
    return await search(target_scope, query, limit)


# --- store registration (Phase 4, additive) ----------------------------------
# Knowledge scopes are platform-owned Stores of type "Note", accessed through the
# grant mechanics (services/stores + services/permissions). This is a REGISTRATION
# WRAPPER: it declares each EXISTING scope directory as a Store, moving/rewriting
# nothing and touching none of the search/note/Letta internals above. The plaintext
# markdown dirs remain the source of truth exactly as before.
def _scope_name(scope_key: str) -> str:
    """A store-name-safe form of a scope key (':' is not allowed in a store name)."""
    return scope_key.replace(":", "-")


def register_knowledge_stores() -> None:
    """Declare each existing knowledge scope as a Store of type "Note".

    Enumerates the on-disk scope directories (never creating one) and registers a
    Store per scope under owner_app "knowledge". Best-effort and idempotent."""
    try:
        from services import stores
    except Exception:
        return

    root = _root()
    found: list[tuple[str, Path]] = []

    ws = root / "shared" / "knowledge"
    if ws.exists():
        found.append(("workshop", ws))

    for kind, base in (("project", root / "projects"), ("app", root / "apps")):
        if base.exists():
            for child in sorted(base.iterdir()):
                d = child / "knowledge"
                if d.is_dir() and _SLUG_RE.fullmatch(child.name):
                    found.append((f"{kind}:{child.name}", d))

    agents_base = _runtime() / "agents"
    if agents_base.exists():
        for child in sorted(agents_base.iterdir()):
            d = child / "knowledge"
            if d.is_dir() and _SLUG_RE.fullmatch(child.name):
                found.append((f"agent:{child.name}", d))

    for scope_key, d in found:
        try:
            stores.declare("knowledge", _scope_name(scope_key), d, type="Note")
        except Exception:
            continue
