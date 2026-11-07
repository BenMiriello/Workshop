"""An app's GitHub repository on the app page: its state, the managed switch,
and Push now. The reasoning is in services/project_repo.py."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from services import project_repo

router = APIRouter()


class PushOut(BaseModel):
    state: str
    at: str
    sha: str = ""
    detail: str = ""


class RepoOut(BaseModel):
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



@router.get("/{slug}/repo", response_model=RepoOut)
async def get_repo(slug: str) -> dict:
    return await run_in_threadpool(project_repo.describe, slug)


@router.put("/{slug}/repo/managed", response_model=RepoOut)
async def put_managed(slug: str, body: ManagedIn) -> dict:
    try:
        return await run_in_threadpool(project_repo.set_managed, slug, body.managed)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{slug}/repo/push", response_model=RepoOut)
async def post_push(slug: str) -> dict:
    def _push() -> dict:
        project_repo.push(slug)
        return project_repo.describe(slug, fetch=False)

    return await run_in_threadpool(_push)
