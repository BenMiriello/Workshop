"""HTTP surface for project branch worktrees (per ADR-009 section 7).

Each project can host N parallel branches as git worktrees. Build agents pin
to a branch by slug; the operator inspects, merges, or discards each from the
Project Detail "Branches" card.

URL path stays /api/worktrees/ since that's the literal git concept; the
user-facing slug is `branch_slug`.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import worktrees

router = APIRouter()


class _CreateIn(BaseModel):
    branch_slug: str = Field(min_length=1, max_length=48)


class _MergeIn(BaseModel):
    message: str = ""


@router.get("/{project_slug}")
async def list_for_project(project_slug: str):
    try:
        return worktrees.list_branches(project_slug)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{project_slug}")
async def create(project_slug: str, body: _CreateIn):
    try:
        return worktrees.create_branch(project_slug, body.branch_slug)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{project_slug}/{branch_slug}/diff")
async def diff(project_slug: str, branch_slug: str):
    try:
        return worktrees.diff_branch(project_slug, branch_slug)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{project_slug}/{branch_slug}/merge")
async def merge(project_slug: str, branch_slug: str, body: _MergeIn):
    try:
        result = worktrees.merge_branch(project_slug, branch_slug, message=body.message)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason") or "merge failed")
    return result


@router.delete("/{project_slug}/{branch_slug}")
async def discard(project_slug: str, branch_slug: str):
    try:
        return worktrees.discard_branch(project_slug, branch_slug)
    except ValueError as e:
        raise HTTPException(400, str(e))
