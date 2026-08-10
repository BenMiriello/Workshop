"""Cloudflare Pages deploy provider.

Direct API for preflight/status/rollback/list_history; Wrangler CLI for
the upload step (the direct-upload manifest+JWT+chunked-file dance is ~300
lines of edge cases we don't need to reimplement — Cloudflare ships wrangler
for exactly this). Wrangler must be reachable on the runner's PATH; the
preflight check verifies both the API token and the CLI.

Target config:
    {
      "kind": "cloudflare-pages",
      "project_name": "my-site",      # CF Pages project (must exist in CF)
      "account_id": "..."                 # optional override, else from env
    }

Source config (git-branch):
    {
      "kind": "git-branch",
      "branch": "main",
      "build": { "cmd": "npm run build", "output_dir": "dist", "node_version": "20" }
    }
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .base import BuildResult, Preflight, RollbackResult, Status, UploadResult


KIND = "cloudflare-pages"
LABEL = "Cloudflare Pages"
SOURCE_KINDS = ["git-branch"]
TARGET_FIELDS = [
    {"key": "project_name", "required": True, "description": "CF Pages project name"},
    {"key": "account_id", "required": False, "description": "Override CF_ACCOUNT_ID"},
]


API_BASE = "https://api.cloudflare.com/client/v4"


def env_requirements() -> list[dict]:
    return [
        {"key": "CF_API_TOKEN", "required": True,
         "description": "Cloudflare API token, Pages:Edit + Account:Read"},
        {"key": "CF_ACCOUNT_ID", "required": True,
         "description": "Cloudflare account id (falls back if target.account_id set)"},
    ]


# ---------------------------------------------------------------------------
# small HTTP helper (stdlib only) — routes all calls through this so the
# egress guard / logging has one chokepoint
# ---------------------------------------------------------------------------

def _api(env: dict, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    token = env.get("CF_API_TOKEN") or ""
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        payload = e.read()
        status = e.code
    except (urllib.error.URLError, OSError) as e:
        return 0, {"success": False, "errors": [{"message": str(e)}]}
    try:
        return status, json.loads(payload.decode() or "{}")
    except json.JSONDecodeError:
        return status, {"success": False, "errors": [{"message": "non-json response"}]}


def _account_id(deploy: dict, env: dict) -> str:
    return (deploy.get("target") or {}).get("account_id") or env.get("CF_ACCOUNT_ID") or ""


def _project_name(deploy: dict) -> str:
    return (deploy.get("target") or {}).get("project_name") or ""


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------

def preflight(deploy: dict, env: dict) -> Preflight:
    missing = [k["key"] for k in env_requirements() if k["required"] and not env.get(k["key"])]
    if missing:
        return Preflight(ok=False, missing_keys=missing, detail="required env not set")
    if not _project_name(deploy):
        return Preflight(ok=False, detail="target.project_name is required")
    if not shutil.which("wrangler"):
        return Preflight(ok=False, detail=(
            "wrangler CLI not on PATH (install: `npm i -g wrangler` or use npx). "
            "Upload requires wrangler; preflight/status/rollback are API-only."
        ))
    acc = _account_id(deploy, env)
    proj = _project_name(deploy)
    code, resp = _api(env, "GET", f"/accounts/{acc}/pages/projects/{proj}")
    if code == 200 and resp.get("success"):
        return Preflight(ok=True)
    err = ""
    if isinstance(resp.get("errors"), list) and resp["errors"]:
        err = str(resp["errors"][0].get("message") or resp["errors"][0])
    return Preflight(ok=False, detail=f"CF API {code}: {err or 'project lookup failed'}")


# ---------------------------------------------------------------------------
# build (generic; not CF-specific — same shape lives on rsync-ssh later)
# ---------------------------------------------------------------------------

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
        return BuildResult(ok=False, output_dir=output_dir,
                           detail=f"build exited {proc.returncode}")
    out = workdir / output_dir
    if not out.is_dir():
        return BuildResult(ok=False, output_dir=output_dir,
                           detail=f"build ok but output_dir not found: {out}")
    return BuildResult(ok=True, output_dir=str(out))


# ---------------------------------------------------------------------------
# upload — wrangler pages deploy
# ---------------------------------------------------------------------------

def upload(deploy: dict, build_result: BuildResult, env: dict,
           log_write: Callable[[str], None]) -> UploadResult:
    if not build_result.ok or not build_result.output_dir:
        return UploadResult(ok=False, detail="no build artifact")
    proj = _project_name(deploy)
    acc = _account_id(deploy, env)
    branch = (deploy.get("source") or {}).get("branch") or "main"
    # Wrangler picks the deployment name from the branch it thinks it's on. The
    # source branch controls which environment the deploy lands in on CF:
    # `production_branch` on the CF project decides prod vs preview.
    child_env = dict(os.environ)
    child_env["CLOUDFLARE_API_TOKEN"] = env["CF_API_TOKEN"]
    child_env["CLOUDFLARE_ACCOUNT_ID"] = acc
    cmd = [
        "wrangler", "pages", "deploy", build_result.output_dir,
        f"--project-name={proj}",
        f"--branch={branch}",
        "--commit-dirty=true",
    ]
    log_write(f"$ {' '.join(cmd)}\n")
    proc = subprocess.run(
        cmd,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    out = proc.stdout or ""
    err = proc.stderr or ""
    log_write(out)
    if err:
        log_write("[stderr]\n" + err)
    if proc.returncode != 0:
        return UploadResult(ok=False, detail=f"wrangler exited {proc.returncode}")
    # Wrangler prints the deployment URL on success. It also embeds the
    # deployment id in the alias URL segment. We parse both from stdout.
    url = None
    for line in (out + "\n" + err).splitlines():
        line = line.strip()
        # Deployment URLs look like: https://<hash>.<proj>.pages.dev
        if ".pages.dev" in line and "https://" in line:
            i = line.find("https://")
            j = line.find(" ", i)
            url = line[i:] if j < 0 else line[i:j]
            break
    return UploadResult(ok=True, upload_ref=url or "", url=url)


# ---------------------------------------------------------------------------
# status — poll a specific deployment by url or upload_ref
# ---------------------------------------------------------------------------

def status(deploy: dict, upload_ref: str, env: dict) -> Status:
    """Because wrangler already completed synchronously with a URL, this
    reports the *final* status of the returned deployment. We fetch the
    Pages project's most recent deployment and match its url; if we
    can't match, we trust wrangler's exit and report success."""
    acc = _account_id(deploy, env)
    proj = _project_name(deploy)
    code, resp = _api(env, "GET", f"/accounts/{acc}/pages/projects/{proj}/deployments?per_page=10")
    if code != 200 or not resp.get("success"):
        return Status(kind="success", detail="wrangler succeeded; live status poll failed (non-fatal)",
                      url=upload_ref or None)
    for d in resp.get("result", []) or []:
        d_url = d.get("url") or ""
        if upload_ref and d_url and (d_url in upload_ref or upload_ref in d_url):
            stage = (d.get("latest_stage") or {}).get("status") or ""
            if stage == "success":
                return Status(kind="success", url=d.get("url"), detail=None)
            if stage in ("failure", "canceled"):
                return Status(kind="failure", url=d.get("url"), detail=stage)
            return Status(kind="running", url=d.get("url"), detail=stage or "in progress")
    return Status(kind="success", url=upload_ref or None,
                  detail="wrangler succeeded; deployment not yet indexed")


# ---------------------------------------------------------------------------
# rollback — promote a prior successful deployment
# ---------------------------------------------------------------------------

def rollback(deploy: dict, to_run_id: str, env: dict,
             log_write: Callable[[str], None]) -> RollbackResult:
    """CF Pages rollback: POST to
    /accounts/{acc}/pages/projects/{proj}/deployments/{id}/rollback

    to_run_id is a Job id; we look at the Job's binding.upload_ref for the CF
    deployment id. The runner passes this in via env['_UPLOAD_REF'] because
    the provider itself does not read the Job store.
    """
    upload_ref = env.get("_UPLOAD_REF") or ""
    if not upload_ref:
        return RollbackResult(ok=False, detail="rollback needs the prior deployment's upload_ref")
    # upload_ref is a URL like https://<id>.<proj>.pages.dev — extract the id.
    dep_id = ""
    if ".pages.dev" in upload_ref:
        try:
            dep_id = upload_ref.split("//", 1)[1].split(".", 1)[0]
        except (IndexError, ValueError):
            dep_id = ""
    if not dep_id:
        return RollbackResult(ok=False, detail=f"could not extract deployment id from {upload_ref!r}")
    acc = _account_id(deploy, env)
    proj = _project_name(deploy)
    log_write(f"POST /accounts/{acc}/pages/projects/{proj}/deployments/{dep_id}/rollback\n")
    code, resp = _api(env, "POST", f"/accounts/{acc}/pages/projects/{proj}/deployments/{dep_id}/rollback")
    if code == 200 and resp.get("success"):
        result = resp.get("result") or {}
        return RollbackResult(ok=True, url=result.get("url") or upload_ref)
    err = ""
    if isinstance(resp.get("errors"), list) and resp["errors"]:
        err = str(resp["errors"][0].get("message") or resp["errors"][0])
    return RollbackResult(ok=False, detail=f"CF API {code}: {err or 'rollback failed'}")
