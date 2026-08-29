"""Scope-generic Notes API (the "Notes" surface, notes-system plan Phase N1).

Notes are to the knowledge store what the Work surface is to tasks: the same
substrate, one cross-scope view on top. The knowledge store
(`services/knowledge.py`) already IS a per-scope markdown note store with exactly
the scopes the operator asked for. There is no new store here.

Terminology: the module, files, and per-note routes all say `knowledge`. The UI
noun is "Notes". Per-note read/write/delete reuse the existing knowledge routes
(`routes/knowledge.py`, mounted at `/knowledge`); this router only adds the
cross-scope aggregation and scope enumeration.

Scope keys mirror `knowledge.scope_dir`:
  workshop        -> shared/knowledge/
  development     -> shared/knowledge/development/   (developer mode only)
  role:<role>     -> shared/knowledge/roles/<role>/
  project:<slug>  -> projects/<slug>/knowledge/
  app:<name>      -> apps/<name>/knowledge/
  agent:<slug>    -> .runtime/agents/<slug>/knowledge/

Enumeration surfaces only scopes whose knowledge directory already exists, so a
listing never creates an empty scope (knowledge.list_notes / scope_dir mkdir on
an existing directory is a no-op). `development` is the one exception and it is
deliberate: see `_scope_roots`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from services import knowledge, ownership
from wsplatform import dev_mode

router = APIRouter(prefix="/memory", tags=["memory"])

_PROJECTS_REGISTRY = Path(os.environ.get(
    "WORKSHOP_PROJECTS_REGISTRY", str(ownership._RUNTIME / "projects.json")))


# ---- Scope enumeration -----------------------------------------------------

def _project_slugs() -> list[str]:
    if not _PROJECTS_REGISTRY.exists():
        return []
    try:
        reg = json.loads(_PROJECTS_REGISTRY.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return list((reg.get("projects") or {}).keys())


def _resolved(d: Path) -> Path | None:
    """`d.resolve()`, or None when the filesystem refuses to answer. Resolving
    does NOT require the path to exist, which is what lets the two fixed scopes
    (`workshop`, `development`) be surfaced before their directory does."""
    try:
        return d.resolve()
    except OSError:
        return None


def _scope_roots() -> list[tuple[str, Path]]:
    """Every knowledge scope that actually has a directory, deduped by resolved
    path. Priority order: workshop root, development, roles, projects (registry
    then any stray on disk), apps, agents. Apart from the two fixed scopes of
    an installation (`workshop` and `development`), only an existing knowledge
    dir is surfaced, so listing never creates a scope."""
    seen: set[str] = set()
    order: list[tuple[str, Path]] = []

    def add(scope_key: str, d: Path) -> None:
        try:
            rp = d.resolve()
        except OSError:
            return
        if not rp.is_dir():
            return
        key = str(rp)
        if key in seen:
            return
        seen.add(key)
        order.append((scope_key, rp))

    root = knowledge._root()
    runtime = knowledge._runtime()

    # THE CURRENT OWNER'S shared/knowledge, not the tracked one. This used to be
    # spelled `root / "shared" / "knowledge"` inline, which is the operator's
    # copy for everybody. It did not disclose anybody's notes, because the
    # listing reads contents through `knowledge.list_notes_meta(scope_key)` and
    # that resolves per-owner via `knowledge._shared_knowledge_root()` — but the
    # ENUMERATION was still answering "does this scope exist?" about somebody
    # else's directory, so a second account's `workshop` and `development`
    # scopes were listed (or not) on the strength of the operator's disk. One
    # function owns where the layer lives; this asks it.
    try:
        ws = knowledge._shared_knowledge_root()
    except Exception:
        # No owner pinned and no bootstrap yet. Fall back to the tracked layer,
        # which is exactly what `_shared_knowledge_root` itself returns in that
        # state, rather than dropping every shared scope from the listing.
        ws = root / "shared" / "knowledge"

    # `workshop` is surfaced whether or not its directory exists yet, for the
    # same reason `development` is (see below): it is a fixed scope of every
    # installation, not one that comes and goes with a project. Enumerating it
    # from disk was harmless while the only reader was the operator, whose
    # `shared/knowledge/` has always existed. It stops being harmless the moment
    # the layer is per-owner: a second account starts with no directory, so the
    # scope would be missing from the filter and from the New note dialog, and
    # writing the first note is the only thing that would create the directory.
    wsr = _resolved(ws)
    if wsr is not None and str(wsr) not in seen:
        seen.add(str(wsr))
        order.append(("workshop", wsr))

    # `development` is surfaced whether or not its directory exists yet, and ONLY
    # in developer mode (ADR 0014).
    #
    # Why the exception. Every other scope is enumerated from disk, which is
    # right for scopes that come and go with a project. `development` is not one
    # of those: it is a fixed scope of this installation, created by the flag
    # rather than by a directory. Enumerating it from disk made it unreachable in
    # the one state that matters, the empty one: no directory meant no scope in
    # the filter and no option in the New note dialog, so there was no UI path to
    # write the first note, and writing the first note was the only way to get
    # the directory. `workshop` never hit this because its directory has always
    # existed.
    #
    # The alternative was to create shared/knowledge/development at install or at
    # import. Rejected: an empty directory is a worse carrier for "this scope
    # exists" than the flag that actually decides it, it would be created on
    # installs that must not have the scope at all, and git does not track empty
    # directories, so it would not survive a fresh clone anyway.
    #
    # A normal install cannot grow this scope: dev_mode_on() is false, the key is
    # never listed, `knowledge._parse_scope` refuses it on the write path, and
    # nothing here touches the filesystem. The directory is created on demand by
    # `knowledge.scope_dir`, exactly as it is for every other scope.
    if dev_mode.dev_mode_on():
        dev = ws / "development"
        try:
            key = str(dev.resolve())
        except OSError:
            key = str(dev)
        if key not in seen:
            seen.add(key)
            order.append(("development", dev))

    # `role:<role>` — the SECOND AXIS (see the knowledge.py module docstring and
    # scope_tree's "TWO AXES, NOT ONE"). A role scope is a real, writable
    # knowledge scope: `knowledge.scope_dir` maps it to shared/knowledge/roles/
    # <role>/, `scope_tree.parse` accepts it, and `scope_tree.govern` puts it in
    # every agent's prompt between the house layer and the agent's own place.
    # It was missing from this enumeration entirely, so the one thing it holds
    # (`roles/reviewer/review-evidence.md`) reached every reviewer's prompt and
    # could not be listed, opened or edited from the Notes UI. Injected and
    # invisible is the worst combination a memory layer can have.
    #
    # Enumerated from disk like every other non-fixed scope: the set of roles an
    # installation has written notes for is not a fixed list, and asking
    # `knowledge.scope_dir` about a candidate role would CREATE its directory
    # just by looking.
    roles_base = ws / "roles"
    if roles_base.is_dir():
        for child in sorted(roles_base.iterdir()):
            if child.is_dir() and knowledge._SLUG_RE.fullmatch(child.name):
                # A role scope IS its directory: it already lives under
                # shared/knowledge, so there is no nested `knowledge/`.
                add(f"role:{child.name.lower()}", child)

    # Projects: the registry order first (mirrors tasks_all), then a filesystem
    # sweep so a project with a knowledge dir but no registry entry is not hidden.
    for slug in _project_slugs():
        if knowledge._SLUG_RE.fullmatch(slug):
            add(f"project:{slug}", root / "projects" / slug / "knowledge")
    projects_base = root / "projects"
    if projects_base.is_dir():
        for child in sorted(projects_base.iterdir()):
            if child.is_dir() and knowledge._SLUG_RE.fullmatch(child.name):
                add(f"project:{child.name}", child / "knowledge")

    apps_base = root / "apps"
    if apps_base.is_dir():
        for child in sorted(apps_base.iterdir()):
            if child.is_dir() and knowledge._SLUG_RE.fullmatch(child.name):
                add(f"app:{child.name}", child / "knowledge")

    agents_base = runtime / "agents"
    if agents_base.is_dir():
        for child in sorted(agents_base.iterdir()):
            if child.is_dir() and knowledge._SLUG_RE.fullmatch(child.name):
                add(f"agent:{child.name}", child / "knowledge")

    return order


# ---- Aggregate list --------------------------------------------------------


class NoteRow(BaseModel):
    """One note in the cross-scope view, tagged with the scope it resolved in
    plus the provenance the Memory page shows per row. `_mtime` is stripped by
    `list_notes_meta` on purpose -- it is a ranking input, not something a
    reader should depend on."""

    id: str
    title: str
    hook: str
    updated: str | None = None
    scope: str
    author: str | None = None
    chat: str | None = None


class AllNotes(BaseModel):
    """`scopes` is how many scopes were actually READ, not how many exist, so a
    filtered request reports the size of what it looked at."""

    notes: list[NoteRow]
    count: int
    scopes: int


class ScopeList(BaseModel):
    """Only scopes that already HAVE a knowledge directory, so enumerating never
    creates an empty one."""

    scopes: list[str]


class ScopeNode(BaseModel):
    """The same keys as the flat list, shaped so a picker can convey containment
    by indentation alone without building a tree widget. `parent` is null when
    the real parent is not itself a visible scope."""

    key: str
    parent: str | None = None
    depth: int


class ScopeTree(BaseModel):
    scopes: list[ScopeNode]


@router.get("/notes", response_model=AllNotes, response_model_exclude_unset=True)
async def list_all(scope: str | None = None, q: str | None = None) -> dict:
    """Notes across every scope, each tagged with its resolved `scope` key plus
    `author` / `type` / `pinned` (design doc sec 11: provenance and the ranked-
    digest facets, shown per row on the Memory page).

    `scope` filters to that scope AND EVERYTHING IT REACHES (`scope_tree.reach`,
    sec 4.2: "its own and everything else below it"), not an exact match --
    selecting `workshop` also shows `project:*` / `app:*` / unattached agents.
    An unparseable scope falls back to an exact match rather than 400ing, since
    a stale `?scope=` deep link should narrow to nothing found, not break the
    page. `q` filters by a plaintext match (delegated to `knowledge.search`,
    which scans title, hook, and body)."""
    from services import scope_tree
    reach_set: set[str] | None = None
    if scope:
        try:
            reach_set = set(scope_tree.reach(scope))
        except ValueError:
            reach_set = {scope}
    out: list[dict] = []
    scopes = 0
    query = (q or "").strip()
    for skey, _root in _scope_roots():
        if reach_set is not None and skey not in reach_set:
            continue
        scopes += 1
        try:
            notes = knowledge.list_notes_meta(skey)
        except Exception:
            continue
        if query:
            try:
                hits = await knowledge.search(skey, query, limit=1000)
                ids = {h.get("note_id") for h in hits}
            except Exception:
                ids = set()
            notes = [n for n in notes if n["id"] in ids]
        for n in notes:
            rec = dict(n)
            rec["scope"] = skey
            out.append(rec)
    return {"notes": out, "count": len(out), "scopes": scopes}


@router.get("/scopes", response_model=ScopeList, response_model_exclude_unset=True)
async def list_scopes() -> dict:
    """The scope keys that currently have a knowledge directory, for the
    filter/create controls. Cheap directory + registry scan, no note load."""
    return {"scopes": [skey for skey, _ in _scope_roots()]}


@router.get("/scopes/tree", response_model=ScopeTree, response_model_exclude_unset=True)
async def list_scopes_tree() -> dict:
    """The same scope keys as `/scopes`, shaped as a tree instead of a flat
    list (design doc sec 11: "a scope filter shaped like the TREE, not a flat
    list"). Each entry carries its `parent` and a `depth`, in root-first
    depth-first order, so a picker can convey containment by indentation alone
    without building a tree widget.

    A PARENT THAT IS NOT VISIBLE IS SKIPPED, NOT DROPPED. This enumerates the
    scopes that have a knowledge DIRECTORY, while `scope_tree.parent` answers
    for the whole model, so a real parent can be absent here (an app with no
    notes of its own still contains its project). Attaching such a child to the
    root instead of to its nearest visible ancestor promotes it above scopes
    that genuinely contain it: measured 2026-08-23, when `project:<slug>` moved
    under `app:<slug>`, four projects whose app had no notes jumped from under
    `workshop` to the top level, as siblings of `development`. Walking up keeps
    containment truthful under partial visibility, and the `seen` guard means a
    cycle in a hand-edited `.runtime/scope-tree.json` cannot hang the request."""
    from services import scope_tree
    keys = [skey for skey, _ in _scope_roots()]
    key_set = set(keys)

    def _visible_parent(key: str) -> str | None:
        seen: set[str] = {key}
        cur = key
        while True:
            try:
                nxt = scope_tree.parent(cur)
            except ValueError:
                return None
            if nxt is None or nxt in seen:
                return None
            if nxt in key_set:
                return nxt
            seen.add(nxt)
            cur = nxt

    parent_of: dict[str, str | None] = {k: _visible_parent(k) for k in keys}
    children: dict[str | None, list[str]] = {}
    for k in keys:
        children.setdefault(parent_of[k], []).append(k)
    for kids in children.values():
        kids.sort()

    out: list[dict] = []

    def walk(key: str, depth: int) -> None:
        out.append({"key": key, "parent": parent_of[key], "depth": depth})
        for child in children.get(key, []):
            walk(child, depth + 1)

    for root in children.get(None, []):
        walk(root, 0)
    return {"scopes": out}
