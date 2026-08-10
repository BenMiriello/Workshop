import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import paths

router = APIRouter(prefix="/auto-pr", tags=["auto-pr"])

SECRETS = paths.config("secrets.env")
AUTO_PRS_DIR = paths.runtime("auto-prs")
AUTO_PR_SCRIPT = paths.scripts("auto_pr.sh")

AUTO_PR_SWITCH = "WORKSHOP_AUTO_PR"


def _load_auto_pr_mode() -> str:
    """Returns '1' if WORKSHOP_AUTO_PR=1 is set in the env or secrets.env."""
    if AUTO_PR_SWITCH in os.environ:
        return os.environ[AUTO_PR_SWITCH]
    if SECRETS.exists():
        for line in SECRETS.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{AUTO_PR_SWITCH}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0"


class AutoPRRequest(BaseModel):
    repo: str = ""
    title: str
    body: str
    diff: str = ""
    log_ref: str = ""


class AutoPRResponse(BaseModel):
    mode: str
    output_path: str = ""
    pr_url: str = ""
    message: str = ""


@router.post("", response_model=AutoPRResponse)
async def create_auto_pr(req: AutoPRRequest):
    if not req.title or not req.body:
        raise HTTPException(status_code=400, detail="title and body are required")

    mode = "real" if _load_auto_pr_mode() == "1" else "dry-run"

    cmd = [str(AUTO_PR_SCRIPT), "--title", req.title, "--body", req.body]
    if req.repo:
        cmd += ["--repo", req.repo]
    if req.diff:
        cmd += ["--diff", req.diff]
    if req.log_ref:
        cmd += ["--log-ref", req.log_ref]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="auto_pr.sh timed out")

    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"auto_pr.sh failed: {result.stderr.strip() or result.stdout.strip()}",
        )

    output = result.stdout.strip()

    # Extract path or PR URL from script output
    pr_url = ""
    output_path = ""
    if "written to " in output:
        output_path = output.split("written to ")[-1].strip().split()[0]
    if "real mode:" in output:
        for token in output.split():
            if token.startswith("https://"):
                pr_url = token
        for line in output.splitlines():
            if "body also at:" in line:
                output_path = line.split("body also at:")[-1].strip()

    return AutoPRResponse(mode=mode, output_path=output_path, pr_url=pr_url, message=output)


@router.get("/list")
async def list_auto_prs():
    """List recent dry-run PR files."""
    AUTO_PRS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(AUTO_PRS_DIR.glob("*.md"), reverse=True)
    results = []
    for f in files[:20]:
        lines = f.read_text().splitlines()
        title = lines[0].lstrip("# ").strip() if lines else f.stem
        results.append({"file": f.name, "title": title, "path": str(f)})
    return {"prs": results, "count": len(results)}


@router.get("/mode")
async def get_mode():
    """Return current auto-PR mode (dry-run or real)."""
    mode = "real" if _load_auto_pr_mode() == "1" else "dry-run"
    return {"mode": mode, "switch": f"{AUTO_PR_SWITCH} in {SECRETS}"}
