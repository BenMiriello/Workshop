import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

PROJECTS_ROOT = Path(os.environ.get("WORKSHOP_PROJECTS_ROOT", str(Path.home())))


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
async def list_projects():
    if not PROJECTS_ROOT.exists():
        raise HTTPException(404, "projects root missing")
    out = []
    for entry in sorted(PROJECTS_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if not (entry / ".git").exists():
            continue
        meta = _git_meta(entry)
        out.append({"name": entry.name, "path": str(entry), **(meta or {})})
    return out
