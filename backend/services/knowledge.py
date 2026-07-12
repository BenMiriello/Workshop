"""KnowledgeStore — per-scope notes with plaintext files as source of truth and
a per-scope Letta archive as the derived semantic-recall layer.

Plaintext markdown is authoritative: every write lands on disk first and MUST
succeed even when Letta is down. The Letta sync (delete-then-insert a passage
tagged with the note id) is best-effort and only logged on failure. Files are
git-inspectable; the archive is the vector index rebuilt from them.

Scope model (see `scope_for_context`): one of `workshop`, `project:<slug>`,
`app:<name>`, `agent:<id>`. Each scope maps to a directory holding `MEMORY.md`
(the index) plus one `<note-id>.md` per note, and to a Letta archive named
`workshop-knowledge:<scope_key>`. The `scope_key -> {archive_id, passages}` map
lives in `.runtime/knowledge/archives.json`.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from pathlib import Path

from letta_legacy_client import LettaClient

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


def _archives_path() -> Path:
    return _runtime() / "knowledge" / "archives.json"


# --- Letta client (self-sufficient; no app.state dependency) -----------------
@contextlib.asynccontextmanager
async def _letta():
    """A short-lived LettaClient built the same way the app builds app.state.letta
    (LETTA_URL + LETTA_SERVER_PASS via the client's own module defaults). Mirrors
    global_memory.py, which opens a fresh httpx client per call."""
    client = LettaClient()
    try:
        yield client
    finally:
        await client.aclose()


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


def _load_map() -> dict:
    p = _archives_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        log.warning("knowledge: archives.json unreadable, treating as empty")
        return {}


def _save_map(m: dict) -> None:
    _atomic_write(_archives_path(), json.dumps(m, indent=2, sort_keys=True))


def _scope_entry(m: dict, scope_key: str) -> dict:
    ent = m.setdefault(scope_key, {})
    ent.setdefault("passages", {})
    return ent


def _set_passage(scope_key: str, note_id: str, passage_id: str) -> None:
    m = _load_map()
    _scope_entry(m, scope_key)["passages"][note_id] = passage_id
    _save_map(m)


def _pop_passage(scope_key: str, note_id: str) -> str | None:
    m = _load_map()
    ent = m.get(scope_key)
    if not ent:
        return None
    pid = ent.get("passages", {}).pop(note_id, None)
    _save_map(m)
    return pid


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


def get_note(scope_key: str, note_id: str) -> str | None:
    """The body of `<note-id>.md`, or None if absent."""
    _parse_scope(scope_key)
    _valid_note_id(note_id)
    f = scope_dir(scope_key) / f"{note_id}.md"
    return f.read_text() if f.exists() else None


# --- writes (files first, Letta best-effort) ---------------------------------
async def put_note(scope_key: str, note_id: str, title: str, content: str, hook: str = "") -> dict:
    """Write a note and sync it to the scope archive.

    1. write `<note-id>.md` atomically (source of truth),
    2. upsert the MEMORY.md index line (kept sorted),
    3. sync to Letta: drop the note's old passage, insert the new text tagged
       `[note-id]`, remember the passage id. Letta failure is logged, not fatal.
    Returns {scope, note_id, letta}.
    """
    _parse_scope(scope_key)
    _valid_note_id(note_id)
    d = scope_dir(scope_key)

    _atomic_write(d / f"{note_id}.md", content)

    idx = _read_index(scope_key)
    idx[note_id] = {"title": (title or note_id).strip(), "hook": (hook or "").strip()}
    _write_index(scope_key, idx)

    letta_ok = False
    passage_text = f"{title}\n\n{content}".strip() if title else content.strip()
    try:
        aid = await _archive_id(scope_key)
        old_pid = _load_map().get(scope_key, {}).get("passages", {}).get(note_id)
        async with _letta() as c:
            if old_pid:
                with contextlib.suppress(Exception):
                    await c.delete_passage(aid, old_pid)
            res = await c.insert_passage(
                aid, passage_text, tags=[note_id], metadata={"scope": scope_key, "title": title},
            )
        new_pid = _passage_id(res)
        if new_pid:
            _set_passage(scope_key, note_id, new_pid)
            letta_ok = True
    except Exception as e:
        log.warning("knowledge: Letta sync failed for %s/%s: %s", scope_key, note_id, e)

    return {"scope": scope_key, "note_id": note_id, "letta": letta_ok}


async def delete_note(scope_key: str, note_id: str) -> dict:
    """Remove the note file, its index line, and its archive passage."""
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

    pid = _pop_passage(scope_key, note_id)
    aid = _load_map().get(scope_key, {}).get("archive_id")
    if pid and aid:
        try:
            async with _letta() as c:
                await c.delete_passage(aid, pid)
        except Exception as e:
            log.warning("knowledge: Letta passage delete failed for %s/%s: %s", scope_key, note_id, e)

    return {"scope": scope_key, "note_id": note_id, "existed": existed}


def _passage_id(res) -> str | None:
    if isinstance(res, list) and res:
        return res[0].get("id")
    if isinstance(res, dict):
        return res.get("id")
    return None


# --- archive management ------------------------------------------------------
async def _archive_id(scope_key: str) -> str:
    """The scope's Letta archive id, lazily created. Reuses an existing archive
    by name if the local map was lost."""
    _parse_scope(scope_key)
    m = _load_map()
    ent = m.get(scope_key)
    if ent and ent.get("archive_id"):
        return ent["archive_id"]

    name = f"workshop-knowledge:{scope_key}"
    async with _letta() as c:
        existing = await c.list_archives()
        found = next((a for a in existing if a.get("name") == name), None)
        if found:
            aid = found["id"]
        else:
            cfg = await c.default_embedding_config()
            arch = await c.create_archive(
                name, cfg, description=f"Workshop knowledge scope {scope_key}",
            )
            aid = arch["id"]

    m = _load_map()
    _scope_entry(m, scope_key)["archive_id"] = aid
    _save_map(m)
    return aid


# --- search + recall ---------------------------------------------------------
async def search(scope_key: str, query: str, limit: int = 5) -> list[dict]:
    """Vector search within the scope archive. On Letta error OR empty result,
    fall back to a plaintext substring match so recall always returns something
    usable. Each result carries a `debug` field: "letta" or "fallback"."""
    _parse_scope(scope_key)
    try:
        aid = await _archive_id(scope_key)
        async with _letta() as c:
            hits = await c.search_passages(aid, query, limit)
        results = []
        for item in hits:
            p = item.get("passage") or {}
            tags = p.get("tags") or []
            results.append({
                "note_id": tags[0] if tags else None,
                "text": p.get("text", ""),
                "score": item.get("score"),
                "debug": "letta",
            })
        if results:
            return results
    except Exception as e:
        log.warning("knowledge: Letta search failed for %s: %s", scope_key, e)

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
