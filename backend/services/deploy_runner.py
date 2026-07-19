"""Runs a Deploy end to end and writes progress into the Job feed.

Fire-and-forget: routes call ``kick(deploy_id, ref, message, trigger)`` and
get back a job_id immediately. A background thread walks preflight → prepare
workdir → build → upload → status → land, writing a per-run log file and
finishing the Job at the end.

The Job's binding carries {deploy_id, ref, commit_sha, upload_ref} so the
UI can filter runs by deploy and the rollback path can find a prior upload.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import deploys, deploy_providers, jobs, secrets_store


WORKSHOP_ROOT = Path(os.environ.get("WORKSHOP_ROOT", str(Path(__file__).resolve().parents[2])))
RUNS_ROOT = Path(os.environ.get(
    "WORKSHOP_DEPLOY_RUNS_ROOT",
    str(WORKSHOP_ROOT / ".runtime" / "deploys"),
))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log_path(deploy_id: str, run_id: str) -> Path:
    return RUNS_ROOT / deploy_id / "runs" / f"{run_id}.log"


def _tail(text: str, lines: int = 40) -> str:
    if not text:
        return ""
    parts = text.splitlines()
    return "\n".join(parts[-lines:])


def _resolve_project_dir(project_slug: str) -> Path | None:
    """Look up the project's `dir` from the registry. Kept as a thin helper so
    the runner never imports the projects router (avoids a startup cycle)."""
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


def _git(*args: str, cwd: Path, log: list[str]) -> tuple[int, str]:
    log.append("$ git " + " ".join(args))
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.append(f"[git error] {e}")
        return 1, ""
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    if out:
        log.append(out)
    return proc.returncode, (proc.stdout or "").strip()


def _prepare_workdir(deploy_id: str, run_id: str, project_dir: Path, ref: str | None,
                     log: list[str]) -> tuple[Path | None, str | None]:
    """Populate a fresh workdir with the project's source at the requested ref.

    Uses ``git worktree add`` when possible so we get a clean detached
    checkout at the exact ref without cloning; falls back to ``git clone``
    when the project isn't a git repo. Returns (workdir, commit_sha)."""
    workdir = RUNS_ROOT / deploy_id / "runs" / run_id / "src"
    workdir.parent.mkdir(parents=True, exist_ok=True)
    if workdir.exists():
        shutil.rmtree(workdir)
    dot_git = project_dir / ".git"
    if not dot_git.exists():
        log.append(f"[prepare] project dir has no .git — copying tree wholesale from {project_dir}")
        try:
            shutil.copytree(project_dir, workdir, symlinks=True,
                            ignore=shutil.ignore_patterns("node_modules", ".venv", "__pycache__"))
        except OSError as e:
            log.append(f"[prepare] copy failed: {e}")
            return None, None
        return workdir, None
    target_ref = ref or ""
    if not target_ref:
        rc, out = _git("rev-parse", "HEAD", cwd=project_dir, log=log)
        target_ref = out if rc == 0 else "HEAD"
    rc, _ = _git("worktree", "add", "--detach", str(workdir), target_ref,
                 cwd=project_dir, log=log)
    if rc != 0:
        log.append("[prepare] worktree add failed; falling back to clone")
        rc, _ = _git("clone", str(project_dir), str(workdir), cwd=project_dir.parent, log=log)
        if rc != 0:
            return None, None
        _git("checkout", target_ref, cwd=workdir, log=log)
    rc, sha = _git("rev-parse", "HEAD", cwd=workdir, log=log)
    return workdir, (sha if rc == 0 else None)


def _cleanup_worktree(deploy_id: str, run_id: str, project_dir: Path, log: list[str]) -> None:
    """Best-effort worktree remove — keeps the log dir but frees the checkout."""
    workdir = RUNS_ROOT / deploy_id / "runs" / run_id / "src"
    if not workdir.exists():
        return
    if (project_dir / ".git").exists():
        _git("worktree", "remove", "--force", str(workdir), cwd=project_dir, log=log)
    if workdir.exists():
        try:
            shutil.rmtree(workdir)
        except OSError:
            pass


def _write_log(deploy_id: str, run_id: str, lines: list[str]) -> None:
    p = _log_path(deploy_id, run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(line if line.endswith("\n") else line + "\n")
    lines.clear()


def _env_for_deploy(deploy: dict, provider) -> tuple[dict, list[str]]:
    """Load env for this provider from per-project secrets with workspace
    fallback. Returns (env_dict, missing_required_keys)."""
    keys = [r["key"] for r in provider.env_requirements()]
    env = secrets_store.env_with_workspace(deploy["project_slug"], keys)
    missing = [
        r["key"] for r in provider.env_requirements()
        if r.get("required") and not env.get(r["key"])
    ]
    return env, missing


def _run_deploy(deploy_id: str, ref: str | None, message: str | None,
                trigger: str, job_id: str) -> None:
    log: list[str] = []
    log.append(f"[{_now_iso()}] deploy start id={deploy_id} ref={ref or 'HEAD'} trigger={trigger}")
    if message:
        log.append(f"[message] {message}")
    try:
        deploy = deploys.get(deploy_id)
        if not deploy:
            log.append("[fatal] deploy record not found")
            _write_log(deploy_id, job_id, log)
            jobs.finish(job_id, status="failed", evidence={"summary": "deploy not found"})
            return

        provider = deploy_providers.get(deploy["target"]["kind"])
        env, missing = _env_for_deploy(deploy, provider)
        if missing:
            log.append(f"[preflight] missing env: {missing}")
            _write_log(deploy_id, job_id, log)
            jobs.finish(job_id, status="failed",
                        evidence={"summary": f"preflight: missing env {missing}",
                                  "missing_keys": missing})
            return
        pf = provider.preflight(deploy, env)
        if not pf.ok:
            log.append(f"[preflight] failed: {pf.detail}")
            _write_log(deploy_id, job_id, log)
            jobs.finish(job_id, status="failed",
                        evidence={"summary": f"preflight: {pf.detail}",
                                  "missing_keys": pf.missing_keys})
            return
        log.append("[preflight] ok")

        project_dir = _resolve_project_dir(deploy["project_slug"])
        if not project_dir or not project_dir.exists():
            log.append(f"[prepare] project dir not found for {deploy['project_slug']}")
            _write_log(deploy_id, job_id, log)
            jobs.finish(job_id, status="failed",
                        evidence={"summary": "project directory not found"})
            return

        workdir, commit_sha = _prepare_workdir(deploy_id, job_id, project_dir, ref, log)
        if not workdir:
            _write_log(deploy_id, job_id, log)
            jobs.finish(job_id, status="failed",
                        evidence={"summary": "prepare workdir failed"})
            return
        log.append(f"[prepare] workdir={workdir} sha={commit_sha}")
        _write_log(deploy_id, job_id, log)

        def log_write(chunk: str) -> None:
            log.append(chunk)
            _write_log(deploy_id, job_id, log)

        br = provider.build(deploy, workdir, log_write)
        if not br.ok:
            log.append(f"[build] failed: {br.detail}")
            _write_log(deploy_id, job_id, log)
            _cleanup_worktree(deploy_id, job_id, project_dir, log)
            _write_log(deploy_id, job_id, log)
            jobs.finish(job_id, status="failed",
                        evidence={"summary": f"build: {br.detail}",
                                  "log_tail": _tail(_log_path(deploy_id, job_id).read_text(errors="replace"))})
            return
        log.append(f"[build] ok output={br.output_dir}")
        _write_log(deploy_id, job_id, log)

        # Effective commit sha: build may have picked it up; else our own probe.
        commit_sha = br.commit_sha or commit_sha

        ur = provider.upload(deploy, br, env, log_write)
        if not ur.ok:
            log.append(f"[upload] failed: {ur.detail}")
            _write_log(deploy_id, job_id, log)
            _cleanup_worktree(deploy_id, job_id, project_dir, log)
            _write_log(deploy_id, job_id, log)
            jobs.finish(job_id, status="failed",
                        evidence={"summary": f"upload: {ur.detail}",
                                  "log_tail": _tail(_log_path(deploy_id, job_id).read_text(errors="replace"))})
            return
        log.append(f"[upload] ok ref={ur.upload_ref} url={ur.url}")
        _write_log(deploy_id, job_id, log)

        # Some providers finish synchronously; poll once so the recorded status
        # reflects the target's view (mismatch surfaces here, not in the UI).
        st = provider.status(deploy, ur.upload_ref or "", env)
        log.append(f"[status] {st.kind} {st.detail or ''}")
        _write_log(deploy_id, job_id, log)

        _cleanup_worktree(deploy_id, job_id, project_dir, log)
        _write_log(deploy_id, job_id, log)

        if st.kind == "failure":
            jobs.finish(job_id, status="failed",
                        evidence={"summary": f"target status: {st.detail}",
                                  "url": st.url or ur.url,
                                  "log_tail": _tail(_log_path(deploy_id, job_id).read_text(errors="replace"))})
            return

        landed = {
            "run_id": job_id,
            "commit_sha": commit_sha,
            "deployed_at": _now_iso(),
            "url": st.url or ur.url,
            "upload_ref": ur.upload_ref,
        }
        deploys.set_current(deploy_id, landed)
        jobs.finish(job_id, status="done",
                    evidence={"summary": f"deployed to {landed['url']}",
                              "commit_sha": commit_sha,
                              "url": landed["url"],
                              "upload_ref": ur.upload_ref,
                              "log_tail": _tail(_log_path(deploy_id, job_id).read_text(errors="replace"))})
    except Exception as e:
        log.append(f"[fatal] {type(e).__name__}: {e}")
        _write_log(deploy_id, job_id, log)
        try:
            jobs.finish(job_id, status="failed",
                        evidence={"summary": f"runner crashed: {type(e).__name__}: {e}"})
        except Exception:
            pass


def kick(deploy_id: str, *, ref: str | None = None, message: str | None = None,
         trigger: str = "manual") -> str:
    """Fire a deploy in a background thread. Returns the Job id immediately.
    The Job's binding carries {deploy_id, ref} so downstream filters work."""
    deploy = deploys.get(deploy_id)
    if not deploy:
        raise KeyError(deploy_id)
    if deploy["target"]["kind"] not in deploy_providers.kinds():
        raise ValueError(f"unknown provider: {deploy['target']['kind']}")
    rec = jobs.start(
        project_slug=deploy["project_slug"],
        trigger=trigger,
        status="running",
        kind="deploy",
        binding={"deploy_id": deploy_id, "ref": ref or "HEAD"},
    )
    job_id = rec["id"]
    deploys.set_last_run(deploy_id, job_id)
    t = threading.Thread(
        target=_run_deploy,
        args=(deploy_id, ref, message, trigger, job_id),
        name=f"deploy-{deploy_id}-{job_id}",
        daemon=True,
    )
    t.start()
    return job_id


def _run_rollback(deploy_id: str, to_run_id: str, job_id: str) -> None:
    log: list[str] = []
    log.append(f"[{_now_iso()}] rollback start id={deploy_id} to_run={to_run_id}")
    try:
        deploy = deploys.get(deploy_id)
        if not deploy:
            log.append("[fatal] deploy not found")
            _write_log(deploy_id, job_id, log)
            jobs.finish(job_id, status="failed", evidence={"summary": "deploy not found"})
            return
        prior = jobs.get(to_run_id)
        if not prior or prior.get("status") != "done":
            log.append(f"[fatal] prior job not usable: {prior}")
            _write_log(deploy_id, job_id, log)
            jobs.finish(job_id, status="failed",
                        evidence={"summary": "prior run not a successful deploy"})
            return
        prior_evidence = prior.get("evidence") or {}
        upload_ref = prior_evidence.get("upload_ref") or ""
        provider = deploy_providers.get(deploy["target"]["kind"])
        env, missing = _env_for_deploy(deploy, provider)
        if missing:
            log.append(f"[preflight] missing env: {missing}")
            _write_log(deploy_id, job_id, log)
            jobs.finish(job_id, status="failed",
                        evidence={"summary": f"preflight: missing env {missing}"})
            return
        env["_UPLOAD_REF"] = upload_ref

        def log_write(chunk: str) -> None:
            log.append(chunk)
            _write_log(deploy_id, job_id, log)

        rb = provider.rollback(deploy, to_run_id, env, log_write)
        if not rb.ok:
            log.append(f"[rollback] failed: {rb.detail}")
            _write_log(deploy_id, job_id, log)
            jobs.finish(job_id, status="failed",
                        evidence={"summary": f"rollback: {rb.detail}"})
            return

        landed = {
            "run_id": job_id,
            "commit_sha": prior_evidence.get("commit_sha"),
            "deployed_at": _now_iso(),
            "url": rb.url or prior_evidence.get("url"),
            "upload_ref": upload_ref,
        }
        deploys.set_current(deploy_id, landed)
        jobs.finish(job_id, status="done",
                    evidence={"summary": f"rolled back to {landed['url']}",
                              "url": landed["url"],
                              "upload_ref": upload_ref,
                              "log_tail": _tail(_log_path(deploy_id, job_id).read_text(errors="replace"))
                                          if _log_path(deploy_id, job_id).exists() else ""})
    except Exception as e:
        log.append(f"[fatal] {type(e).__name__}: {e}")
        _write_log(deploy_id, job_id, log)
        try:
            jobs.finish(job_id, status="failed",
                        evidence={"summary": f"rollback crashed: {type(e).__name__}: {e}"})
        except Exception:
            pass


def kick_rollback(deploy_id: str, to_run_id: str, *, trigger: str = "manual") -> str:
    deploy = deploys.get(deploy_id)
    if not deploy:
        raise KeyError(deploy_id)
    rec = jobs.start(
        project_slug=deploy["project_slug"],
        trigger=trigger,
        status="running",
        kind="deploy-rollback",
        binding={"deploy_id": deploy_id, "to_run_id": to_run_id},
    )
    job_id = rec["id"]
    deploys.set_last_run(deploy_id, job_id)
    t = threading.Thread(
        target=_run_rollback,
        args=(deploy_id, to_run_id, job_id),
        name=f"deploy-rollback-{deploy_id}-{job_id}",
        daemon=True,
    )
    t.start()
    return job_id
