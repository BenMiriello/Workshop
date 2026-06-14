"""Base Apps — tenant-level git-repo listing.

Returns one record per top-level directory containing a `.git/`, with
branch and last-commit metadata. Previously mounted at /projects/ (named
to match the original top-nav tab); moved to /base-apps/ per ADR-008 so
"/projects/" can host the operator's Projects hub.
"""
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException

from services import manifests

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
