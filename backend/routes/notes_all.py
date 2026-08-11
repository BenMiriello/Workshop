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

from services import app_runner, knowledge, ownership

router = APIRouter(prefix="/notes", tags=["notes"])

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


def _scope_roots() -> list[tuple[str, Path]]:
    """Every knowledge scope that actually has a directory, deduped by resolved
    path. Priority order: workshop root, projects (registry then any stray on
    disk), apps, agents. Only an existing knowledge dir is surfaced, so listing
    never creates a scope."""
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

    ws = root / "shared" / "knowledge"
    add("workshop", ws)

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
    if app_runner.dev_mode_on():
        dev = ws / "development"
        try:
            key = str(dev.resolve())
        except OSError:
            key = str(dev)
        if key not in seen:
            seen.add(key)
            order.append(("development", dev))

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

@router.get("")
async def list_all(scope: str | None = None, q: str | None = None) -> dict:
    """Notes across every scope, each tagged with its resolved `scope` key.
    Optional `scope` restricts to one scope; optional `q` filters by a plaintext
    match (delegated to `knowledge.search`, which scans title, hook, and body)."""
    out: list[dict] = []
    scopes = 0
    query = (q or "").strip()
    for skey, _root in _scope_roots():
        if scope and skey != scope:
            continue
        scopes += 1
        try:
            notes = knowledge.list_notes(skey)
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


@router.get("/scopes")
async def list_scopes() -> dict:
    """The scope keys that currently have a knowledge directory, for the
    filter/create controls. Cheap directory + registry scan, no note load."""
    return {"scopes": [skey for skey, _ in _scope_roots()]}
