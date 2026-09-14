"""KnowledgeStore — per-scope notes with plaintext markdown as the source of truth.

Every write lands on disk (git-inspectable); recall is a plaintext substring
search over the scope's notes. The Letta vector-archive layer that used to derive
a semantic index from these files was retired in the Stage 9 Letta teardown; the
plaintext store was always authoritative and its search fallback is now the only
path.

Scope model (see `scope_for_context`): one of `workshop`, `project:<slug>`,
`app:<name>`, `agent:<id>`, `role:<role>`. Each scope maps to a directory
holding `INDEX.md` (the index) plus one `<note-id>.md` per note.

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
from services import scope_tree
from wsplatform import paths

log = logging.getLogger("workshop.knowledge")

# note_id: kebab/hex slug, optionally one folder prefix (e.g. user/my-note).
# Only [a-z0-9-] and a single /; no .. or absolute paths -> no traversal.
_NOTE_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)?")
_SLUG_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", re.IGNORECASE)

# INDEX.md index line: "- [Title](note-id.md) - one-line hook"
# `id` may be a path (e.g. user/note-name.md) for notes filed in subdirectories.
_INDEX_RE = re.compile(
    r"^-\s+\[(?P<title>.*)\]\((?P<id>[a-z0-9/-]+)\.md\)(?:\s+-\s+(?P<hook>.*))?\s*$"
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
def scope_for_context(context_key: str) -> str:
    """Map a chat context_key to a knowledge scope_key.

    The rule itself lives in `services.scope_tree`, the single owner of the
    scope model, alongside `parse`/`reach`/`govern`. This spelling stays because
    it is the name ~15 call sites already use; it must never grow a branch of
    its own. See `scope_tree.scope_for_context` for the table and for the five
    disagreeing copies it replaced.
    """
    from services import scope_tree
    return scope_tree.scope_for_context(context_key)


def _parse_scope(scope_key: str) -> tuple[str, str]:
    """Return (kind, slug). Raises ValueError on an unknown shape.

    A thin call onto `services.scope_tree.parse`, the single owner of the scope
    model (memory-and-documentation-model-2026-08-14.md sec 7.1). Kept as a
    (kind, slug) tuple rather than switching every caller to `scope_tree.Scope`,
    because `learnings.py`, `memory_keeper.py`, `triager.py`, `skills.py` and the
    knowledge/skills routes already unpack this shape directly.

    `development` is present only in developer mode (ADR 0014). Off, it is not a
    scope at all and raises like any other unknown key — the notes layer for
    work on the Workshop's own source does not exist in a normal install, rather
    than existing and refusing."""
    from services import scope_tree
    scope = scope_tree.parse(scope_key)
    return (scope.kind, scope.slug)


def scope_dir(scope_key: str, *, create: bool = False) -> Path:
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

    `create` DEFAULTS TO FALSE, AND THAT IS THE POINT. This used to mkdir
    unconditionally, so every READ created the directory it was about to read.
    Listing an app that does not exist created `apps/<slug>/knowledge/`, and a
    caller that sent the JavaScript string "undefined" left a permanent
    `agent:undefined` scope in the operator's Memory tree (measured 2026-08-23).
    `routes/notes_all.py::_scope_roots` enumerates scopes by which directories
    exist and states "listing never creates a scope" — that invariant is only
    true with this default. Pass `create=True` from a path that is about to
    WRITE, and from nowhere else.
    """
    kind, slug = _parse_scope(scope_key)
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
    elif kind == "role":
        d = shared / "roles" / slug.lower()
    else:
        # project / app / agent: each is a tree-owning scope, so its knowledge
        # directory hangs off the ONE scope root rather than this module
        # re-spelling `projects/`, `apps/` or `agents/`. `scope_tree.scope_root`
        # reproduces the exact former parents (projects_root()/<slug>,
        # workshop_root()/apps/<name>, runtime_dir()/agents/<slug>); the path is
        # byte-for-byte what it was, now derived from the single mapping.
        d = scope_tree.scope_root(scope_key) / "knowledge"
    if create:
        existed = d.exists()
        d.mkdir(parents=True, exist_ok=True)
        if not existed:
            (d / "system").mkdir(exist_ok=True)
            (d / "user").mkdir(exist_ok=True)
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
        raise ValueError(f"invalid note_id: {note_id!r} (slug or folder/slug required)")
    return note_id


# --- atomic file + map IO ----------------------------------------------------
def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


# --- INDEX.md index ----------------------------------------------------------
# Memory is the PLACE, a note is the THING, and this file is the scope's index
# of notes -- so it is named for what it indexes, not for the surface that
# renders it. It was `MEMORY.md` until 2026-09-05, which named the place at the
# file end of the stack while `Memory.tsx` named the thing at the page end: the
# same defect, mirrored. Renaming it costs nothing because the index is a pure
# mirror of the `.md` files beside it and is never injected into a prompt
# (`index_block` builds from `_ranked_notes`, which reads the note files).
INDEX_FILENAME = "INDEX.md"
_LEGACY_INDEX_FILENAME = "MEMORY.md"


def _index_path(scope_key: str, *, create: bool = False) -> Path:
    """The scope's index file, migrating a pre-rename `MEMORY.md` on the way.

    A store written before the rename has the index under the old name, and a
    missing index is not an error here (`_read_index` returns {} and
    `list_notes` falls back to a directory scan), so a store that never
    migrated would silently lose every title and hook until something wrote a
    note. The rename is done once, in the one place that resolves the path.
    """
    d = scope_dir(scope_key, create=create)
    path = d / INDEX_FILENAME
    legacy = d / _LEGACY_INDEX_FILENAME
    if not path.exists() and legacy.exists():
        with contextlib.suppress(OSError):
            os.replace(legacy, path)
    return path


def _read_index(scope_key: str) -> dict:
    """note_id -> {title, hook}. Empty if the index file is missing."""
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
    _atomic_write(_index_path(scope_key, create=True), "\n".join(lines) + "\n")


# --- index cap (Claude Code 200-line always-loaded rule) ---------------------
# The scope index is the always-loaded layer: a fresh worker reads it into
# its stable prefix every turn, and an agent harness typically loads only the
# first 200 lines of one, so an unbounded index silently truncates (and bloats the cached
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
    """Bound a scope's INDEX.md to ``max_lines`` total lines. When it is
    over, demote the least-recently-referenced notes (oldest file mtime first)
    into the ``archive/`` subdir and catalog them in the ``memory-archive`` note,
    trimming the index so the always-loaded layer stays bounded. Returns
    {"scope", "demoted", "index_lines"}. A cheap no-op while under the cap."""
    _parse_scope(scope_key)
    entries = _read_index(scope_key)
    total = _INDEX_HEADER_LINES + len(entries)
    if total <= max_lines:
        return {"scope": scope_key, "demoted": 0, "index_lines": total}

    d = scope_dir(scope_key, create=True)

    def _recency(note_id: str) -> float:
        f = d / f"{note_id}.md"
        try:
            return f.stat().st_mtime
        except OSError:
            return 0.0

    # The archive note itself is never demoted; leave room for its own index line.
    # Neither is the working-state note: demotion moves a file into `archive/`,
    # where `get_note` (and therefore `read_working_state`, and therefore the
    # prompt block) cannot see it, so a scope that crossed the cap would
    # silently stop handing new chats its working state. It is the one note
    # whose whole job is to be found by somebody who does not know it exists.
    # It is rewritten on every fold, so in practice it is always among the most
    # recent anyway; this is the guarantee, not the common case.
    _RESERVED = (ARCHIVE_NOTE_ID, WORKING_STATE_NOTE_ID, MANIFEST_NOTE_ID)
    demotable = sorted((nid for nid in entries if nid not in _RESERVED), key=_recency)
    keep = max(0, max_lines - _INDEX_HEADER_LINES - len(_RESERVED))
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
            dest = archive_dir / f"{nid}.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dest)
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
# Memoized per RESOLVED DIRECTORY -- see design doc sec 7.2: this glob-and-parse
# used to run unconditionally on every call, and the layered-index path
# (`layered_index_block`) calls it once per rendered layer, so a single turn in
# a project chat paid it three or four times over for state that had not
# changed since the last turn.
#
# Keyed on `str(d)` (the resolved scope directory), NOT on the bare `scope_key`
# string. A scope name like `workshop` is per-owner (`_shared_knowledge_root`)
# and, in tests, per fixture: the same string can name a different physical
# directory from one call to the next, and two DIFFERENT directories can
# legitimately land on the same (scope_version, dir_mtime_ns) pair (coarse
# filesystem timestamp resolution, or two independent callers both on their
# first write). Keying on the string alone served one caller's cache entry to
# another's directory the first time this was tried, caught by
# `test_a_note_written_before_metadata_existed_still_renders` reading back a
# stale corpus. Keying on the resolved path removes the ambiguity outright.
#
# The freshness check inside each entry is (scope_version.current(scope), the
# directory's own mtime_ns): `scope_version` is bumped on every `put_note`
# (including an in-place update), and the directory mtime changes on any
# add/remove of a `<id>.md` entry, including `delete_note` and
# `enforce_index_cap`'s archive moves, neither of which bumps `scope_version`
# today. Checking both is what makes a stale read impossible without having to
# change either write path's behaviour, which this migration must not do.
_list_notes_cache: dict[str, tuple[tuple, list[dict]]] = {}


def _list_notes_cache_key(scope_key: str, d: Path) -> tuple:
    from services import scope_version
    try:
        dir_mtime = d.stat().st_mtime_ns
    except OSError:
        dir_mtime = 0
    return (scope_version.current(scope_key), dir_mtime)


def list_notes(scope_key: str) -> list[dict]:
    """[{id, title, hook, updated}] for the scope. Parses INDEX.md, falls back
    to a directory scan for any `<id>.md` the index does not mention."""
    _parse_scope(scope_key)
    d = scope_dir(scope_key)
    cache_id = str(d)
    key = _list_notes_cache_key(scope_key, d)
    cached = _list_notes_cache.get(cache_id)
    if cached is not None and cached[0] == key:
        return list(cached[1])

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
    for f in sorted(d.rglob("*.md")):
        rel = f.relative_to(d)
        if rel.name in (INDEX_FILENAME, _LEGACY_INDEX_FILENAME):
            continue
        parts = rel.parts
        if len(parts) > 1 and parts[0] == "archive":
            continue
        note_id = str(rel.with_suffix(""))
        if note_id in seen:
            continue
        if not _NOTE_ID_RE.fullmatch(note_id):
            continue
        out.append({"id": note_id, "title": note_id, "hook": "", "updated": _mtime_iso(f)})
    out.sort(key=lambda n: n["id"])

    _list_notes_cache[cache_id] = (key, out)
    return list(out)


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

    A thin call onto `services.scope_tree.govern` (memory-and-documentation-
    model-2026-08-14.md sec 4.3, sec 7.1): this function's shape is exactly
    what that design calls GOVERNANCE, not the ancestor chain, and `govern`
    is now the single implementation of it. `layered_index_block` reads
    element 0 of this list positionally as the governance layer, so the order
    (governance widest-first) must not change.
    """
    from services import scope_tree
    return scope_tree.govern(scope_key, role=role, private=private)


# The total token budget for the WHOLE block layered_index_block injects, not
# per layer. The first cut of the ranked digest gave `index_block` its own
# per-layer budget and had `layered_index_block` call it once per layer with no
# total, so a chat reading three or four layers MULTIPLIED the cap instead of
# bounding it: measured on this corpus, a plain workshop chat rose from ~651
# pre-digest tokens (19 notes, no cap at all) to ~805, and
# `layered_index_block("development", role="reviewer")` (workshop + role +
# development, the read union for a development chat) hit ~1255 against a
# ~672-token pre-digest baseline for that same read union (workshop ~651 +
# development ~21). 500 is set so an ordinary three-to-four-layer chat lands
# clearly under that ~672 baseline with headroom to spare, while still holding
# roughly 10-15 title+hook lines split across the layers that actually have
# notes.
DEFAULT_LAYERED_INDEX_TOKEN_BUDGET = 500

# The GOVERNANCE layer (design doc sec 4.3: house style, operator preferences,
# cross-cutting rules) is `read_layers`'s FIRST element, read positionally
# rather than hardcoded as "workshop" because a later phase makes the
# governance scope configurable. Pure narrowest-first allocation starves
# exactly this layer whenever a narrow scope has more notes than the budget
# allows, which is backwards: governance is the thing that must always reach
# the model, regardless of how much a project or an agent happened to write.
# So it gets a reserved FLOOR, spent before anything else is allocated. 0.4
# (40%) leaves the narrower layers a majority of the budget to still compete
# for (60% of 500 = 300 tokens, split narrowest-first across up to three more
# layers) while guaranteeing governance enough for several preference/decision
# lines rather than the one that survived when it was starved to whatever
# narrower layers left over.
GOVERNANCE_FLOOR_FRACTION = 0.4


def layered_index_block(scope_key: str, role: str | None = None,
                        private: str | None = None,
                        total_tokens: int = DEFAULT_LAYERED_INDEX_TOKEN_BUDGET
                        ) -> tuple[str, list[str]]:
    """`index_block` over the read-union, under ONE total token budget for the
    whole block. Returns (text, scopes_used).

    Each layer is labelled, because an unlabelled merge tells the model what is
    known but not what governs: "this is how reviewing is done here" and "this is
    how THIS project does it" are different claims, and a conflict between them
    is resolvable only if the reader can see which is which.

    Allocation, in order:

    1. The GOVERNANCE layer (`read_layers`'s first, widest element) is served
       first, up to its reserved floor (`GOVERNANCE_FLOOR_FRACTION` of the
       total). Whatever it does not use returns to the pool for step 2, rather
       than being wasted.
    2. Everything else is allocated NARROWEST LAYER FIRST (the reverse of
       `read_layers`' widest-first order, excluding governance, which already
       had its turn): the narrowest layer is the one most relevant to this
       chat, so it spends what it needs before a wider layer that happens to
       hold many more notes.
    3. If step 2 leaves budget unspent (there were no other layers, or they
       did not need it all), governance gets a TOP-UP pass with the leftover.
       Without this a chat whose only layer IS governance (an ordinary
       workshop chat, no role, no place beyond workshop) would be capped at
       the 40% floor and waste the other 60% of the budget on nothing, which
       is the opposite of "must always reach the model": the floor is a
       minimum guarantee against being starved by a busy narrow scope, not a
       ceiling on governance when nothing is competing for the budget.

    A layer left with no budget still announces itself in one line (its note
    count, no titles), rather than vanishing: a layer disappearing entirely is
    the same failure `index_block`'s own truncation footer exists to avoid, one
    level up. But a stub must never cost more than the content it replaces: if
    a layer's FULL content (every note, untruncated) would cost about as much
    as the sentence saying it exists, that content is shown instead - printing
    a same-length sentence that says a note exists, in place of the note
    itself, is pure loss.
    """
    layers = read_layers(scope_key, role, private)
    if not layers:
        return "", []

    def _stub(n: int) -> str:
        if n <= 0:
            return ""
        return f"({n} {_noun('note', n)} in this scope; use the search tool to reach them)"

    def _stub_or_render(sk: str, budget: int) -> str:
        if budget <= 0:
            try:
                n = len(list_notes(sk))
            except Exception:
                n = 0
            stub = _stub(n)
            if not stub:
                return ""
            # A stub replaces content only when the content would cost MORE
            # than the stub itself. `max_tokens=_approx_tokens(stub)` renders
            # whatever fits in that same budget; compare actual costs rather
            # than note counts, because `index_block` always shows at least
            # one note regardless of budget, so a single huge note would
            # otherwise look like "everything fit" when it did not. In
            # practice this reduces to "prefer content only when nothing was
            # left out": a truncated render always pays for the remainder
            # line ON TOP of its notes, which reliably pushes it over the
            # stub's own cost whenever anything was actually cut.
            try:
                full = index_block(sk, max_tokens=_approx_tokens(stub))
            except Exception:
                full = ""
            if full and _approx_tokens(full) <= _approx_tokens(stub):
                return full
            return stub
        try:
            return index_block(sk, max_tokens=budget)
        except Exception:
            return ""

    rendered: dict[str, str] = {}

    # 1. Governance first, off the top, capped at its floor.
    governance = layers[0]
    floor = int(total_tokens * GOVERNANCE_FLOOR_FRACTION)
    gov_block = _stub_or_render(governance, floor)
    gov_used = _approx_tokens(gov_block)
    remaining = total_tokens - gov_used

    # 2. Everyone else, narrowest first, off what is left (including any of
    # governance's floor it did not spend).
    others = [sk for sk in layers if sk != governance]
    for sk in reversed(others):
        b = _stub_or_render(sk, remaining)
        if not b:
            continue
        rendered[sk] = b
        remaining -= _approx_tokens(b)

    # 3. Top up governance with whatever is still unspent (see docstring).
    # Re-rendering with a larger budget is cheap (small scopes) and simpler
    # than tracking a separate "extra" allotment to append.
    if remaining > 0 and gov_block:
        topped_up = _stub_or_render(governance, floor + remaining)
        topped_used = _approx_tokens(topped_up)
        if topped_used > gov_used:
            remaining -= (topped_used - gov_used)
            gov_block = topped_up

    if gov_block:
        rendered[governance] = gov_block

    blocks: list[str] = []
    used: list[str] = []
    for sk in layers:  # back to widest-first for display, unchanged from before
        b = rendered.get(sk)
        if b:
            blocks.append(f"[{sk}]\n{b}")
            used.append(sk)
    return ("\n\n".join(blocks), used)


# --- ambient index: a ranked, token-capped digest ----------------------------
# This used to list EVERY note in the scope, so the block injected into every
# chat prompt grew linearly with the corpus and enforce_index_cap(200) above was
# a band-aid on that growth rather than a design. See
# docs/platform-redesign/memory-and-documentation-model-2026-08-14.md sec 5.5.
# Now it ranks (pinned first, then by type, recency as the tiebreak WITHIN a
# type) and stops at a token budget, and it SAYS what it left out rather than
# truncating silently: a list that presents itself as complete is worse than
# no list, because the model stops looking for the rest.

# ~4 chars/token, the same rough estimate used elsewhere in this repo (e.g.
# services/learnings._approx_tokens). Good enough to hold a prompt block under a
# budget without pulling in a tokenizer dependency.
def _approx_tokens(text: str) -> int:
    return (len(text or "") + 3) // 4


def _noun(word: str, n: int) -> str:
    """English singular/plural for a count in a rendered sentence ("1 note",
    "2 notes"). Only the plurals this module actually needs (a trailing -s);
    not a general inflector."""
    return word if n == 1 else word + "s"


# The default for a STANDALONE call to `index_block` on a single scope: a
# direct caller that wants one scope's whole digest, not a turn's injected
# block. 800 tokens (~3200 chars) holds roughly 15-25 title+hook lines
# depending on length. It has to stay small no matter how large a scope's note
# count grows, but it is not what bounds a chat turn: a turn reads the layered
# union (up to four layers via `read_layers`), and THAT path is bounded by
# `DEFAULT_LAYERED_INDEX_TOKEN_BUDGET` below, spent once across every layer
# rather than once per layer. (The two were conflated in the first cut of this
# digest, which let a multi-layer turn multiply the per-layer cap instead of
# being bounded by it - see that constant's comment for the measured numbers.)
DEFAULT_INDEX_TOKEN_BUDGET = 800

def _ranked_notes(scope_key: str) -> list[dict]:
    """`list_notes()` plus each note's `author` / `chat` / raw mtime, read
    from its metadata marker. One extra file read per note (small scopes; the
    same cost class list_notes already pays per-note for mtime)."""
    d = scope_dir(scope_key)
    out = []
    for n in list_notes(scope_key):
        f = d / f"{n['id']}.md"
        author, chat, mtime = "", "", 0.0
        if f.exists():
            try:
                a, _r, c, _ = _parse_meta_line(f.read_text())
                author = a
                chat = c
                mtime = f.stat().st_mtime
            except OSError:
                pass
        out.append({**n, "author": author, "chat": chat, "_mtime": mtime})
    return out


def list_notes_meta(scope_key: str) -> list[dict]:
    """Public entry point onto `_ranked_notes`: `list_notes()` plus `author` /
    `chat` per note. `chat` is the originating thread id, or "" when it was
    never recorded. Drops the private `_mtime` sort key that `index_block`
    needs and callers outside this module should not depend on."""
    return [{k: v for k, v in n.items() if k != "_mtime"} for n in _ranked_notes(scope_key)]


def note_meta(scope_key: str, note_id: str) -> dict:
    """`{author, reach, chat}` for one note, defaults applied. Used by the
    per-note GET route so the editor can show and round-trip provenance.
    `chat` is the originating thread id, or "" when it was never recorded."""
    author, reach, chat = _stored_meta(scope_key, note_id)
    return {"author": author, "reach": reach, "chat": chat}


def _index_rank_key(n: dict) -> tuple:
    """Most-recently-updated first, then id (stable tiebreak)."""
    return (-(n.get("_mtime") or 0.0), n.get("id") or "")


def index_block(scope_key: str, max_tokens: int = DEFAULT_INDEX_TOKEN_BUDGET) -> str:
    """A ranked, token-capped ambient digest of the scope's notes for the chat
    prompt, or "" when the scope has no notes. Titles + hooks only, never bodies
    (ADR 0011 restated in sec 5.5 of the design doc), so it stays small and
    cache-stable regardless of corpus size. Pinned notes first, then by type
    (preference/decision before fact/reference), then most-recently-updated as
    the tiebreak within a type. When the scope has more notes than fit the
    budget, the last line declares how many were left out."""
    try:
        notes = _ranked_notes(scope_key)
    except Exception:
        return ""
    if not notes:
        return ""
    notes.sort(key=_index_rank_key)

    lines: list[str] = []
    used_tokens = 0
    shown = 0
    for n in notes:
        hook = f": {n['hook']}" if n.get("hook") else ""
        line = f"- {n['title']} ({n['id']}){hook}"
        cost = _approx_tokens(line)
        # Always show at least one note, even if it alone busts the budget: an
        # empty digest is a worse failure than a slightly-over-budget one.
        if shown and used_tokens + cost > max_tokens:
            break
        lines.append(line)
        used_tokens += cost
        shown += 1

    remaining = len(notes) - shown
    if remaining > 0:
        lines.append(
            f"({remaining} more {_noun('note', remaining)} in this scope; "
            "use the search tool to reach them)"
        )
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


# --- metadata: author, reach, chat -------------------------------------------
# A note the operator hand-wrote, one the model wrote through a ```note``` block
# last week, and one distilled during compaction were byte-identical on
# re-entry, so nothing downstream could weigh them differently. Author, reach,
# and chat ride in an HTML comment on the first line of the body rather than in
# INDEX.md: the index line is regex-parsed with an already-optional ` - hook`
# tail, and a second optional tail there would be ambiguous for any hook
# containing " - ".
#
# `chat` is an optional field on the same marker (design doc sec 5.2: "every
# note carries provenance ... originating chat ref"): the bare thread id
# (uuid4 hex; see services/chat_store). "" means not recorded, which is the
# honest state for every note written before this existed or one filed outside
# a chat -- never backfilled, never guessed.
#
# The note's TIMESTAMP is not duplicated here: the file's own mtime
# (`list_notes`'s `updated` field) is already the write time and re-deriving a
# second one on this marker would be two sources of truth for the same fact.
#
# See docs/decisions/0015-transcript-structure-and-content-provenance.md §8 and
# docs/platform-redesign/memory-and-documentation-model-2026-08-14.md §5.

# HOW FAR A NOTE REACHES INTO A PROMPT, which is the field that folded the
# separate learnings store into this one (2026-08-28; the decision is
# the-plan-2026-08-23.md section 2, and backlog-reconciliation-2026-08-24.md
# section 1.1 settles it against the `kind` field two same-day documents also
# proposed).
#
#   index   the default. Title and hook appear in the scope index; the body is
#           retrieved on request. Every note ever written is this.
#   always  the body is injected verbatim into every prompt in scope. This is
#           what a "learning" was, and carrying it was the ONLY thing that ever
#           made learnings a separate system.
#   search  findable, never surfaced. The archive tier.
#
# WHY `reach` AND NOT A `kind` WITH A `learning` VALUE. A kind preserves the
# unanswerable write-time question of whether a sentence is a note or a
# learning. Two protocol blocks 21 lines apart in chat_context.py both said
# "durable" and both said "scoped" and asked the model to sort on a distinction
# neither stated; 11 of 49 rows came out machine noise. `reach` asks a question
# that has an answer: should this be injected in full, indexed by title, or
# merely findable.
#
# THE NAME OF THE THING is a STANDING NOTE, and the property is "always in
# context". "Learning" is a gerund used as a count noun that names provenance
# rather than function, and every row was in fact an imperative.
_NOTE_REACH = ("index", "always", "search")
_DEFAULT_NOTE_REACH = "index"

_META_RE = re.compile(
    r"^<!--\s*author:\s*(?P<author>[^;>]*?)"
    r"(?:;\s*type:\s*[a-z]+)?"
    r"(?:;\s*reach:\s*(?P<reach>[a-z]+))?"
    r"(?:;\s*pinned:\s*(?:true|false))?"
    r"(?:;\s*chat:\s*(?P<chat>[^;>]*?))?"
    r"\s*-->\s*\n?"
)


def _parse_meta_line(body: str) -> tuple[str, str, str, str]:
    """(author, reach, chat, body-without-the-marker).

    `reach` is "" when the marker (or that field of it) is absent, rather than
    defaulted here: callers that MERGE a write need to tell "not specified this
    call" from "explicitly index". `chat` is "" both when absent and when
    explicitly recorded empty. Callers that just want to RENDER a value use
    `note_reach` / `note_chat` below, which apply the default.
    """
    m = _META_RE.match(body or "")
    if not m:
        return "", "", "", (body or "")
    author = (m.group("author") or "").strip()
    reach = (m.group("reach") or "").strip().lower()
    if reach not in _NOTE_REACH:
        reach = ""
    chat = (m.group("chat") or "").strip()
    return author, reach, chat, (body or "")[m.end():]


def split_author(body: str) -> tuple[str, str]:
    """(author, body-without-the-marker). Author is "" on notes written before
    this existed, which is most of them and is not an error."""
    author, _reach, _chat, rest = _parse_meta_line(body)
    return author, rest


def note_reach(body: str) -> str:
    """How far this note reaches into a prompt, defaulting to "index".

    "index" for every note written before this field existed, which is the
    correct reading: they were written to be looked up, not to be injected."""
    _, r, _, _ = _parse_meta_line(body)
    return r or _DEFAULT_NOTE_REACH


def note_chat(body: str) -> str:
    """The originating chat's thread id, or "" when it was never recorded
    (every note written before this existed, or one filed outside a chat)."""
    _, _, c, _ = _parse_meta_line(body)
    return c


def _stored_meta(scope_key: str, note_id: str) -> tuple[str, str, str]:
    """(author, reach, chat) already on disk for this note, defaults applied."""
    f = scope_dir(scope_key) / f"{note_id}.md"
    if not f.exists():
        return "", _DEFAULT_NOTE_REACH, ""
    try:
        author, r, c, _ = _parse_meta_line(f.read_text())
        return author, (r or _DEFAULT_NOTE_REACH), c
    except OSError:
        return "", _DEFAULT_NOTE_REACH, ""


def _with_meta(body: str, author: str,
              fallback_author: str,
              chat_id: str = "", fallback_chat: str = "",
              reach: str = "", fallback_reach: str = "") -> str:
    """Stamp author/reach/chat onto a body, PRESERVING what is already recorded
    for a field that this call does not specify. An edit that passes no author
    is an edit, not a claim of anonymity; the same holds for reach and chat:
    round-tripping a note through the editor must not silently erase them.
    Fallbacks come from disk rather than from the passed body, because
    `get_note` strips the marker before anything else sees it.

    `chat_id` is never fabricated or guessed here: an empty call preserves
    whatever chat (possibly none) is already recorded, exactly like author.

    When the result would carry no author, the default reach, and no chat,
    the marker is omitted entirely so an untouched old note stays byte-for-byte
    what it was."""
    existing_author, existing_reach, existing_chat, clean = _parse_meta_line(body)
    a = (author or "").strip() or existing_author or fallback_author
    r = (reach or "").strip().lower()
    if r not in _NOTE_REACH:
        r = existing_reach or fallback_reach or _DEFAULT_NOTE_REACH
    c = (chat_id or "").strip() or existing_chat or fallback_chat
    if not a and r == _DEFAULT_NOTE_REACH and not c:
        return clean
    marker = f"<!-- author: {a}"
    if r != _DEFAULT_NOTE_REACH:
        marker += f"; reach: {r}"
    if c:
        marker += f"; chat: {c}"
    marker += " -->\n"
    return marker + clean


# --- writes ------------------------------------------------------------------
def _rewrite_wikilink(body: str, old_title: str, new_title: str) -> str:
    """Every `[[old title]]` in `body` becomes `[[new title]]`, preserving the
    optional `#anchor` and `|label` the wikilink syntax allows.

    Matching is case-insensitive on the target because `extract_refs` resolves
    it that way (`titles.get(target.lower())`), so a link written with different
    capitalisation is a link that resolves, and therefore a link that breaks.
    """
    import re as _re

    def sub(m: "_re.Match[str]") -> str:
        target, rest = m.group(1), m.group(2) or ""
        if target.strip().lower() != old_title.strip().lower():
            return m.group(0)
        return f"[[{new_title}{rest}]]"

    return _re.sub(r"\[\[([^\]|#\n]+)((?:#[^\]|\n]*)?(?:\|[^\]\n]*)?)\]\]", sub, body)


def _retitle_wikilinks(scope_key: str, note_id: str, old_title: str, new_title: str) -> None:
    """Rewrite `[[old_title]]` to `[[new_title]]` in every note that links here.

    The sources come from the backlink INDEX, not a corpus scan: the index is
    already in memory and already answers exactly this question. Each rewritten
    source is written back through the same atomic write the note write uses,
    and its own backlink row is left alone -- the refs it resolves to are
    unchanged, only the text naming one of them moved.
    """
    from services import backlinks, refs

    target = refs.note_ref(scope_key, note_id)
    for source in backlinks.referenced_by(target):
        # `parse_ref` RAISES on anything it cannot round-trip, and the index
        # holds ticket and chat refs too, so this skips rather than fails.
        try:
            parsed = refs.parse_ref(source)
        except ValueError:
            continue
        if parsed.kind != "note":
            continue
        src_scope, _, src_id = parsed.key.partition("/")
        if not src_scope or not src_id:
            continue
        try:
            d = scope_dir(src_scope, create=False)
        except Exception:
            continue
        path = d / f"{src_id}.md"
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8")
        rewritten = _rewrite_wikilink(body, old_title, new_title)
        if rewritten != body:
            _atomic_write(path, rewritten)
            log.info("retitle: rewrote [[%s]] -> [[%s]] in %s/%s",
                     old_title, new_title, src_scope, src_id)


async def put_note(scope_key: str, note_id: str, title: str, content: str, hook: str = "",
                   author: str = "", chat_id: str = "", reach: str = "") -> dict:
    """Write a note. The async entry point, which is what every caller uses.

    The work is entirely synchronous file IO and lives in `_put_note_sync`
    below; this awaits nothing and never did. The split exists because the
    compaction fold's landing (`routes/chat._land_fold`) is a synchronous
    function called from inside a running event loop, so it can neither await
    this nor `asyncio.run` it, and the alternative -- a second note-writing
    implementation for sync callers -- is exactly the kind of parallel system
    this repo is removing. One implementation, two doors."""
    return _put_note_sync(scope_key, note_id, title, content, hook=hook,
                          author=author, chat_id=chat_id, reach=reach)


def _put_note_sync(scope_key: str, note_id: str, title: str, content: str, hook: str = "",
                   author: str = "", chat_id: str = "", reach: str = "") -> dict:
    """Write a note (source of truth on disk).

    1. write `<note-id>.md` atomically,
    2. upsert the INDEX.md index line (kept sorted).
    Returns {scope, note_id}.

    `author` is an origin string (see services/origin): "operator", "self",
    "agent:<slug>", "derived:<model>". Optional, and absent on every note written
    before it existed.

    `reach` is how far the note reaches into a prompt: "index" (the default,
    title and hook in the scope index), "always" (body injected verbatim into
    every prompt in scope, which is what a standing note is), or "search"
    (findable, never surfaced). Empty preserves whatever is on disk, exactly
    like `author` and `chat_id`.

    `chat_id` is the thread id of the conversation that produced this note
    (design doc sec 5.2: "some retention of the chat that it came from ...
    reconstructable later"), passed down from the turn's finalize path. Never
    fabricated when absent: an empty call preserves whatever is already
    recorded (or nothing, for a note with no chat), exactly like `author`.
    """
    _parse_scope(scope_key)
    _valid_note_id(note_id)
    d = scope_dir(scope_key, create=True)

    stored_author, stored_reach, stored_chat = _stored_meta(scope_key, note_id)
    _atomic_write(d / f"{note_id}.md",
                  _with_meta(content, author,
                            stored_author,
                            chat_id, stored_chat, reach, stored_reach))

    idx = _read_index(scope_key)
    old_title = (idx.get(note_id) or {}).get("title", "")
    new_title = (title or note_id).strip()
    idx[note_id] = {"title": new_title, "hook": (hook or "").strip()}
    _write_index(scope_key, idx)

    # RETITLING REWRITES THE WIKILINKS THAT POINT AT THIS NOTE.
    #
    # `refs.extract_refs` resolves `[[Title]]` to a note id by TITLE LOOKUP, at
    # the SOURCE's write time. So a retitle leaves every inbound `[[old title]]`
    # naming something that no longer exists -- and the damage is delayed and
    # silent: the backlink row survives, because it stores the RESOLVED
    # `workshop:note/<scope>/<id>` and nothing rewrites targets, and then the
    # edge vanishes the next time that source happens to be saved and
    # re-extracted. Measured 2026-08-27
    # (memory-page-and-file-viewer-2026-08-27.md 1.6 and D11); it became worth
    # fixing rather than documenting when the title became editable in one
    # obvious place on the Memory page.
    #
    # Only notes are rewritten, and that is the whole set rather than a
    # simplification: `services/tasks` and `services/chat_store` both call
    # `extract_refs` WITHOUT `note_scope`, so a ticket body and a chat receipt
    # never produce a wikilink-derived note ref in the first place.
    if old_title and old_title != new_title:
        try:
            _retitle_wikilinks(scope_key, note_id, old_title, new_title)
        except Exception:
            # The note is renamed either way. Refusing the rename because a
            # referring note could not be rewritten would be worse than a
            # reported partial, and this is the log entry that reports it.
            log.warning("wikilink rewrite FAILED after retitling %s/%s from %r to %r; "
                        "inbound [[%s]] links are now stale",
                        scope_key, note_id, old_title, new_title, old_title,
                        exc_info=True)

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

    # Re-extract this note's outgoing refs and replace its row in the backlink
    # index (design doc sec 10/7.2). Replace, not append: an edit that removed
    # a `[[Title]]` or a `workshop:...` ref must drop the corresponding
    # backlink, not just accumulate new ones on top of stale old ones.
    #
    # WARNING, not debug, when it fails. The other best-effort blocks above
    # recover on their own (the next search rebuilds the index; the next turn
    # re-reads the scope version). A dropped backlink write does not: the row
    # stays wrong until this note is edited again, and `referenced_by` is an
    # index, not a grep, so nothing downstream notices. A silent debug line is
    # how "the backlink index holds no note rows" becomes a mystery instead of
    # a log entry.
    try:
        from services import backlinks, refs
        backlinks.set_source(
            refs.note_ref(scope_key, note_id),
            refs.extract_refs(content, note_scope=scope_key),
        )
    except Exception:
        log.warning("backlink index update FAILED for %s/%s; its outgoing refs "
                    "are now stale until the note is written again",
                    scope_key, note_id, exc_info=True)

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

    # Same reason as `put_note`, in the direction that is worse: a search hit
    # quoting a note that no longer exists reads as a live fact.
    if existed:
        try:
            from services import semantic_index
            semantic_index.mark_stale(f"deleted {scope_key}/{note_id}")
        except Exception:
            log.debug("semantic index stale-mark skipped for %s", scope_key, exc_info=True)

    # The deleted note can no longer name anything either. Drop its row rather
    # than leaving a source that still lists targets for a body that is gone.
    try:
        from services import backlinks, refs
        backlinks.remove_source(refs.note_ref(scope_key, note_id))
    except Exception:
        log.warning("backlink index cleanup FAILED for %s/%s; a deleted note "
                    "still lists targets in referenced_by",
                    scope_key, note_id, exc_info=True)

    return {"scope": scope_key, "note_id": note_id, "existed": existed}


# --- working state (the compaction handoff) ----------------------------------
# WHAT THIS IS. When a chat compacts, the messages it folds away are replaced by
# a summary that lives on the thread record and nowhere else, so a NEW chat in
# the same scope inherits nothing at all: every session started from zero and
# re-derived what the last one had already worked out. The two hand-written
# `docs/platform-redesign/HANDOFF-*.md` files exist because two agents happened
# to remember to write one. This makes that automatic.
#
# WHY IT IS A NOTE AND NOT A NEW STORE. It is scope-keyed, durable, plaintext,
# operator-readable and operator-editable, which is the definition of a note
# here. This installation is already reducing eleven parallel memory
# substrates; adding a twelfth to fix a memory problem would be the joke
# writing itself. So it is `<scope>/working-state.md`, indexed in INDEX.md,
# rendered by the Memory page, searchable, backlinked, versioned -- all of it
# for free, because it went into the store that already exists.
#
# HOW A READER TELLS IT APART FROM AN ORDINARY NOTE. Three ways, in descending
# order of how much you have to already know:
#   1. the note id is exactly `working-state`, one per scope, reserved;
#   2. the body opens with `_WORKING_STATE_HEADER`, whose first sentence says
#      it is machine-written and will be overwritten;
#   3. its author marker is `derived:<model>` and its `chat:` field names the
#      conversation whose compaction last wrote it.
# An ordinary note is a durable claim about the world. This one is a snapshot
# of a piece of work in progress, and the next compaction in this scope
# replaces it wholesale.
#
# ONE PER SCOPE, UPDATED IN PLACE. `put_note` on a fixed id overwrites, so a
# scope has exactly one current working state rather than a pile of dated
# handoffs nobody reads. The history is not lost: the file is on disk, in git
# for the tracked scopes, and every fold that produced one is in the chat
# transcript it came from.
WORKING_STATE_NOTE_ID = "working-state"
WORKING_STATE_TITLE = "Working state (auto-written at compaction)"
WORKING_STATE_HOOK = ("the last session's handoff for this scope: objective, "
                      "decisions and why, done, not done, shortcuts owed, open questions")

# The six sections, in order. Exported because three places must agree on them:
# the summarizer prompt that asks for them (`routes/chat._summary_prompt`), the
# reader that trims within them (`_bound_working_state`), and the tests that
# check a fold round-trips them. A seventh section invented by the model is not
# an improvement, it is the free prose this replaced coming back in disguise.
WORKING_STATE_SECTIONS = (
    "Objective",
    "Decisions and why",
    "Done",
    "Not done",
    "Shortcuts taken and what they owe",
    "Open questions",
)

# THE BUDGET, in characters, at the repo's usual 4-chars-per-token proxy:
# 4800 chars is ~1200 tokens.
#
# Why that number and not another. It is spent on EVERY turn of EVERY chat in
# the scope, so it has to be justified against its neighbours in the same
# prompt rather than picked for feeling generous:
#   - the layered knowledge index gets 500 tokens for ALL note titles in the
#     scope (DEFAULT_LAYERED_INDEX_TOKEN_BUDGET). The working state is not
#     taken from that budget and does not compete with it: an index of titles
#     and a body of current state are different things, and starving either to
#     feed the other is how both stop being useful.
#   - compaction leaves ~4000 tokens of the conversation verbatim
#     (`_COMPACT_KEEP_TAIL_TOKENS`). The handoff must stay comfortably under
#     that: a summary of old work that outweighs the live conversation is
#     inverted priorities, and the model will read it as the more important of
#     the two because it sits higher in the prompt.
# 1200 sits between them. It carries roughly 800 words -- six sections with
# real content in each, decisions with their reasons attached -- while costing
# under a third of the verbatim tail. The summarizer is separately asked for
# ~500 words, so this cap is headroom for a model that overshoots rather than
# the target; in the normal case nothing is trimmed at all.
#
# It is a HARD cap, not a target, and that is the point: a running summary that
# is re-fed to itself on every fold is exactly the shape of thing that grows
# without bound, and the operator can also edit this note by hand to any length
# he likes. Bounded at write AND at read, so neither path can be the one that
# forgets.
WORKING_STATE_MAX_CHARS = 4800

_WORKING_STATE_TRIM_MARKER = "[trimmed to fit the working-state budget]"

_WORKING_STATE_HEADER = """Written automatically when a chat in this scope compacts, and overwritten by
the next compaction here. It is the handoff a new chat in this scope reads to
continue the work without the old transcript.

Edit it freely to correct what is wrong: the corrected text is what the next
chat reads. But it is a snapshot of work in progress, not a durable record, so
anything that must outlive this piece of work belongs in a note of its own.

"""


def _bound_working_state(text: str, max_chars: int = WORKING_STATE_MAX_CHARS) -> str:
    """Trim a working state to `max_chars`, TAKING FROM THE FATTEST SECTION.

    Not a tail truncation. The sections are ordered by what a reader needs
    first, which puts "Shortcuts taken and what they owe" and "Open questions"
    last, and those two are precisely the things nobody writes down and nobody
    can reconstruct. Cutting the tail would delete them first and every time.

    So: keep every heading, and drop the last bullet from whichever section
    currently has the most text under it, until it fits. An over-long section
    loses its least-recently-added items; the shape survives. One marker line
    is appended so a reader (and the next fold, which is handed this text back)
    can see that something was dropped rather than silently reading a partial
    state as a complete one.
    """
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    marker_cost = len(_WORKING_STATE_TRIM_MARKER) + 1
    blocks: list[list[str]] = []
    for line in text.split("\n"):
        if line.startswith("## ") or not blocks:
            blocks.append([line])
        else:
            blocks[-1].append(line)

    def size() -> int:
        return sum(len(ln) + 1 for b in blocks for ln in b) + marker_cost

    while size() > max_chars:
        fattest = max(blocks, key=lambda b: sum(len(ln) + 1 for ln in b[1:]))
        if len(fattest) <= 1:
            # Every section is down to its bare heading and it still does not
            # fit, which means the headings themselves are the bulk (a model
            # that emitted one enormous unbroken line, or a hand-edited note
            # with no structure at all). Hard cut rather than loop forever.
            return text[: max(0, max_chars - marker_cost)].rstrip() + "\n" + _WORKING_STATE_TRIM_MARKER
        fattest.pop()
    return ("\n".join(ln for b in blocks for ln in b).rstrip()
            + "\n" + _WORKING_STATE_TRIM_MARKER)


def _sections_only(body: str) -> str:
    """The `## `-headed part of a working-state note, without the human-facing
    header. The header explains the note to the OPERATOR reading the Memory
    page; re-injecting it into every prompt would spend ~90 tokens a turn
    telling a model about a file it is not being asked to maintain.

    Falls back to the whole body when there is no heading at all, which is what
    a hand-edited note may legitimately look like: better to inject the
    operator's own words than nothing."""
    body = (body or "").strip()
    if body.startswith("## "):
        return body
    i = body.find("\n## ")
    return body[i + 1:].strip() if i >= 0 else body


def write_working_state(scope_key: str, sections: str, *, author: str = "",
                        chat_id: str = "") -> dict:
    """Write (or overwrite) this scope's working-state note. Synchronous, so
    the fold's landing can call it without an event loop of its own.

    `sections` is the structured text the summarizer produced. It is bounded
    here, before it reaches disk, so a runaway summary cannot leave a note that
    the read path then has to fight with on every turn.

    RAISES on failure. The caller (`routes/chat._land_fold`) is the one that
    must not fail, and it catches: a handoff that could not be written is worth
    a log line and nothing more, because the fold it rode in on has already
    done the thing the operator asked for."""
    text = _bound_working_state(_sections_only(sections))
    if not text:
        raise ValueError("refusing to write an empty working state")
    return _put_note_sync(
        scope_key, WORKING_STATE_NOTE_ID, WORKING_STATE_TITLE,
        _WORKING_STATE_HEADER + text + "\n",
        hook=WORKING_STATE_HOOK, author=author,
        chat_id=chat_id,
    )


def read_working_state(scope_key: str) -> tuple[str, str]:
    """(sections, originating chat id) for this scope's working state.

    `("", "")` when there is none, which is the normal state of a scope that
    has never compacted. Bounded again on the way out: the note is editable by
    hand and by any agent with note tools, and the prompt's budget is not
    theirs to spend.

    The chat id is returned alongside because the one caller that injects this
    (`chat_context.working_state_block`) needs to know whether the chat it is
    building a prompt for is the same one that last wrote the note -- in which
    case that chat already carries this text verbatim as its own compaction
    summary, and injecting it a second time would spend the budget saying the
    same thing twice."""
    body = get_note(scope_key, WORKING_STATE_NOTE_ID)
    if not body or not body.strip():
        return "", ""
    _author, _reach, chat = _stored_meta(scope_key, WORKING_STATE_NOTE_ID)
    return _bound_working_state(_sections_only(body)), chat


# --- the manifest: a scope's goal / plan / where-to-start --------------------
# The manifest is what used to be a repo PROJECT.md, moved into memory on the
# operator's instruction (2026-08-25): "make manifest a memory, not in
# files/repo". So it lives here as ONE reserved, pinned note per scope, alongside
# the working state, and is read/edited through the same Memory surface, not as a
# file that travels with the code.
#
# It differs from the working state in two ways that matter:
#   - AUTHORSHIP is either. A person may write it directly, or an agent may draft
#     and update it; the platform assumes neither. The working state, by
#     contrast, is written by compaction.
#   - It is FREE-FORM. There are no fixed sections and no auto-summary re-feeding
#     it to itself, so there is no runaway-growth shape to bound. It is just a
#     note a scope points its "read this first" instruction at.
# It is pinned and reserved for the same reason the working state is: it is the
# one note a newcomer to the scope must be able to find without knowing it exists.
MANIFEST_NOTE_ID = "manifest"
MANIFEST_TITLE = "Manifest (goal, plan, where to start)"
MANIFEST_HOOK = ("this scope's goal, plan and where to start, written by the "
                 "operator or an agent")


def write_manifest(scope_key: str, body: str, *, author: str = "") -> dict:
    """Write (or overwrite) this scope's manifest note. Free-form markdown.

    RAISES on an empty body: a manifest is a deliberate document, and an empty
    one is a delete, which goes through `delete_note` so the index stays
    consistent."""
    text = (body or "").strip()
    if not text:
        raise ValueError("refusing to write an empty manifest; delete it instead")
    return _put_note_sync(
        scope_key, MANIFEST_NOTE_ID, MANIFEST_TITLE, text + "\n",
        hook=MANIFEST_HOOK, author=author,
    )


def read_manifest(scope_key: str) -> str:
    """This scope's manifest body, or "" when there is none (the normal state of
    a scope nobody has written one for). No bounding: a manifest is not re-fed to
    a summarizer, so it has no runaway shape, and the reader is not the prompt's
    per-turn budget but an agent choosing to read it."""
    body = get_note(scope_key, MANIFEST_NOTE_ID)
    return body.strip() if body and body.strip() else ""


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
    """Same scope is always allowed. Cross-scope is a membership test against
    `scope_tree.reach(from_scope)` (memory-and-documentation-model-2026-08-14.md
    sec 4.2): `from_scope` can recall `target_scope` exactly when it is `S`
    itself or a descendant of it, downward only, never siblings, never upward.

    This used to defer to `permissions.can_recall`, which does not exist, so
    every cross-scope recall silently returned `[]` regardless of how the two
    scopes related. This is the first phase in which cross-scope recall
    actually works."""
    if from_scope == target_scope:
        return True
    from services import memory_policy, scope_tree
    try:
        in_reach = target_scope in scope_tree.reach(from_scope)
    except ValueError:
        return False
    # A scope-level `share` (Phase 5) can narrow this further: a scope marked
    # `private` is recalled only from inside itself, and one marked `open` is
    # recalled from anywhere. Notes default to `inherit`, which is exactly the
    # tree rule above, so an instance with no policy file behaves identically.
    share = memory_policy.resolve("note", target_scope)
    return memory_policy.reachable(share=share, item_scope=target_scope,
                                   from_scope=from_scope, in_reach=in_reach)


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
    Store per scope under owner_app "knowledge". Best-effort and idempotent.

    THE TRACKED PATH BELOW IS DELIBERATE; do not "fix" it to
    `_shared_knowledge_root()`. `services.stores._REGISTRY` is a process-global
    dict keyed by store URI, and `register_all()` runs on every `/stores` read,
    so a per-owner path here would let whichever owner called last rewrite
    `knowledge:workshop` for the whole process — turning a store catalog that is
    merely operator-shaped into one that actually serves one account's directory
    to another. The routes that reach this are operator-only (`main.py` mounts
    the stores router behind the operator dependency), and with the operator
    pinned `_shared_knowledge_root()` returns exactly this path anyway. Making
    the catalog per-owner is a change to the REGISTRY's shape, not to this
    enumeration."""
    try:
        from services import stores
    except Exception:
        return

    root = _root()
    found: list[tuple[str, Path]] = []

    ws = root / "shared" / "knowledge"
    if ws.exists():
        found.append(("workshop", ws))

    from wsplatform import dev_mode
    dev = ws / "development"
    if dev.is_dir() and dev_mode.dev_mode_on():
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
