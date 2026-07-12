"""Skills API — per-scope SKILL.md instruction bundles (Phase 5).

Portable skill folders (Agent Skills open standard): list / get / author /
delete per scope, plus the resolved ambient set a chat in a scope actually sees
(own scope + workshop baseline). Builders discover skills natively from their
cwd `.claude/skills/`; assistants load a skill's body on demand via use_skill.

Operator-gated (mounted with the _OPERATOR dependency in main.py). See
services/skills.py.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import knowledge, skills

router = APIRouter()


class PutSkillIn(BaseModel):
    description: str = ""
    body: str
    label: str = ""


def _scope(scope_key: str) -> str:
    try:
        knowledge._parse_scope(scope_key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return scope_key


@router.get("/{scope_key}/skills")
async def list_skills(scope_key: str):
    return skills.list_skills(_scope(scope_key))


@router.get("/{scope_key}/available")
async def available(scope_key: str):
    """The ambient skill set a chat in this scope sees: own-scope skills plus the
    workshop baseline (own-scope wins on a name collision)."""
    return skills.available_for_scope(_scope(scope_key))


@router.get("/{scope_key}/skills/{name}")
async def get_skill(scope_key: str, name: str):
    _scope(scope_key)
    sk = skills.get_skill(scope_key, name)
    if sk is None:
        raise HTTPException(404, f"skill not found: {name}")
    return sk


@router.put("/{scope_key}/skills/{name}")
async def put_skill(scope_key: str, name: str, body: PutSkillIn):
    _scope(scope_key)
    try:
        return skills.put_skill(scope_key, name, body.description, body.body, label=body.label)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{scope_key}/skills/{name}")
async def delete_skill(scope_key: str, name: str):
    _scope(scope_key)
    if not skills.delete_skill(scope_key, name):
        raise HTTPException(404, f"skill not found: {name}")
    return {"deleted": True, "scope": scope_key, "name": name}
