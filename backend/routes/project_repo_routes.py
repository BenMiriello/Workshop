"""An app's GitHub repository on the app page: its state, the managed switch,
Push now, and (for installed apps) create or link. Project apps create and link
through `/projects/{slug}/connect-github`. The reasoning is in
services/project_repo.py.

The same verbs are mounted twice: under `/projects` for an app with a working
tree in the projects registry, and under `/installed-apps` for an app that is
its own tree under the apps root.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from services import project_repo


class PushOut(BaseModel):
    state: str
    at: str
    sha: str = ""
    detail: str = ""


class RepoOut(BaseModel):
    available: bool = True
    connected: bool
    error: str = ""
    owner: str = ""
    name: str = ""
    web_url: str = ""
    default_branch: str = ""
    managed: bool = False
    created_by: str = ""
    ahead: int = 0
    behind: int = 0
    push: PushOut | None = None


class ManagedIn(BaseModel):
    managed: bool


class RepoConnectIn(BaseModel):
    mode: Literal["create", "bind"]
    remote_url: str | None = None


def _target(kind: str, slug: str) -> project_repo.Target:
    try:
        return project_repo.target(kind, slug)
    except LookupError as e:
        raise HTTPException(404, str(e))


def _verbs(kind: str) -> APIRouter:
    router = APIRouter()

    @router.get("/{slug}/repo", response_model=RepoOut)
    async def get_repo(slug: str) -> dict:
        return await run_in_threadpool(project_repo.describe, _target(kind, slug))

    @router.put("/{slug}/repo/managed", response_model=RepoOut)
    async def put_managed(slug: str, body: ManagedIn) -> dict:
        try:
            return await run_in_threadpool(project_repo.set_managed, _target(kind, slug), body.managed)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/{slug}/repo/push", response_model=RepoOut)
    async def post_push(slug: str) -> dict:
        t = _target(kind, slug)

        def _push() -> dict:
            project_repo.push(t)
            return project_repo.describe(t, fetch=False)

        return await run_in_threadpool(_push)

    return router


project_router = _verbs("project")
app_router = _verbs("app")


@app_router.post("/{slug}/repo/connect", response_model=RepoOut)
async def post_connect(slug: str, body: RepoConnectIn) -> dict:
    t = _target("app", slug)

    def _connect() -> dict:
        if body.mode == "create":
            err = project_repo.provision(t, scratch=True)
            if err:
                raise HTTPException(400, err)
        else:
            if not (body.remote_url or "").strip():
                raise HTTPException(400, "A remote URL is required to link an existing repository.")
            try:
                project_repo.link(t, body.remote_url or "")
            except Exception as e:
                from routes import projects as projects_routes

                raise projects_routes._github_http_error(e)
        return project_repo.describe(t, fetch=False)

    return await run_in_threadpool(_connect)
