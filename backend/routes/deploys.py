"""Deploys API.

  GET    /deploys                                        cross-project list
  GET    /deploys/providers                              provider catalog
  GET    /projects/{slug}/deploys                        one project's targets
  POST   /projects/{slug}/deploys                        create a target
  PATCH  /projects/{slug}/deploys/{deploy_id}            edit source/autonomy/name
  DELETE /projects/{slug}/deploys/{deploy_id}            remove a target
  POST   /projects/{slug}/deploys/{deploy_id}:deploy     kick a deploy run (returns job_id)
  POST   /projects/{slug}/deploys/{deploy_id}:rollback   promote a prior deploy (returns job_id)
  GET    /projects/{slug}/deploys/{deploy_id}/runs       runs bound to this deploy
  GET    /deploys/{deploy_id}/log/{run_id}               per-run build/upload log

The gate is applied in main.py (operator only). Nothing here mutates the
autonomy overlay; that lives in the workspace autonomy override (Phase 3).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from routes.api_models import Job
from services import deploys, deploy_providers, deploy_runner, deploys_override, deploy_watcher, jobs


router = APIRouter(tags=["deploys"])


# ---- schemas ---------------------------------------------------------------


class DeployIn(BaseModel):
    name: str = Field(..., description='"prod" | "preview" | free')
    target: dict = Field(..., description="{kind, ...provider fields}")
    source: dict = Field(..., description="{kind, branch, build:{cmd,output_dir,...}}")
    domain: str | None = None
    autonomy: str = "reactive"


class DeployPatch(BaseModel):
    name: str | None = None
    target: dict | None = None
    source: dict | None = None
    domain: str | None = None
    autonomy: str | None = None


class KickIn(BaseModel):
    ref: str | None = None
    message: str | None = None


class RollbackIn(BaseModel):
    to_run_id: str


class OverrideEngageIn(BaseModel):
    reason: str | None = None
    by: str | None = "operator"


# ---- response models (ADR 0023 clause 6) -----------------------------------
# Read off services/deploys.py (the Deploy dataclass and its `to_json`),
# deploy_providers.describe_all, deploys_override, deploy_watcher and jobs.
# FastAPI FILTERS a return value through the declared model and drops any key
# it does not name, so an under-declared field here is a live API field that
# vanishes.
#
# `target` and `source` STAY OPEN, deliberately. Their contents are the
# PROVIDER's schema -- `target` is `{kind, ...provider fields}` and each
# provider declares its own `TARGET_FIELDS` -- so enumerating them here would
# describe whichever providers happen to be registered today and silently drop
# the next one's fields. That is also why `deploy_providers` serves the field
# catalog as data rather than the schema hardcoding it.
#
# Clause 4 (RFC3339, `*_at`) already holds on this router: every instant is an
# ISO-8601 UTC string from `_now()`.


class Deploy(BaseModel):
    """One deploy target. `Deploy.to_json` STRIPS None values, so a record
    carries only the fields that have been set -- which is why everything below
    the required four is optional and why absence here means unset rather than
    null.

    `current` is the live deployment: {run_id, commit_sha, deployed_at, url,
    upload_ref}. It is provider-shaped like `target`, so it stays open."""

    id: str
    project_slug: str
    name: str
    target: dict[str, Any]
    source: dict[str, Any]
    domain: str | None = None
    autonomy: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    last_run_id: str | None = None
    current: dict[str, Any] | None = None


class DeployList(BaseModel):
    """Most-recently-updated first."""

    deploys: list[Deploy]


class ProviderEnvRequirement(BaseModel):
    """A credential the provider needs. Each provider module declares its own,
    so the set is open beyond the key."""

    model_config = ConfigDict(extra="allow")

    key: str | None = None


class ProviderInfo(BaseModel):
    """One entry of the 'Add target' catalog. `target_fields` is what that
    provider's `target` config expects, served as DATA so the modal renders a
    new provider with no frontend edit."""

    kind: str
    label: str
    env_requirements: list[ProviderEnvRequirement]
    target_fields: list[dict[str, Any]]
    source_kinds: list[str]


class ProviderCatalog(BaseModel):
    providers: list[ProviderInfo]


class DeploysOverride(BaseModel):
    """The workspace-level kill switch FOR SHIPPING, and it is independent of
    the workshop autonomy override: freezing agent dispatch and freezing prod
    deploys are separate decisions. An absent file reads as clear; a corrupt or
    unreadable one reads as engaged (fail-closed) with `engaged_by` "system"."""

    engaged: bool
    reason: str | None = None
    engaged_at: str | None = None
    engaged_by: str | None = None


class DeployDeleted(BaseModel):
    ok: bool


class RunKicked(BaseModel):
    """A deploy and a rollback both answer with the job that will do it, not
    with the outcome -- the work is asynchronous and the run is how it is
    followed."""

    job_id: str


class DeployRuns(BaseModel):
    """Both `deploy` and `deploy-rollback` runs bound to this target, merged and
    newest-first, then capped at `limit`."""

    runs: list[Job]


class WatcherResult(BaseModel):
    """One deploy's tick decision. `status` is one of no-deploy, not-autonomous,
    no-project-dir, no-remote, no-change, skipped-running, paused, kicked, error
    or crashed, and which other fields are present follows from it -- `sha` only
    once a remote was read, `job_id` only on kicked, `reason` only where there
    is one to give.

    The three identity fields are stamped by `tick_all` and are absent from a
    single-deploy tick, which returns the bare decision."""

    status: str
    sha: str | None = None
    job_id: str | None = None
    reason: str | None = None
    deploy_id: str | None = None
    project_slug: str | None = None
    name: str | None = None


class WatcherSweep(BaseModel):
    """One pass over every Deploy. A crash in one is captured as its own
    `crashed` result rather than raised: a broken remote for one Deploy must not
    stop the others."""

    results: list[WatcherResult]


class DeployNotice(BaseModel):
    """'Change detected but paused' -- written when a tick found new work and an
    override was holding it, so the operator sees it even though no Job fired.
    Cleared automatically on the next kick. `none: true` is the no-notice
    answer, which is why every other field is optional."""

    none: bool | None = None
    deploy_id: str | None = None
    branch: str | None = None
    remote_sha: str | None = None
    detected_at: str | None = None
    reason: str | None = None


class DeployLog(BaseModel):
    """The tail of one run's build/upload log."""

    deploy_id: str
    run_id: str
    log: str


# ---- cross-project ---------------------------------------------------------


@router.get("/deploys", response_model=DeployList, response_model_exclude_unset=True)
def list_all():
    return {"deploys": deploys.list_all()}


@router.get("/deploys/providers", response_model=ProviderCatalog, response_model_exclude_unset=True)
def providers():
    return {"providers": deploy_providers.describe_all()}


# Deploys autonomy override — the workspace-level kill switch for shipping.
# Independent of the workshop autonomy override (services/autonomy_override.py):
# freezing agent dispatch and freezing prod deploys are separate decisions.
@router.get("/deploys/autonomy/override", response_model=DeploysOverride, response_model_exclude_unset=True)
def override_read():
    return deploys_override.read()


@router.post("/deploys/autonomy/override", response_model=DeploysOverride, response_model_exclude_unset=True)
def override_engage(body: OverrideEngageIn):
    return deploys_override.engage(reason=body.reason, by=body.by)


@router.delete("/deploys/autonomy/override", response_model=DeploysOverride, response_model_exclude_unset=True)
def override_clear():
    return deploys_override.clear()


# ---- per-project -----------------------------------------------------------


@router.get("/projects/{slug}/deploys", response_model=DeployList, response_model_exclude_unset=True)
def list_project(slug: str):
    return {"deploys": deploys.list_by_project(slug)}


@router.post("/projects/{slug}/deploys", response_model=Deploy, response_model_exclude_unset=True)
def create(slug: str, body: DeployIn):
    if body.target.get("kind") not in deploy_providers.kinds():
        raise HTTPException(400, f"unknown provider: {body.target.get('kind')}")
    try:
        rec = deploys.create(
            project_slug=slug,
            name=body.name,
            target=body.target,
            source=body.source,
            domain=body.domain,
            autonomy=body.autonomy,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return rec


@router.patch("/projects/{slug}/deploys/{deploy_id}", response_model=Deploy, response_model_exclude_unset=True)
def patch(slug: str, deploy_id: str, body: DeployPatch):
    rec = deploys.get(deploy_id)
    if not rec or rec.get("project_slug") != slug:
        raise HTTPException(404, "deploy not found")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "target" in fields and fields["target"].get("kind") not in deploy_providers.kinds():
        raise HTTPException(400, f"unknown provider: {fields['target'].get('kind')}")
    try:
        return deploys.update(deploy_id, **fields)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.delete("/projects/{slug}/deploys/{deploy_id}", response_model=DeployDeleted, response_model_exclude_unset=True)
def remove(slug: str, deploy_id: str):
    rec = deploys.get(deploy_id)
    if not rec or rec.get("project_slug") != slug:
        raise HTTPException(404, "deploy not found")
    deploys.delete(deploy_id)
    return {"ok": True}


@router.post("/projects/{slug}/deploys/{deploy_id}:deploy", response_model=RunKicked, response_model_exclude_unset=True)
def kick(slug: str, deploy_id: str, body: KickIn):
    rec = deploys.get(deploy_id)
    if not rec or rec.get("project_slug") != slug:
        raise HTTPException(404, "deploy not found")
    try:
        job_id = deploy_runner.kick(deploy_id, ref=body.ref, message=body.message, trigger="manual")
    except deploy_runner.OverrideEngaged as e:
        raise HTTPException(409, str(e))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"job_id": job_id}


@router.post("/projects/{slug}/deploys/{deploy_id}:rollback", response_model=RunKicked, response_model_exclude_unset=True)
def rollback(slug: str, deploy_id: str, body: RollbackIn):
    rec = deploys.get(deploy_id)
    if not rec or rec.get("project_slug") != slug:
        raise HTTPException(404, "deploy not found")
    prior = jobs.get(body.to_run_id)
    if not prior or prior.get("status") != "done":
        raise HTTPException(400, "to_run_id is not a successful deploy")
    b = prior.get("binding") or {}
    if b.get("deploy_id") != deploy_id:
        raise HTTPException(400, "to_run_id belongs to a different deploy")
    try:
        job_id = deploy_runner.kick_rollback(deploy_id, body.to_run_id, trigger="manual")
    except deploy_runner.OverrideEngaged as e:
        raise HTTPException(409, str(e))
    except KeyError:
        raise HTTPException(404, "deploy not found")
    return {"job_id": job_id}


@router.get("/projects/{slug}/deploys/{deploy_id}/runs", response_model=DeployRuns, response_model_exclude_unset=True)
def list_runs(slug: str, deploy_id: str, limit: int = 20):
    rec = deploys.get(deploy_id)
    if not rec or rec.get("project_slug") != slug:
        raise HTTPException(404, "deploy not found")
    runs = jobs.read(kind="deploy", binding_key="deploy_id", binding_value=deploy_id,
                     limit=limit)
    runs.extend(jobs.read(kind="deploy-rollback", binding_key="deploy_id", binding_value=deploy_id,
                          limit=limit))
    runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return {"runs": runs[:limit]}


@router.post("/deploys/watcher/tick", response_model=WatcherSweep, response_model_exclude_unset=True)
def watcher_tick():
    """One-shot manual sweep across all autonomous Deploys. Returns per-deploy
    decisions so the operator can see what happened. Called by the frontend
    Refresh button and by cron for a periodic reality check."""
    return {"results": deploy_watcher.tick_all()}


@router.post("/projects/{slug}/deploys/{deploy_id}:tick", response_model=WatcherResult, response_model_exclude_unset=True)
def watcher_tick_one(slug: str, deploy_id: str):
    """Force a single-Deploy watcher tick. Handy for testing without waiting
    for the loop interval."""
    rec = deploys.get(deploy_id)
    if not rec or rec.get("project_slug") != slug:
        raise HTTPException(404, "deploy not found")
    return deploy_watcher.tick_one(deploy_id)


@router.get("/projects/{slug}/deploys/{deploy_id}/notice", response_model=DeployNotice, response_model_exclude_unset=True)
def read_notice(slug: str, deploy_id: str):
    """The 'change detected but paused' notice, if any. Cleared automatically
    on the next kick."""
    rec = deploys.get(deploy_id)
    if not rec or rec.get("project_slug") != slug:
        raise HTTPException(404, "deploy not found")
    return deploy_watcher.read_notice(deploy_id) or {"none": True}


@router.get("/deploys/{deploy_id}/log/{run_id}", response_model=DeployLog, response_model_exclude_unset=True)
def read_log(deploy_id: str, run_id: str, tail: int = 200):
    from services.deploy_runner import RUNS_ROOT
    p: Path = RUNS_ROOT / deploy_id / "runs" / f"{run_id}.log"
    if not p.exists():
        raise HTTPException(404, "log not found")
    text = p.read_text(errors="replace")
    if tail and tail > 0:
        lines = text.splitlines()
        text = "\n".join(lines[-tail:])
    return {"deploy_id": deploy_id, "run_id": run_id, "log": text}
