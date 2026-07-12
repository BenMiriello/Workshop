"""Base Apps — tenant-level git-repo listing.

Returns one record per top-level directory containing a `.git/`, with
branch and last-commit metadata. Previously mounted at /projects/ (named
to match the original top-nav tab); moved to /base-apps/ per ADR-008 so
"/projects/" can host the operator's Projects hub.
"""
import os
import subprocess
from pathlib import Path

import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from services import app_overlay, app_registry, app_runner, gc, local_runner, manifests, permissions, port_registry, registry

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


@router.get("/launcher")
async def get_launcher():
    """Launcher cards for the apps installed on THIS instance, in installed order.

    Drives frontend/src/pages/Apps.tsx: the launcher, its breadcrumbs, and the
    switcher all derive from this data, so an installed app appears and a removed
    one disappears with no frontend edit. A fresh instance (nothing installed)
    returns an empty list — the correct blank-container state."""
    return {"apps": app_registry.launcher_cards()}


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


class AppUiIn(BaseModel):
    agent_button: bool | None = None


@router.put("/manifests/{app_id}/ui")
async def set_app_manifest_ui(app_id: str, body: AppUiIn):
    """Update an app's per-app UI settings (currently `agent_button`, which gates
    the floating assistant dock inside the app) and persist apps.json. Returns the
    refreshed manifest, or 404 if the app is not declared. View-pref style: a
    single boolean that commits on change, like theme/filters."""
    ui = {k: v for k, v in body.model_dump().items() if v is not None}
    entry = manifests.set_app_ui(app_id, ui)
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


# ---- Deploy: LocalRunner standalone surface (doc 05 section 2) ---------------
# Start/stop/status a project's STANDALONE deployment as a scoped local
# subprocess. The app runs in its project dir, on an allocated port, with a
# SCOPED env (allowlist + PORT only — the platform's secret env is never
# passed). See services/local_runner.py. INTEGRATED route-mount (needs frontend
# route injection) and the RemoteRunner (containers) are out of scope for v1.


@router.post("/deploy/{slug}/start")
async def deploy_start(slug: str):
    """Start the project's standalone deploy. Returns
    {running, port, url, pid, command}. 400 if no run command can be detected,
    404 if the project is unknown, 503 if no deploy port is free."""
    try:
        return local_runner.start_deploy(slug)
    except local_runner.DeployError as e:
        raise HTTPException(e.status, e.message)


@router.post("/deploy/{slug}/stop")
async def deploy_stop(slug: str):
    """Stop the project's standalone deploy. Idempotent: {stopped: true} even if
    it was not running."""
    return local_runner.stop_deploy(slug)


@router.get("/deploy/{slug}/status")
async def deploy_status(slug: str):
    """Status of the project's standalone deploy:
    {running, port?, url?, pid?, command?}."""
    return local_runner.deploy_status(slug)


# ---- Run: multi-service app runner (services/app_runner.py) ------------------
# Start/stop/status an app made of SEVERAL cooperating services (e.g. a backend
# API + a frontend + a worker, plus docker deps). Each service is a scoped
# subprocess on a centrally-allocated port; build + spawn run on a background
# thread (status reflects a live state machine). app_monitor restarts a service
# that crashes while the app is desired-running. This supersedes single-process
# /deploy for multi-service apps; /deploy stays for trivial one-process projects.


@router.get("/run/{slug}/manifest")
async def run_manifest(slug: str):
    """The resolved app manifest (declared workshop.app.json, else auto-detected):
    {name, detected, services[], deps[]}."""
    try:
        return app_runner.manifest_dict(slug)
    except app_runner.AppError as e:
        raise HTTPException(e.status, e.message)


@router.post("/run/{slug}/start")
async def run_start(slug: str):
    """Start all enabled services. Returns immediately with a 'starting' snapshot;
    build + spawn proceed on a background thread. Poll status for progress."""
    try:
        return app_runner.start_app(slug)
    except app_runner.AppError as e:
        raise HTTPException(e.status, e.message)


@router.post("/run/{slug}/stop")
async def run_stop(slug: str):
    """Stop all services and set desired state = stopped (monitor won't restart)."""
    return app_runner.stop_app(slug)


@router.get("/run/{slug}/status")
async def run_status(slug: str):
    """Live per-service status + health, app phase, desired state, declared deps."""
    return app_runner.app_status(slug)


@router.get("/run/{slug}/logs")
async def run_logs(slug: str, service: str):
    """Tail one service's combined build+run log."""
    return {"service": service, "log": app_runner.app_logs(slug, service)}


@router.post("/run/{slug}/services/{service}/restart")
async def run_restart_service(slug: str, service: str):
    """Re-spawn one service (no rebuild). Manual counterpart to the monitor."""
    try:
        return app_runner.restart_service(slug, service)
    except app_runner.AppError as e:
        raise HTTPException(e.status, e.message)


@router.get("/run")
async def run_list():
    """Status of every app the runner knows about."""
    return {"apps": app_runner.list_apps()}


# ---- Environment / secrets registry -----------------------------------------
# The manifest declares env REQUIREMENTS (key + metadata, no values); the
# operator fills VALUES here; secrets_store holds them (gitignored, 0600); the
# runner injects them at spawn. Status is presence-only (the `check_secrets`
# view) — values are write-only and never returned through these endpoints.


class EnvValuesIn(BaseModel):
    values: dict[str, str]


@router.get("/run/{slug}/env")
async def run_env(slug: str):
    """Declared env requirements + per-key SET status (never values). Also lists
    any stored keys not declared as requirements."""
    return app_runner.env_overview(slug)


@router.put("/run/{slug}/env")
async def run_env_set(slug: str, body: EnvValuesIn):
    """Set/merge env values for a project. A blank value clears that key. Returns
    the refreshed presence-only overview (no values echoed back)."""
    from services import secrets_store
    secrets_store.set_values(slug, body.values or {})
    return app_runner.env_overview(slug)


@router.delete("/run/{slug}/env/{key}")
async def run_env_delete(slug: str, key: str):
    """Unset one env value. Returns the refreshed overview."""
    from services import secrets_store
    secrets_store.delete_key(slug, key)
    return app_runner.env_overview(slug)


@router.get("/ports")
async def ports():
    """The central port registry: every current local-service port allocation
    (preview / deploy / app), after a reconcile pass. The single source of truth
    that prevents port conflicts across all three runners."""
    return {"windows": port_registry.WINDOWS, "allocations": port_registry.all_allocations()}


# ---- Reverse proxy: view a running app's service over the tailnet ------------
# App services bind 127.0.0.1:<port> (not exposed to the tailnet). This proxy
# forwards to one service so the operator can open it through the EXISTING backend
# origin (https://<host>:8446/base-apps/run/<slug>/proxy/<service>/...), without a
# new tailscale-serve mapping. HTTP + SSE stream through fine. Two honest limits:
# WebSockets (e.g. Vite HMR) do not traverse this HTTP proxy, and an SPA that calls
# an absolute `/api` expects its OWN origin — for those, give the app a dedicated
# origin with `tailscale serve` (see docs/app-platform-runner.md).

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding",
    "content-length",
}


@router.api_route(
    "/run/{slug}/proxy/{service}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def run_proxy(slug: str, service: str, path: str, request: Request):
    st = app_runner.app_status(slug)
    svc = next((s for s in st.get("services", []) if s["name"] == service), None)
    if not svc or not svc.get("port"):
        raise HTTPException(404, f"no running service '{service}' for app '{slug}'")
    if svc.get("status") != "running":
        raise HTTPException(409, f"service '{service}' is {svc.get('status')}, not running")

    # Forward the FULL incoming path verbatim (not just the {path} tail), so an
    # app configured with base = this proxy path serves its assets correctly. The
    # app's public base path is exactly request.url.path's prefix up to {service}.
    target = f"http://127.0.0.1:{svc['port']}{request.url.path}"
    fwd_headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in _HOP_BY_HOP and k.lower() != "host"}
    # Force an unencoded upstream body so we can string-inject the nav overlay
    # into HTML (you cannot rewrite a gzipped body). Dev preview, so the small
    # cost of skipping compression is fine.
    fwd_headers["accept-encoding"] = "identity"
    body = await request.body()
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
    try:
        upstream = await client.send(
            client.build_request(
                request.method, target, headers=fwd_headers,
                params=dict(request.query_params), content=body or None,
            ),
            stream=True,
        )
    except httpx.RequestError as e:
        await client.aclose()
        raise HTTPException(502, f"upstream service unreachable: {e}")

    # Strip framing/CSP guards so the app can be framed in the Workshop preview
    # and the injected overlay's inline script runs (a dev-preview proxy, not a
    # production origin). Drop these regardless of body type.
    _STRIP = _HOP_BY_HOP | {
        "content-security-policy", "content-security-policy-report-only", "x-frame-options",
    }
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _STRIP}

    ctype = upstream.headers.get("content-type", "")
    # HTML documents get the nav overlay injected and are returned whole (small,
    # and string-injection needs the full body). Everything else (assets, SSE)
    # streams through untouched.
    if "text/html" in ctype.lower():
        raw = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        try:
            text = raw.decode("utf-8")
            text = app_overlay.inject(text, slug, service)
            out = text.encode("utf-8")
        except UnicodeDecodeError:
            out = raw  # non-utf8 HTML: pass through unmodified
        return Response(content=out, status_code=upstream.status_code,
                        headers=resp_headers, media_type=ctype)

    async def _body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        _body(), status_code=upstream.status_code, headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )
