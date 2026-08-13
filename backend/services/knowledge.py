"""KnowledgeStore — per-scope notes with plaintext markdown as the source of truth.

Every write lands on disk (git-inspectable); recall is a plaintext substring
search over the scope's notes. The Letta vector-archive layer that used to derive
a semantic index from these files was retired in the Stage 9 Letta teardown; the
plaintext store was always authoritative and its search fallback is now the only
path.

Scope model (see `scope_for_context`): one of `workshop`, `project:<slug>`,
`app:<name>`, `agent:<id>`, `role:<role>`. Each scope maps to a directory
holding `MEMORY.md` (the index) plus one `<note-id>.md` per note.

TWO AXES, NOT ONE. Scope used to name only a PLACE, and place is the wrong sole
axis for how agents learn. Two reviewers working in two different projects share
a ROLE, not a place, so everything either of them learned about reviewing was
either stranded in a project scope or dumped into `workshop`, where it polluted
every other reader. `role:<role>` is the second axis, and `read_layers` below is
what makes an agent read the union of both without either one leaking.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from pathlib import Path
from services import paths

log = logging.getLogger("workshop.knowledge")

def app_context_keys() -> set[str]:
    """Context keys that map onto an `app:<key>` scope.

    Read from the installed apps rather than listed here: a key naming an app
    the instance does not have is a scope nothing can reach, and an app added
    tomorrow would otherwise need an edit to this file.
    """
    from services import app_registry

    return set(app_registry.installed_app_ids())

# note_id: kebab/hex slug, lowercase, no path separators or dots -> no traversal.
_NOTE_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SLUG_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", re.IGNORECASE)

# MEMORY.md index line: "- [Title](note-id.md) - one-line hook"
_INDEX_RE = re.compile(
    r"^-\s+\[(?P<title>.*)\]\((?P<id>[a-z0-9-]+)\.md\)(?:\s+-\s+(?P<hook>.*))?\s*$"
)


# --- roots (env-overridable so tests can point at a temp tree) ---------------
def _root() -> Path:
    return paths.workshop_root()


def _runtime() -> Path:
    return Path(os.environ.get("WORKSHOP_RUNTIME", str(_root() / ".runtime")))


def _shared_knowledge_root() -> Path:
    """`shared/knowledge/` FOR THE CURRENT OWNER — the `workshop`, `role:` and
    `development` layers.

    `read_layers` puts the `workshop` layer into every scope's prompt by design
    ("operator preferences, house style, cross-cutting facts"), which is right
    and is exactly why it could not stay shared: one directory meant one
    person's house style, notes and distilled conversations were read into every
    other account's turns, and their note fences wrote back into it.

    The first fix for that was to gate the injection on `caller_is_operator()` —
    correct as far as leaks go, and it left a second user with a Workshop whose
    knowledge layer, work digest and auto-recall silently did nothing. This is
    the real fix: the layer is per-owner, so it can be read and written by
    everybody, against their own. Those gates are removed rather than kept
    alongside this.

    The operator's tracked `shared/knowledge/` does not move.
    """
    from services import ownership
    return ownership.workspace_path(_root() / "shared" / "knowledge", "knowledge")


# --- scope mapping -----------------------------------------------------------
def _project_exists(slug: str) -> bool:
    root = paths.projects_root()
    return (root / slug / "knowledge").parent.exists() or (root / slug).exists()


def scope_for_context(context_key: str) -> str:
    """Map a chat context_key to a knowledge scope_key.

    - `general`, `workshop`, `new-project` -> `workshop`
    - `budget`/`finance`/`commute`/`location` -> `app:<key>`
    - `project:<slug>` -> `project:<slug>`
    - `build:<slug>` -> `project:<slug>` if that project exists, else `agent:<slug>`
      (a build agent linked to a project shares the project scope; a freestanding
      one gets its own agent scope)
    - already-scoped `app:<name>` / `agent:<id>` -> passed through
    - any other bare key -> `app:<key>` (treated as an app provider)
    """
    ck = (context_key or "general").strip()
    if ck == "development":
        return "development"
    # `workshop` names itself. It was missing from this list, so the bare key
    # fell all the way to the `app:<key>` catch-all below and resolved to
    # `app:workshop` -> `apps/workshop/knowledge/`, a THIRD directory for a name
    # that already means `shared/queue` in task_scope and `shared/knowledge`
    # here. `scope_dir` mkdirs on demand, so simply opening such a chat created
    # it inside the tracked tree.
    if ck in ("general", "workshop", "new-project"):
        return "workshop"
    if ck in app_context_keys():
        return f"app:{ck}"
    if ":" in ck:
        prefix, _, slug = ck.partition(":")
        if prefix == "project":
            return f"project:{slug}"
        if prefix == "build":
            return f"project:{slug}" if _project_exists(slug) else f"agent:{slug}"
        if prefix in ("app", "agent"):
            return ck
    # An unrecognized bare key is NOT an app. It used to mint `app:<key>`
    # regardless, and since a thread's `context_key` is stored verbatim at
    # creation and `scope_dir` mkdirs on demand, the first ambient-index read of
    # a chat keyed `zzz` created `apps/zzz/knowledge/` inside the tracked tree —
    # an unknown name inventing a store rather than resolving to one. Installed
    # apps are matched above, by name, off the actual catalog.
    return "workshop"


def _parse_scope(scope_key: str) -> tuple[str, str]:
    """Return (kind, slug). Raises ValueError on an unknown shape.

    `development` is present only in developer mode (ADR 0014). Off, it is not a
    scope at all and raises like any other unknown key — the notes layer for
    work on the Workshop's own source does not exist in a normal install, rather
    than existing and refusing."""
    if scope_key == "workshop":
        return ("workshop", "")
    if scope_key == "development":
        from services import app_runner
        if app_runner.dev_mode_on():
            return ("development", "")
        raise ValueError(
            "unknown scope_key: 'development' (developer mode is off; "
            "set WORKSHOP_DEV_MODE=1 for this installation to enable it)")
    if ":" in scope_key:
        kind, _, slug = scope_key.partition(":")
        if kind in ("project", "app", "agent", "role") and slug and _SLUG_RE.fullmatch(slug):
            return (kind, slug)
    raise ValueError(f"unknown scope_key: {scope_key!r}")


def scope_dir(scope_key: str) -> Path:
    """The knowledge directory for a scope, created on demand.

    - `workshop`       -> shared/knowledge/
    - `development`    -> shared/knowledge/development/  (developer mode only)
    - `project:<slug>` -> projects/<slug>/knowledge/
    - `app:<name>`     -> apps/<name>/knowledge/
    - `agent:<id>`     -> .runtime/agents/<id>/knowledge/
    - `role:<role>`    -> shared/knowledge/roles/<role>/

    Role memory lives under `shared/` rather than `.runtime/` because it is
    durable, cross-agent, git-inspectable knowledge — the same class of thing as
    `workshop`, addressed on the other axis. An agent's own scope stays in
    `.runtime/` because it is that instance's episodic record.
    """
    kind, slug = _parse_scope(scope_key)
    root = _root()
    shared = _shared_knowledge_root()
    if kind == "workshop":
        d = shared
    elif kind == "development":
        # A sub-directory of the workshop notes layer, like `role:`: durable,
        # git-inspectable knowledge about this installation's own source, kept
        # out of the layer every other scope inherits from.
        #
        # NOT per-owner in any meaningful sense even though it rides on the
        # per-owner root: `development` is the platform's own source, gated by
        # WORKSHOP_DEV_MODE, and ADR 0014 makes it the single key that reaches
        # the repo. A non-operator reaching it is a separate refusal (see
        # get_provider / root_for_context), not a directory question.
        d = shared / "development"
    elif kind == "project":
        # Follows projects_root(), so a per-owner project's knowledge is
        # per-owner without this needing to know about ownership at all.
        d = paths.projects_root() / slug / "knowledge"
    elif kind == "app":
        d = root / "apps" / slug / "knowledge"
    elif kind == "role":
        d = shared / "roles" / slug.lower()
    else:  # agent
        d = _runtime() / "agents" / slug / "knowledge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def all_scope_dirs() -> list[tuple[str, Path]]:
    """Every knowledge directory that EXISTS, as `(scope_key, dir)`.

    The inverse of `scope_dir`, and the thing a whole-corpus reader (the semantic
    index) needs: `scope_dir` answers "where does THIS scope live", and nothing
    answered "which scopes are there". `semantic_index.collect_notes` therefore
    walked the shared root and its immediate subdirectories only, so
    `projects/*/knowledge`, `apps/*/knowledge`, `.runtime/agents/*/knowledge` and
    `roles/<role>/` were never indexed at all.

    ENUMERATED OFF THE FILESYSTEM, never by calling `scope_dir` over a list of
    candidate keys. `scope_dir` mkdirs what it is asked for, so a prober that
    walked the app catalog would CREATE a `knowledge/` directory inside every
    installed app in the tracked tree just by looking. Nothing here creates
    anything.

    `archive/` is excluded deliberately: `enforce_index_cap` demotes notes there
    to get them out of the always-loaded index and out of the search corpus, and
    re-indexing them would undo half of that.
    """
    out: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def add(key: str, d: Path) -> None:
        try:
            if not d.is_dir():
                return
            real = d.resolve()
        except OSError:
            return
        if real in seen:
            return
        seen.add(real)
        out.append((key, d))

    def add_children(parent: Path, key_for, *, suffix: str = "") -> None:
        try:
            kids = sorted(p for p in parent.iterdir() if p.is_dir())
        except OSError:
            return
        for kid in kids:
            if kid.name in ("archive", "__pycache__") or kid.name.startswith("."):
                continue
            add(key_for(kid.name), kid / suffix if suffix else kid)

    try:
        shared = _shared_knowledge_root()
    except Exception as e:  # no owner pinned, or no workspace yet
        log.debug("shared knowledge root unavailable: %s", e)
        return out

    add("workshop", shared)
    # `development` and `roles/<role>` ride on the shared root as subdirectories.
    add("development", shared / "development")
    add_children(shared / "roles", lambda n: f"role:{n.lower()}")
    # Any OTHER subdirectory of the shared root keeps being read, unkeyed. It is
    # not a scope this module can name, but it holds notes somebody wrote and
    # dropping it would be a regression against the previous walk.
    for sub in sorted(p for p in shared.iterdir() if p.is_dir()) if shared.is_dir() else []:
        if sub.name in ("archive", "roles", "development") or sub.name.startswith("."):
            continue
        add(sub.name, sub)

    add_children(paths.projects_root(), lambda n: f"project:{n}", suffix="knowledge")
    add_children(_root() / "apps", lambda n: f"app:{n}", suffix="knowledge")
    add_children(_runtime() / "agents", lambda n: f"agent:{n}", suffix="knowledge")
    return out


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


def read_layers(scope_key: str, role: str | None = None,
                private: str | None = None) -> list[str]:
    """The scopes an agent READS, widest first, narrowest last.

    This is the read-union that makes shared and specialized learning coexist:

      workshop     operator preferences, house style, cross-cutting facts
      role:<role>  procedural memory: how this KIND of work is done well
      place        project:* / app:* - facts about this codebase or domain
      private      agent:<slug> - what this agent tried, and how it went

    Narrowest wins on conflict, which is why the caller renders them in this
    order: a later, more specific note overrides a general one for a reader
    going top to bottom.

    The role layer is the point. Two reviewers in two different projects share a
    role and not a place, so without it everything either learned about reviewing
    was stranded in one project or dumped into `workshop` where it polluted every
    other reader. With it, instances of a role get better together without
    becoming islands and without becoming everyone's business.

    `private` is the fourth layer and is passed only when the reader IS a
    particular agent (`agent:<slug>`), because it is the narrowest thing there
    is: what this one instance tried and how it went. A chat has a place but no
    private layer, which is why this is a separate argument rather than being
    folded into `scope_key` — the caller that has both must be able to say so.

    Deduped and order-preserving; a scope that is its own layer appears once.
    """
    layers: list[str] = ["workshop"]
    if role:
        r = str(role).strip().lower()
        if r and _SLUG_RE.fullmatch(r):
            layers.append(f"role:{r}")
    if scope_key and scope_key not in layers:
        layers.append(scope_key)
    if private and private not in layers:
        layers.append(private)
    seen: set[str] = set()
    out: list[str] = []
    for s in layers:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def layered_index_block(scope_key: str, role: str | None = None,
                        private: str | None = None) -> tuple[str, list[str]]:
    """`index_block` over the read-union. Returns (text, scopes_used).

    Each layer is labelled, because an unlabelled merge tells the model what is
    known but not what governs: "this is how reviewing is done here" and "this is
    how THIS project does it" are different claims, and a conflict between them
    is resolvable only if the reader can see which is which.
    """
    blocks: list[str] = []
    used: list[str] = []
    for sk in read_layers(scope_key, role, private):
        try:
            b = index_block(sk)
        except Exception:
            continue
        if b:
            blocks.append(f"[{sk}]\n{b}")
            used.append(sk)
    return ("\n\n".join(blocks), used)


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
    """The body of `<note-id>.md`, or None if absent.

    The author marker is STRIPPED. It is background structure, not content: it
    was rendering as the literal first line of every note on the Memory page and
    landing in the edit textarea. `put_note` re-reads the stored author when a
    caller supplies none, so a round-trip through the editor cannot lose it."""
    _parse_scope(scope_key)
    _valid_note_id(note_id)
    f = scope_dir(scope_key) / f"{note_id}.md"
    if not f.exists():
        return None
    _, body = split_author(f.read_text())
    return body


# --- authorship --------------------------------------------------------------
# A note the operator hand-wrote, one the model wrote through a ```note``` block
# last week, and one Haiku distilled during compaction were byte-identical on
# re-entry, so nothing downstream could weigh them differently. The author rides
# in an HTML comment on the first line of the body rather than in MEMORY.md: the
# index line is regex-parsed with an already-optional ` - hook` tail, and a
# second optional tail there would be ambiguous for any hook containing " - ".
#
# See docs/decisions/0015-transcript-structure-and-content-provenance.md §8.
_AUTHOR_RE = re.compile(r"^<!--\s*author:\s*(?P<author>[^>]*?)\s*-->\s*\n?")


def split_author(body: str) -> tuple[str, str]:
    """(author, body-without-the-marker). Author is "" on notes written before
    this existed, which is most of them and is not an error."""
    m = _AUTHOR_RE.match(body or "")
    if not m:
        return "", (body or "")
    return m.group("author").strip(), (body or "")[m.end():]


def _stored_author(scope_key: str, note_id: str) -> str:
    """The author already on disk for this note, or ""."""
    f = scope_dir(scope_key) / f"{note_id}.md"
    if not f.exists():
        return ""
    try:
        return split_author(f.read_text())[0]
    except OSError:
        return ""


def _with_author(body: str, author: str, fallback: str = "") -> str:
    """Stamp `author` onto a body, PRESERVING what is already recorded when none
    is given. An edit that passes no author is an edit, not a claim of
    anonymity: round-tripping a note through the editor must not silently erase
    who wrote it. The fallback comes from disk rather than from the passed body,
    because `get_note` strips the marker before anything else sees it."""
    existing, clean = split_author(body)
    a = (author or "").strip() or existing or fallback
    return f"<!-- author: {a} -->\n{clean}" if a else clean


# --- writes ------------------------------------------------------------------
async def put_note(scope_key: str, note_id: str, title: str, content: str, hook: str = "",
                   author: str = "") -> dict:
    """Write a note (source of truth on disk).

    1. write `<note-id>.md` atomically,
    2. upsert the MEMORY.md index line (kept sorted).
    Returns {scope, note_id}.

    `author` is an origin string (see services/origin): "operator", "self",
    "agent:<slug>", "derived:<model>". Optional, and absent on every note written
    before it existed.
    """
    _parse_scope(scope_key)
    _valid_note_id(note_id)
    d = scope_dir(scope_key)

    _atomic_write(d / f"{note_id}.md",
                  _with_author(content, author, _stored_author(scope_key, note_id)))

    idx = _read_index(scope_key)
    idx[note_id] = {"title": (title or note_id).strip(), "hook": (hook or "").strip()}
    _write_index(scope_key, idx)

    # Keep the always-loaded index bounded (Claude Code 200-line rule). Best-effort:
    # a demotion failure must never fail the note write that just succeeded.
    try:
        enforce_index_cap(scope_key)
    except Exception:
        log.debug("index cap enforcement skipped for %s", scope_key, exc_info=True)

    # A note is shared state: another chat in this scope may be talking about
    # the same thing and have no idea this landed. Bump the scope's counter so
    # it can find out for the price of an integer compare rather than a search
    # on every turn. See services/scope_version.
    try:
        from services import scope_version
        scope_version.bump(scope_key, "note", (title or note_id).strip())
    except Exception:
        log.debug("scope version bump skipped for %s", scope_key, exc_info=True)

    # The semantic index has just gone out of date. Mark it, and let the next
    # search rebuild (services/semantic_index.rebuild_if_stale). A note nobody
    # can find until somebody remembers to run a build by hand is a note nobody
    # can find, and the alternative — rebuilding HERE — would put a local
    # embedding pass with an unbounded worst case in the middle of an HTTP write.
    #
    # Best-effort, like the two above: the note is already on disk and is the
    # thing that mattered.
    try:
        from services import semantic_index
        semantic_index.mark_stale(f"note {scope_key}/{note_id}")
    except Exception:
        log.debug("semantic index stale-mark skipped for %s", scope_key, exc_info=True)

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
        author, body = split_author(f.read_text() if f.exists() else "")
        hay = f"{note['title']} {note['hook']} {body}".lower()
        score = sum(hay.count(t) for t in terms) if terms else 0
        if score > 0 or (query and query.lower() in hay):
            scored.append((score, {
                "note_id": note_id,
                "text": (f"{note['title']}\n\n{body}").strip(),
                # Who wrote it. The recall site labels the note with this so a
                # model-distilled note and an operator-written one are not
                # byte-identical on re-entry. "" on older notes.
                "author": author,
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

    from services import app_runner
    dev = ws / "development"
    if dev.is_dir() and app_runner.dev_mode_on():
        found.append(("development", dev))

    for kind, base in (("project", root / "projects"), ("app", root / "apps")):
        if base.exists():
            for child in sorted(base.iterdir()):
                d = child / "knowledge"
                if d.is_dir() and _SLUG_RE.fullmatch(child.name):
                    found.append((f"{kind}:{child.name}", d))

    roles_base = root / "shared" / "knowledge" / "roles"
    if roles_base.exists():
        for child in sorted(roles_base.iterdir()):
            # A role scope IS its directory (no nested `knowledge/`), because it
            # already lives under shared/knowledge.
            if child.is_dir() and _SLUG_RE.fullmatch(child.name):
                found.append((f"role:{child.name}", child))

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
