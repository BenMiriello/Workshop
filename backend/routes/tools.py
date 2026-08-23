"""The tools surface: what capabilities exist, who owns each, and CRUD for the
ones a user owns.

Design of record: `docs/platform-redesign/tools-are-declared-2026-08-23.md`
(stage 2 of its section 5). The enumeration comes from
`services/tool_registry`; nothing here decides what any agent may call.
`services/llm_router.tool_flags` is still the only thing that grants a tool, and
these routes deliberately do not touch it — a tool listed here is a declaration
with an owner, not a permission.

WRITES ARE USER-PROVENANCE ONLY, and the refusal is explicit. A platform tool
"can only be used not edited" (the operator, 2026-08-23), and an app's tools
arrive and leave with the app. An edit or delete aimed at either is refused with
409 and a reason that names the id and its provenance, rather than being ignored
with a 200: a write that reports success and changes nothing teaches an operator
that the record is theirs when it is not.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from routes.api_models import DeletedOut, ToolRecord, ToolSpec, ToolsOut
from services import tool_registry

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolWrite(BaseModel):
    """The editable half of a tool record.

    `provenance` is deliberately absent: it is set by the source that loaded a
    record, so a body cannot declare itself platform-owned. `id` is in the body
    on create and in the path on update, because an id is the one thing an edit
    may not change — renaming a tool is deleting one and declaring another, and
    an allowlist elsewhere may already name the old one.
    """

    title: str = ""
    description: str = ""
    kind: str = "command"
    # Kind-specific launch detail. The same model the response uses: a typed
    # floor with `extra="allow"`, so a caller can send a key this kind grows
    # later without the request model dropping it, and the generated client
    # still describes the shape (a bare `dict` here would generate
    # `Record<string, never>`, per scripts/check_opaque_fields.py).
    spec: ToolSpec = Field(default_factory=ToolSpec)
    scopes: list[str] = Field(default_factory=list)
    ceiling: list[str] = Field(default_factory=list)


class ToolCreate(ToolWrite):
    id: str = Field(min_length=1, max_length=64)


def _body(w: ToolWrite) -> dict:
    return {
        "title": w.title,
        "description": w.description,
        "kind": w.kind,
        "spec": w.spec.model_dump(exclude_none=True),
        "scopes": w.scopes,
        "ceiling": w.ceiling,
    }


@router.get("", response_model=ToolsOut, response_model_exclude_unset=True)
async def list_tools():
    """Every tool this instance can name: platform first, then the installed
    apps', then the user's own. `editable` is true for exactly the user ones."""
    return {"tools": [t.to_dict() for t in tool_registry.catalog()]}


@router.post("", response_model=ToolRecord, response_model_exclude_unset=True)
async def create_tool(body: ToolCreate):
    """Declare a user tool.

    Refused when the id is already a platform or app tool's, naming it: a base
    tool a user record could quietly redefine would be a default, not a base
    tool."""
    try:
        return tool_registry.create_user_tool(body.id, _body(body)).to_dict()
    except tool_registry.ToolConflict as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/{tool_id}", response_model=ToolRecord, response_model_exclude_unset=True)
async def update_tool(tool_id: str, body: ToolWrite):
    try:
        return tool_registry.update_user_tool(tool_id, _body(body)).to_dict()
    except tool_registry.ToolNotEditable as e:
        raise HTTPException(409, str(e))
    except KeyError:
        raise HTTPException(404, f"no user tool {tool_id!r}")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{tool_id}", response_model=DeletedOut, response_model_exclude_unset=True)
async def delete_tool(tool_id: str):
    try:
        deleted = tool_registry.delete_user_tool(tool_id)
    except tool_registry.ToolNotEditable as e:
        raise HTTPException(409, str(e))
    if not deleted:
        raise HTTPException(404, f"no user tool {tool_id!r}")
    return {"deleted": True}
