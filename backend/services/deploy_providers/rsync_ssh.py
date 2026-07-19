"""rsync-ssh deploy provider — ship a build directory to a plain SSH host.

The point of this provider is to prove the seam: after CF Pages, we should
be able to add a whole new host with one file. This is the smallest useful
"host it yourself" target — no managed platform, no API. The operator gives
us `user@host:/path/to/webroot` and an SSH key alias; we rsync the built
output_dir there, atomically swapping to a new release directory so a bad
build doesn't leave a half-uploaded site.

Target config:
    {
      "kind": "rsync-ssh",
      "host": "user@example.test",                # ssh target (used verbatim)
      "path": "/srv/www/example",                 # webroot on the host
      "releases_subdir": "releases",              # optional; where snapshots land
      "current_symlink": "current"                # optional; symlink swap target
    }

Env requirements:
    SSH_KEY_PATH   - absolute path to a private key on the workshop host
                     that has authorized access on the remote host

Rollback: rsync-ssh keeps the last N releases in <path>/<releases_subdir>/;
rollback swaps the `current` symlink to a prior release. `_UPLOAD_REF`
(passed by the runner from the prior Job's evidence) is the release id.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from .base import BuildResult, Preflight, RollbackResult, Status, UploadResult


KIND = "rsync-ssh"
LABEL = "rsync + SSH"
SOURCE_KINDS = ["git-branch"]
TARGET_FIELDS = [
    {"key": "host", "required": True, "description": "SSH target, e.g. deploy@example.test"},
    {"key": "path", "required": True, "description": "Webroot on the remote host"},
    {"key": "releases_subdir", "required": False, "description": "Where release snapshots land (default: releases)"},
    {"key": "current_symlink", "required": False, "description": "Symlink pointing at the live release (default: current)"},
]


def env_requirements() -> list[dict]:
    return [
        {"key": "SSH_KEY_PATH", "required": True,
         "description": "Absolute path to an SSH private key the remote authorizes"},
    ]


def _ssh_prefix(env: dict) -> list[str]:
    key = env["SSH_KEY_PATH"]
    return [
        "ssh", "-i", key,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
    ]


def preflight(deploy: dict, env: dict) -> Preflight:
    missing = [k["key"] for k in env_requirements() if k["required"] and not env.get(k["key"])]
    if missing:
        return Preflight(ok=False, missing_keys=missing, detail="required env not set")
    target = deploy.get("target") or {}
    if not target.get("host") or not target.get("path"):
        return Preflight(ok=False, detail="target.host and target.path are required")
    key_path = env["SSH_KEY_PATH"]
    if not Path(key_path).is_file():
        return Preflight(ok=False, detail=f"SSH_KEY_PATH not readable: {key_path}")
    # Prove the ssh connection: `echo ok` on the remote.
    host = target["host"]
    try:
        proc = subprocess.run(
            [*_ssh_prefix(env), host, "echo", "ok"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return Preflight(ok=False, detail=f"ssh probe failed: {e}")
    if proc.returncode != 0 or "ok" not in (proc.stdout or ""):
        return Preflight(ok=False, detail=f"ssh probe exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
    return Preflight(ok=True)


def build(deploy: dict, workdir: Path, log_write: Callable[[str], None]) -> BuildResult:
    src = deploy.get("source") or {}
    b = src.get("build") or {}
    cmd = b.get("cmd")
    output_dir = b.get("output_dir") or "dist"
    if not cmd:
        return BuildResult(ok=False, detail="source.build.cmd is required")
    log_write(f"$ (cwd={workdir}) {cmd}\n")
    proc = subprocess.run(
        ["bash", "-c", cmd],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=int(b.get("timeout_sec") or 900),
    )
    log_write(proc.stdout or "")
    if proc.stderr:
        log_write("[stderr]\n" + proc.stderr)
    if proc.returncode != 0:
        return BuildResult(ok=False, output_dir=output_dir, detail=f"build exited {proc.returncode}")
    out = workdir / output_dir
    if not out.is_dir():
        return BuildResult(ok=False, output_dir=output_dir, detail=f"output_dir not found: {out}")
    return BuildResult(ok=True, output_dir=str(out))


def _release_id(deploy: dict) -> str:
    """Timestamped snapshot id — sortable, unambiguous. Uses the local wall
    clock; a colliding second across deploys is fine since the id is scoped
    per Deploy on the remote."""
    from datetime import datetime, timezone
    return "r-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def upload(deploy: dict, build_result: BuildResult, env: dict,
           log_write: Callable[[str], None]) -> UploadResult:
    if not build_result.ok or not build_result.output_dir:
        return UploadResult(ok=False, detail="no build artifact")
    target = deploy["target"]
    host = target["host"]
    path = target["path"].rstrip("/")
    releases = target.get("releases_subdir") or "releases"
    current = target.get("current_symlink") or "current"
    rid = _release_id(deploy)
    remote_release = f"{path}/{releases}/{rid}"

    # mkdir -p releases/<rid>
    log_write(f"# ensuring {remote_release}\n")
    proc = subprocess.run(
        [*_ssh_prefix(env), host, "mkdir", "-p", remote_release],
        capture_output=True, text=True, timeout=30,
    )
    log_write((proc.stdout or "") + (proc.stderr or ""))
    if proc.returncode != 0:
        return UploadResult(ok=False, detail=f"mkdir remote failed: {proc.returncode}")

    # rsync with delete inside the release dir (release itself is atomic).
    src_dir = build_result.output_dir.rstrip("/") + "/"
    rsync_cmd = [
        "rsync", "-az", "--delete",
        "-e", " ".join([*_ssh_prefix(env)]),
        src_dir, f"{host}:{remote_release}/",
    ]
    log_write(f"$ {' '.join(rsync_cmd)}\n")
    proc = subprocess.run(rsync_cmd, capture_output=True, text=True, timeout=1800)
    log_write(proc.stdout or "")
    if proc.stderr:
        log_write("[stderr]\n" + proc.stderr)
    if proc.returncode != 0:
        return UploadResult(ok=False, detail=f"rsync exited {proc.returncode}")

    # Atomic symlink swap: write a temp symlink, mv over the target.
    swap = (
        f"ln -sfn '{remote_release}' '{path}/{current}.new' && "
        f"mv -Tf '{path}/{current}.new' '{path}/{current}'"
    )
    log_write(f"# atomic swap: {swap}\n")
    proc = subprocess.run(
        [*_ssh_prefix(env), host, "bash", "-c", swap],
        capture_output=True, text=True, timeout=30,
    )
    log_write((proc.stdout or "") + (proc.stderr or ""))
    if proc.returncode != 0:
        return UploadResult(ok=False, detail=f"symlink swap failed: {proc.returncode}")

    url = deploy.get("domain") or ""
    if url and not url.startswith("http"):
        url = f"https://{url}"
    return UploadResult(ok=True, upload_ref=rid, url=url or None)


def status(deploy: dict, upload_ref: str, env: dict) -> Status:
    """Confirm the release dir exists and the `current` symlink points at it."""
    target = deploy["target"]
    host = target["host"]
    path = target["path"].rstrip("/")
    current = target.get("current_symlink") or "current"
    probe = f"readlink '{path}/{current}' 2>/dev/null || true"
    try:
        proc = subprocess.run(
            [*_ssh_prefix(env), host, "bash", "-c", probe],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return Status(kind="success", detail="upload ok; status probe failed", url=deploy.get("domain"))
    live = (proc.stdout or "").strip()
    if upload_ref and upload_ref in live:
        return Status(kind="success", detail=f"live: {live}", url=deploy.get("domain"))
    if not live:
        return Status(kind="failure", detail=f"{current} symlink not readable on remote")
    return Status(kind="success", detail=f"live: {live} (upload_ref {upload_ref!r} matches partially or not at all)",
                  url=deploy.get("domain"))


def rollback(deploy: dict, to_run_id: str, env: dict,
             log_write: Callable[[str], None]) -> RollbackResult:
    """Swap the `current` symlink back to a prior release id.
    upload_ref for this provider is the release id ("r-YYYYMMDD-HHMMSS")."""
    upload_ref = (env.get("_UPLOAD_REF") or "").strip()
    if not upload_ref:
        return RollbackResult(ok=False, detail="rollback needs the prior deployment's upload_ref (release id)")
    target = deploy["target"]
    host = target["host"]
    path = target["path"].rstrip("/")
    releases = target.get("releases_subdir") or "releases"
    current = target.get("current_symlink") or "current"
    remote_release = f"{path}/{releases}/{upload_ref}"
    # First verify the release still exists.
    verify = f"test -d '{remote_release}' && echo ok"
    proc = subprocess.run(
        [*_ssh_prefix(env), host, "bash", "-c", verify],
        capture_output=True, text=True, timeout=15,
    )
    if "ok" not in (proc.stdout or ""):
        return RollbackResult(ok=False, detail=f"release {upload_ref} not present on remote at {remote_release}")
    swap = (
        f"ln -sfn '{remote_release}' '{path}/{current}.new' && "
        f"mv -Tf '{path}/{current}.new' '{path}/{current}'"
    )
    log_write(f"# rollback symlink swap: {swap}\n")
    proc = subprocess.run(
        [*_ssh_prefix(env), host, "bash", "-c", swap],
        capture_output=True, text=True, timeout=30,
    )
    log_write((proc.stdout or "") + (proc.stderr or ""))
    if proc.returncode != 0:
        return RollbackResult(ok=False, detail=f"symlink swap failed: {proc.returncode}")
    return RollbackResult(ok=True, url=deploy.get("domain"))
