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
    """[{id, title, hook, updated}] for the scope. Parses MEMORY.md, falls back
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
    for f in sorted(d.glob("*.md")):
        note_id = f.stem
        if note_id == "MEMORY" or note_id in seen:
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

# `preference` and `decision` outrank `fact` and `reference` (lower number sorts
# first) REGARDLESS OF AGE. A preference stated months ago still governs; a
# fact written this morning usually does not, and ranking recency above type
# let a single busy day's worth of same-mtime facts sweep the top of the
# digest ahead of standing house-style preferences that had not been touched
# since they were written. Recency is the tiebreak WITHIN a type, never above
# it.
_TYPE_RANK = {"preference": 0, "decision": 0, "fact": 1, "reference": 1}


def _ranked_notes(scope_key: str) -> list[dict]:
    """`list_notes()` plus each note's `author` / `pinned` / `type` / `chat` /
    raw mtime, read from its metadata marker. One extra file read per note
    (small scopes; the same cost class list_notes already pays per-note for
    mtime)."""
    d = scope_dir(scope_key)
    out = []
    for n in list_notes(scope_key):
        f = d / f"{n['id']}.md"
        author, pinned, ntype, chat, mtime = "", False, _DEFAULT_NOTE_TYPE, "", 0.0
        if f.exists():
            try:
                a, t, p, c, _ = _parse_meta_line(f.read_text())
                author = a
                pinned = bool(p)
                ntype = t or _DEFAULT_NOTE_TYPE
                chat = c
                mtime = f.stat().st_mtime
            except OSError:
                pass
        out.append({**n, "author": author, "pinned": pinned, "type": ntype,
                    "chat": chat, "_mtime": mtime})
    return out


def list_notes_meta(scope_key: str) -> list[dict]:
    """Public entry point onto `_ranked_notes`: `list_notes()` plus `author` /
    `pinned` / `type` / `chat` per note, for a cross-scope listing that needs to
    show provenance and the ambient-injection facets (memory-and-documentation-
    model-2026-08-14.md sec 11, "provenance on every item"). `chat` is the
    originating thread id, or "" when it was never recorded. Drops the private
    `_mtime` sort key that `index_block` needs and callers outside this module
    should not depend on."""
    return [{k: v for k, v in n.items() if k != "_mtime"} for n in _ranked_notes(scope_key)]


def note_meta(scope_key: str, note_id: str) -> dict:
    """`{author, type, pinned, chat}` for one note, defaults applied. Used by
    the per-note GET route so the editor can show and round-trip provenance and
    the type/pinned/chat facets without a second endpoint. `chat` is the
    originating thread id, or "" when it was never recorded."""
    author, ntype, pinned, chat = _stored_meta(scope_key, note_id)
    return {"author": author, "type": ntype, "pinned": pinned, "chat": chat}


def _index_rank_key(n: dict) -> tuple:
    """Pinned first, then type, then most-recently-updated WITHIN that type,
    then id (stable). Type outranks recency: see _TYPE_RANK's comment."""
    return (
        0 if n.get("pinned") else 1,
        _TYPE_RANK.get(n.get("type") or _DEFAULT_NOTE_TYPE, 1),
        -(n.get("_mtime") or 0.0),
        n.get("id") or "",
    )


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


# --- metadata: author, type, pinned -------------------------------------------
# A note the operator hand-wrote, one the model wrote through a ```note``` block
# last week, and one distilled during compaction were byte-identical on
# re-entry, so nothing downstream could weigh them differently. Author rides in
# an HTML comment on the first line of the body rather than in MEMORY.md: the
# index line is regex-parsed with an already-optional ` - hook` tail, and a
# second optional tail there would be ambiguous for any hook containing " - ".
#
# `type` and `pinned` are OPTIONAL fields added to that SAME marker, per the
# design doc sec 5 ("every note carries a type") rather than a third mechanism
# or a sidecar file. A note with no marker at all, or an author-only marker
# (every note written before this existed, which is most of them), still
# parses: type defaults to "fact", pinned defaults to False.
#
# `chat` is a FOURTH optional field on the same marker (design doc sec 5.2:
# "every note carries provenance ... originating chat ref"). Same reasoning as
# type/pinned: extending the one marker line rather than a sidecar file or a
# second mechanism. It carries the bare thread id (uuid4 hex; see
# services/chat_store), not a full `workshop:chat/<id>#<msg>` ref, because a
# note is written from the turn's finalize path before any reply message id
# exists to anchor a citation. "" (the default) means not recorded, which is
# the honest state for every note written before this existed and for one
# filed outside a chat (the operator's own editor) -- never backfilled, never
# guessed.
#
# The note's TIMESTAMP is not duplicated here: the file's own mtime
# (`list_notes`'s `updated` field) is already the write time and re-deriving a
# second one on this marker would be two sources of truth for the same fact.
#
# See docs/decisions/0015-transcript-structure-and-content-provenance.md §8 and
# docs/platform-redesign/memory-and-documentation-model-2026-08-14.md §5.
_NOTE_TYPES = ("decision", "fact", "reference", "preference")
_DEFAULT_NOTE_TYPE = "fact"

_META_RE = re.compile(
    r"^<!--\s*author:\s*(?P<author>[^;>]*?)"
    r"(?:;\s*type:\s*(?P<type>[a-z]+))?"
    r"(?:;\s*pinned:\s*(?P<pinned>true|false))?"
    r"(?:;\s*chat:\s*(?P<chat>[^;>]*?))?"
    r"\s*-->\s*\n?"
)


def _parse_meta_line(body: str) -> tuple[str, str, bool | None, str, str]:
    """(author, type, pinned, chat, body-without-the-marker).

    `type` is "" and `pinned` is None when the marker (or that field of it) is
    absent, rather than defaulted here: callers that MERGE a write need to tell
    "not specified this call" (fall back to what's on disk) from "explicitly
    fact/unpinned". `chat` is "" both when absent and when explicitly recorded
    empty -- there is no tri-state for it, since an empty originating chat is
    never a meaningful claim to preserve over one already on disk (see
    `_with_meta`). Callers that just want to RENDER a value use `note_type` /
    `note_pinned` below, which apply the default.
    """
    m = _META_RE.match(body or "")
    if not m:
        return "", "", None, "", (body or "")
    author = (m.group("author") or "").strip()
    note_type = (m.group("type") or "").strip().lower()
    if note_type not in _NOTE_TYPES:
        note_type = ""
    pinned_raw = (m.group("pinned") or "").strip().lower()
    pinned = True if pinned_raw == "true" else (False if pinned_raw == "false" else None)
    chat = (m.group("chat") or "").strip()
    return author, note_type, pinned, chat, (body or "")[m.end():]


def split_author(body: str) -> tuple[str, str]:
    """(author, body-without-the-marker). Author is "" on notes written before
    this existed, which is most of them and is not an error."""
    author, _type, _pinned, _chat, rest = _parse_meta_line(body)
    return author, rest


def note_type(body: str) -> str:
    """The note's type, defaulting to "fact" when the marker carries none."""
    _, t, _, _, _ = _parse_meta_line(body)
    return t or _DEFAULT_NOTE_TYPE


def note_pinned(body: str) -> bool:
    """Whether the note is pinned, defaulting to False when the marker carries
    none."""
    _, _, p, _, _ = _parse_meta_line(body)
    return bool(p)


def note_chat(body: str) -> str:
    """The originating chat's thread id, or "" when it was never recorded
    (every note written before this existed, or one filed outside a chat)."""
    _, _, _, c, _ = _parse_meta_line(body)
    return c


def _stored_meta(scope_key: str, note_id: str) -> tuple[str, str, bool, str]:
    """(author, type, pinned, chat) already on disk for this note, defaults
    applied."""
    f = scope_dir(scope_key) / f"{note_id}.md"
    if not f.exists():
        return "", _DEFAULT_NOTE_TYPE, False, ""
    try:
        author, t, p, c, _ = _parse_meta_line(f.read_text())
        return author, (t or _DEFAULT_NOTE_TYPE), bool(p), c
    except OSError:
        return "", _DEFAULT_NOTE_TYPE, False, ""


def _with_meta(body: str, author: str, note_type_: str, pinned: bool | None,
              fallback_author: str, fallback_type: str, fallback_pinned: bool,
              chat_id: str = "", fallback_chat: str = "") -> str:
    """Stamp author/type/pinned/chat onto a body, PRESERVING what is already
    recorded for a field that this call does not specify. An edit that passes
    no author is an edit, not a claim of anonymity, and the same now holds for
    type, pinned and chat: round-tripping a note through the editor must not
    silently erase them. Fallbacks come from disk rather than from the passed
    body, because `get_note` strips the marker before anything else sees it.

    `chat_id` is never fabricated or guessed here: an empty call preserves
    whatever chat (possibly none) is already recorded, exactly like author.

    When the result would carry no author, the default type, not be pinned,
    and no chat, the marker is omitted entirely so an untouched old note stays
    byte-for-byte what it was."""
    existing_author, existing_type, existing_pinned, existing_chat, clean = _parse_meta_line(body)
    a = (author or "").strip() or existing_author or fallback_author
    t = (note_type_ or "").strip().lower()
    if t not in _NOTE_TYPES:
        t = existing_type or fallback_type or _DEFAULT_NOTE_TYPE
    if pinned is not None:
        p = bool(pinned)
    elif existing_pinned is not None:
        p = existing_pinned
    else:
        p = fallback_pinned
    c = (chat_id or "").strip() or existing_chat or fallback_chat
    if not a and t == _DEFAULT_NOTE_TYPE and not p and not c:
        return clean
    marker = f"<!-- author: {a}"
    if t != _DEFAULT_NOTE_TYPE:
        marker += f"; type: {t}"
    if p:
        marker += "; pinned: true"
    if c:
        marker += f"; chat: {c}"
    marker += " -->\n"
    return marker + clean


# --- writes ------------------------------------------------------------------
async def put_note(scope_key: str, note_id: str, title: str, content: str, hook: str = "",
                   author: str = "", note_type: str = "", pinned: bool | None = None,
                   chat_id: str = "") -> dict:
    """Write a note (source of truth on disk).

    1. write `<note-id>.md` atomically,
    2. upsert the MEMORY.md index line (kept sorted).
    Returns {scope, note_id}.

    `author` is an origin string (see services/origin): "operator", "self",
    "agent:<slug>", "derived:<model>". Optional, and absent on every note written
    before it existed.

    `note_type` is one of decision/fact/reference/preference (defaults to "fact"
    when never set). `pinned` is a tri-state: None means "leave whatever is
    already on disk (False for a new note)"; True/False sets it explicitly. Both
    are optional metadata (design doc sec 5); omitting them keeps an existing
    note's write path unchanged.

    `chat_id` is the thread id of the conversation that produced this note
    (design doc sec 5.2: "some retention of the chat that it came from ...
    reconstructable later"), passed down from the turn's finalize path. Never
    fabricated when absent: an empty call preserves whatever is already
    recorded (or nothing, for a note with no chat), exactly like `author`.
    """
    _parse_scope(scope_key)
    _valid_note_id(note_id)
    d = scope_dir(scope_key)

    stored_author, stored_type, stored_pinned, stored_chat = _stored_meta(scope_key, note_id)
    _atomic_write(d / f"{note_id}.md",
                  _with_meta(content, author, note_type, pinned,
                            stored_author, stored_type, stored_pinned,
                            chat_id, stored_chat))

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
