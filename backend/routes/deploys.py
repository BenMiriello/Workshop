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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import deploys, deploy_providers, deploy_runner, deploys_override, jobs


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


# ---- cross-project ---------------------------------------------------------


@router.get("/deploys")
def list_all():
    return {"deploys": deploys.list_all()}


@router.get("/deploys/providers")
def providers():
    return {"providers": deploy_providers.describe_all()}


# Deploys autonomy override — the workspace-level kill switch for shipping.
# Independent of the workshop autonomy override (services/autonomy_override.py):
# freezing agent dispatch and freezing prod deploys are separate decisions.
@router.get("/deploys/autonomy/override")
def override_read():
    return deploys_override.read()


@router.post("/deploys/autonomy/override")
def override_engage(body: OverrideEngageIn):
    return deploys_override.engage(reason=body.reason, by=body.by)


@router.delete("/deploys/autonomy/override")
def override_clear():
    return deploys_override.clear()


# ---- per-project -----------------------------------------------------------


@router.get("/projects/{slug}/deploys")
def list_project(slug: str):
    return {"deploys": deploys.list_by_project(slug)}


@router.post("/projects/{slug}/deploys")
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


@router.patch("/projects/{slug}/deploys/{deploy_id}")
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


@router.delete("/projects/{slug}/deploys/{deploy_id}")
def remove(slug: str, deploy_id: str):
    rec = deploys.get(deploy_id)
    if not rec or rec.get("project_slug") != slug:
        raise HTTPException(404, "deploy not found")
    deploys.delete(deploy_id)
    return {"ok": True}


@router.post("/projects/{slug}/deploys/{deploy_id}:deploy")
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


@router.post("/projects/{slug}/deploys/{deploy_id}:rollback")
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


@router.get("/projects/{slug}/deploys/{deploy_id}/runs")
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


@router.get("/deploys/{deploy_id}/log/{run_id}")
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
