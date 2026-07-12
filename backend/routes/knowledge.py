"""Knowledge API — per-scope notes with plaintext files as source of truth and a
Letta archive as the semantic-recall layer. This router IS the interface a notes
app (or an Obsidian adapter) plugs into: list / get / put / delete / search per
scope, plus cross-scope recall gated by the grants layer.

Operator-gated (mounted with the _OPERATOR dependency in main.py). See
services/knowledge.py for the store and docs/agent-chat-redesign/knowledge-interface.md.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import knowledge

router = APIRouter()


class PutNoteIn(BaseModel):
    title: str = ""
    content: str
    hook: str = ""


class SearchIn(BaseModel):
    query: str
    limit: int = 5


class RecallIn(BaseModel):
    from_scope: str
    target_scope: str
    query: str
    limit: int = 5


def _scope(scope_key: str) -> str:
    try:
        knowledge._parse_scope(scope_key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return scope_key


@router.get("/{scope_key}/notes")
async def list_notes(scope_key: str):
    return knowledge.list_notes(_scope(scope_key))


@router.get("/{scope_key}/notes/{note_id}")
async def get_note(scope_key: str, note_id: str):
    _scope(scope_key)
    try:
        body = knowledge.get_note(scope_key, note_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if body is None:
        raise HTTPException(404, f"note not found: {note_id}")
    return {"scope": scope_key, "note_id": note_id, "content": body}


@router.put("/{scope_key}/notes/{note_id}")
async def put_note(scope_key: str, note_id: str, body: PutNoteIn):
    _scope(scope_key)
    try:
        return await knowledge.put_note(scope_key, note_id, body.title, body.content, body.hook)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{scope_key}/notes/{note_id}")
async def delete_note(scope_key: str, note_id: str):
    _scope(scope_key)
    try:
        return await knowledge.delete_note(scope_key, note_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{scope_key}/search")
async def search(scope_key: str, body: SearchIn):
    _scope(scope_key)
    return await knowledge.search(scope_key, body.query, body.limit)


@router.post("/recall")
async def recall(body: RecallIn):
    _scope(body.from_scope)
    _scope(body.target_scope)
    return await knowledge.recall(body.from_scope, body.target_scope, body.query, body.limit)
