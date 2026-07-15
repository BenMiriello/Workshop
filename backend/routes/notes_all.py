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
  project:<slug>  -> projects/<slug>/knowledge/
  app:<name>      -> apps/<name>/knowledge/
  agent:<slug>    -> .runtime/agents/<slug>/knowledge/

Enumeration surfaces only scopes whose knowledge directory already exists, so a
listing never creates an empty scope (knowledge.list_notes / scope_dir mkdir on
an existing directory is a no-op).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter

from services import knowledge, ownership

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
