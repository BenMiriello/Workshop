"""Auto-deploy watcher.

Every WATCH_INTERVAL seconds, iterate every Deploy with autonomy="autonomous"
and check whether the source branch has moved past the currently-deployed
commit_sha. When it has, kick a deploy (trigger="event"). When either
autonomy override (workshop or deploys-scope) is engaged, a detected change
is LOGGED but not fired; the next tick after the override clears will pick
it up automatically.

Design decisions:
- Uses `git ls-remote` for the sha check (works for any git remote, no
  webhook setup needed). Webhook path is a separate route in routes/deploys
  that calls tick_one(deploy_id, sha) directly — same underlying kick.
- Never fires two deploys concurrently for the same Deploy. If a deploy is
  already running (jobs.read status=running kind=deploy binding.deploy_id
  matches), the tick skips that deploy_id this round.
- Failure to reach the remote is treated as "no change" (safe default) — a
  transient git network error must not fire a fresh deploy on stale state.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import autonomy_override, deploys, deploys_override, deploy_runner, jobs


WATCH_INTERVAL = int(os.environ.get("WORKSHOP_DEPLOY_WATCH_INTERVAL", "300"))
WORKSHOP_ROOT = Path(os.environ.get("WORKSHOP_ROOT", str(Path(__file__).resolve().parents[2])))
NOTICES_ROOT = WORKSHOP_ROOT / ".runtime" / "deploys"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve_project_dir(project_slug: str) -> Path | None:
    reg = Path(os.environ.get(
        "WORKSHOP_PROJECTS_REGISTRY",
        str(WORKSHOP_ROOT / ".runtime" / "projects.json"),
    ))
    if not reg.exists():
        return None
    try:
        data = json.loads(reg.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    p = (data.get("projects") or {}).get(project_slug) or {}
    d = p.get("dir")
    return Path(d) if d else None


def _remote_sha(project_dir: Path, branch: str) -> str | None:
    """Latest sha on `branch` at the project's `origin` remote.
    Returns None on any failure (never raises)."""
    if not (project_dir / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    line = (proc.stdout or "").strip().splitlines()[:1]
    if not line:
        return None
    parts = line[0].split()
    if not parts:
        return None
    return parts[0].strip() or None


def _running_for(deploy_id: str) -> bool:
    """True if a deploy Job for this deploy_id is currently running or queued."""
    for r in jobs.read(kind="deploy", binding_key="deploy_id", binding_value=deploy_id, limit=10):
        if r.get("status") in ("running", "queued", "awaiting_input"):
            return True
    for r in jobs.read(kind="deploy-rollback", binding_key="deploy_id", binding_value=deploy_id, limit=10):
        if r.get("status") in ("running", "queued", "awaiting_input"):
            return True
    return False


def _notice_path(deploy_id: str) -> Path:
    return NOTICES_ROOT / deploy_id / "pending-notice.json"


def _write_notice(deploy_id: str, sha: str, branch: str, reason: str) -> None:
    """Record 'change detected but paused' so the operator can see it in
    the UI even if no Job was fired. Overwritten by the next tick."""
    p = _notice_path(deploy_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "deploy_id": deploy_id,
        "branch": branch,
        "remote_sha": sha,
        "detected_at": _now_iso(),
        "reason": reason,
    }
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_notice(deploy_id: str) -> dict[str, Any] | None:
    p = _notice_path(deploy_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def clear_notice(deploy_id: str) -> None:
    try:
        _notice_path(deploy_id).unlink()
    except FileNotFoundError:
        pass


def tick_one(deploy_id: str, *, force: bool = False) -> dict:
    """Evaluate a single Deploy. Return a dict describing what was decided:
        {status: "no-remote"|"no-change"|"kicked"|"paused"|"skipped-running",
         sha: <str|None>, job_id: <str|None>, reason: <str|None>}
    `force=True` skips the sha-matches-current short-circuit (webhook path
    calls tick_one with force so a re-push of the same commit still fires)."""
    deploy = deploys.get(deploy_id)
    if not deploy:
        return {"status": "no-deploy", "reason": "not found"}
    if deploy.get("autonomy") != "autonomous":
        return {"status": "not-autonomous"}
    project_dir = _resolve_project_dir(deploy["project_slug"])
    if not project_dir:
        return {"status": "no-project-dir"}
    branch = (deploy.get("source") or {}).get("branch") or "main"
    remote = _remote_sha(project_dir, branch)
    if not remote:
        return {"status": "no-remote", "reason": "ls-remote failed"}
    current = ((deploy.get("current") or {}).get("commit_sha") or "").strip()
    if not force and current and remote.startswith(current[:7]) or (not force and current == remote):
        return {"status": "no-change", "sha": remote}
    if _running_for(deploy_id):
        return {"status": "skipped-running", "sha": remote}
    if autonomy_override.is_engaged() or deploys_override.is_engaged():
        reason = "workshop autonomy override engaged" if autonomy_override.is_engaged() else "deploys override engaged"
        _write_notice(deploy_id, remote, branch, reason)
        return {"status": "paused", "sha": remote, "reason": reason}
    # Clear any stale notice, then fire.
    clear_notice(deploy_id)
    try:
        job_id = deploy_runner.kick(deploy_id, ref=branch, message=f"auto-deploy: {branch}@{remote[:7]}", trigger="event")
    except deploy_runner.OverrideEngaged as e:
        _write_notice(deploy_id, remote, branch, str(e))
        return {"status": "paused", "sha": remote, "reason": str(e)}
    except (KeyError, ValueError) as e:
        return {"status": "error", "reason": str(e)}
    return {"status": "kicked", "sha": remote, "job_id": job_id}


def tick_all() -> list[dict]:
    """One pass over every Deploy. Returns per-deploy result dicts (for the
    manual tick route). Errors are captured, not raised — a broken remote
    for one Deploy must not stop the others."""
    out: list[dict] = []
    for rec in deploys.list_all():
        try:
            r = tick_one(rec["id"])
        except Exception as e:
            r = {"status": "crashed", "reason": f"{type(e).__name__}: {e}"}
        r["deploy_id"] = rec["id"]
        r["project_slug"] = rec.get("project_slug")
        r["name"] = rec.get("name")
        out.append(r)
    return out


async def watcher_loop() -> None:
    """Background loop — sleep then tick_all, forever. Errors are logged and
    swallowed so the loop never dies. Started from main.py lifespan."""
    while True:
        try:
            await asyncio.sleep(WATCH_INTERVAL)
            # Run the sync tick in a thread so a slow git ls-remote never blocks
            # the event loop for other tasks.
            await asyncio.to_thread(tick_all)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Best-effort: log via jobs (a diagnostic Job so a crash is visible
            # in the feed rather than eaten silently).
            try:
                jobs.start(
                    trigger="loop", kind="deploy-watcher-error", status="failed",
                    binding={"phase": "loop"},
                )
            except Exception:
                pass
