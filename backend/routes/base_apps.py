"""Base Apps — tenant-level git-repo listing.

Returns one record per top-level directory containing a `.git/`, with
branch and last-commit metadata. Previously mounted at /projects/ (named
to match the original top-nav tab); moved to /base-apps/ per ADR-008 so
"/projects/" can host the operator's Projects hub.
"""
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from services import gc, manifests, permissions, registry

router = APIRouter()

# Defaults to the tenant home. Configurable to scan a different root if needed.
BASE_APPS_ROOT = Path(os.environ.get("WORKSHOP_BASE_APPS_ROOT", str(Path.home())))


def _git_meta(repo: Path):
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%H%n%s%n%ci"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip().splitlines()
        commit, subject, ts = out[0], out[1], out[2]
        branch = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        return {"commit": commit, "subject": subject, "ts": ts, "branch": branch}
    except Exception:
        return None


@router.get("/")
async def list_base_apps():
    if not BASE_APPS_ROOT.exists():
        raise HTTPException(404, "base apps root missing")
    out = []
    for entry in sorted(BASE_APPS_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if not (entry / ".git").exists():
            continue
        meta = _git_meta(entry)
        out.append({"name": entry.name, "path": str(entry), **(meta or {})})
    return out


# ---- App manifests (data layer only — no enforcement) -----------------------
# Read-only view of the per-app manifest store (backend/apps.json). See
# services/manifests.py and docs/app-platform-plan.md section 3. These endpoints
# expose the declared manifests; nothing here enforces scope or grants.


@router.get("/manifests")
async def get_app_manifests():
    """Return the full app manifest map: {"apps": {<id>: {...}}}."""
    return {"apps": manifests.load_manifests()}


@router.get("/manifests/{app_id}")
async def get_app_manifest(app_id: str):
    """Return a single app's manifest, or 404 if it is not declared."""
    entry = manifests.get_manifest(app_id)
    if entry is None:
        raise HTTPException(404, f"app manifest not found: {app_id}")
    return entry


# ---- Grants + permissions (the authz source of truth) -----------------------
# Grants are operator-owned. These endpoints sit behind the same loopback-trusted
# `_SECURE` deps the rest of base-apps uses (see main.py); they add NO new auth
# surface and impose NO middleware on existing routes. The framework is a
# CONSULTED source of truth — default-permissive for the operator — not a wall.


@router.get("/grants")
async def get_grants():
    """Return the operator-owned grants document {"grants": [...], "guests": [...]}.
    Auto-created empty on first read; tolerant of a corrupt file (serves empty)."""
    return permissions.load_grants()


@router.put("/grants")
async def put_grants(doc: dict = Body(...)):
    """Replace the full grants document. Validates the top-level shape (400 on a
    bad shape) and atomically saves. Operator-only conceptually; loopback-trusted
    like the existing manifest pattern — no new auth surface."""
    try:
        return permissions.save_grants(doc)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/permissions/{app_id}")
async def get_app_permissions(app_id: str):
    """Assistant capabilities for an app under the operator identity: what a data
    assistant bound to this app may actually do (can_read, resolved action
    policies, reachable apps). This is what the chat layer consults so an
    assistant's preamble states its real allowed actions (doc 01 section 3)."""
    if manifests.get_manifest(app_id) is None:
        raise HTTPException(404, f"app manifest not found: {app_id}")
    return permissions.assistant_capabilities(app_id, permissions.OPERATOR_IDENTITY)


# ---- Marketplace / registry (LOCAL dev version) -----------------------------
# The local app + kit registry (docs/workshop-v1/03-ui-kit-and-marketplace.md
# sections 4-5). Publish bundles a project's git history into a local release;
# install/fork clones that bundle into the workspace and registers it as a new
# project (reusing the projects hub registry as the single source of truth).
# No network: releases are git bundles on disk. See services/registry.py.


class PublishIn(BaseModel):
    slug: str
    version: str | None = None
    kind: str = "app"
    description: str = ""


class InstallIn(BaseModel):
    target_slug: str | None = None


@router.get("/registry")
async def list_registry():
    """Return the registry catalog: {"items": [...]}."""
    return {"items": registry.list_items()}


@router.get("/registry/{item_id}")
async def get_registry_item(item_id: str):
    """Return a single registry item, or 404 if it is not published."""
    item = registry.get_item(item_id)
    if item is None:
        raise HTTPException(404, f"registry item not found: {item_id}")
    return item


@router.post("/registry/publish")
async def publish_registry_item(body: PublishIn):
    """Publish a project as a new local release. 404 if the project is unknown,
    400 if it is not a git repo with at least one commit."""
    kind = body.kind if body.kind in ("app", "kit") else "app"
    try:
        return registry.publish(
            body.slug.strip(),
            version=body.version,
            kind=kind,
            description=body.description or "",
        )
    except registry.RegistryError as e:
        raise HTTPException(e.status, e.message)


@router.post("/registry/{item_id}/install")
async def install_registry_item(item_id: str, body: InstallIn = Body(default=InstallIn())):
    """Fork a registry item into the workspace and register it as a project.
    404 if the item is missing, 409 if the target slug exists and cannot be
    auto-suffixed. Returns the new project entry (with from_registry provenance)."""
    try:
        return registry.install(item_id, target_slug=body.target_slug)
    except registry.RegistryError as e:
        raise HTTPException(e.status, e.message)


@router.get("/registry/projects/{slug}/status")
async def registry_project_status(slug: str):
    """Whether a newer upstream release is available for an installed project.
    Returns {current, latest, update_available}. 404 if the project is unknown
    or has no registry provenance (was not installed from the registry)."""
    try:
        return registry.update_status(slug)
    except registry.RegistryError as e:
        raise HTTPException(e.status, e.message)


@router.post("/registry/projects/{slug}/update")
async def registry_project_update(slug: str):
    """Pull the project's latest upstream release and merge it into the current
    branch (doc 03 section 4, "update via branch merge"). On a clean merge:
    {updated: true, from, to, merged: true}; on conflict the merge is aborted and
    {updated: false, conflict: true, reason}; if already current {updated: false,
    reason: "up to date"}. 404 if the project has no from_registry provenance."""
    try:
        return registry.update_from_upstream(slug)
    except registry.RegistryError as e:
        raise HTTPException(e.status, e.message)


# ---- Anti-bloat GC (doc 05 section 7) ---------------------------------------
# Copies must not accumulate forever. preview reports everything reapable with
# no side effects; run performs only the SAFE reaps (merged worktrees + stopped
# dev-server runtimes). Checkpoint history is REPORTED, never rewritten here --
# history rewrite is exactly what the git-guard hook blocks for agents.


class GcRunIn(BaseModel):
    apply: bool = True


@router.get("/gc/preview")
async def gc_preview():
    """Report what is reapable: merged worktrees, per-project checkpoint counts
    (squashable beyond retention, report-only), and stopped dev-server runtimes.
    No destructive side effects."""
    return gc.preview()


@router.post("/gc/run")
async def gc_run(body: GcRunIn = Body(default=GcRunIn())):
    """Perform the SAFE reaps (merged worktrees + stopped runtimes) when
    apply=true; with apply=false this is equivalent to preview for those two
    categories. Checkpoint history is never rewritten."""
    return gc.run(apply=body.apply)
