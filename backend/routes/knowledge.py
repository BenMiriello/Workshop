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
    # Optional metadata (design doc sec 5 / sec 11): `author` is an origin
    # string (services/origin), sent by the operator's own editor as
    # "operator" on create and omitted on edit so an edit does not reassign
    # who wrote the note. `note_type` and `pinned` drive the ranked ambient
    # digest (knowledge.index_block); omitting either preserves what is
    # already on disk (knowledge._with_meta).
    author: str = ""
    note_type: str = ""
    pinned: bool | None = None


class SearchIn(BaseModel):
    query: str
    limit: int = 5


class RecallIn(BaseModel):
    from_scope: str
    target_scope: str
    query: str
    limit: int = 5


# ---- response models (ADR 0023 clause 6) -----------------------------------
# Read off services/knowledge.py. FastAPI FILTERS a return value through the
# declared model and drops any key it does not name, so an under-declared field
# here is a live API field that vanishes.
#
# `updated` is an ISO instant but is NOT named `updated_at`, which is an ADR
# 0023 clause 4 violation. It is the name the store, the API and the frontend
# all use today; renaming it is a wire-shape change that lands with its
# frontend edit, so it is recorded here rather than half-changed.


class NoteSummary(BaseModel):
    """One note in a scope listing. `title` falls back to the id for a note the
    index has never seen, and `hook` is the one-line summary the ambient digest
    ranks on -- empty for such a note rather than absent."""

    id: str
    title: str
    hook: str
    updated: str | None = None


class NoteDetail(BaseModel):
    """One note's body plus its provenance and facets, in one read, so the
    editor can round-trip them without a second endpoint.

    `author` is an origin string (see services/origin) and is "" on older
    notes. `chat` is the originating thread id, or "" when none was recorded.
    `type` is the note kind the ranked digest reads."""

    scope: str
    note_id: str
    content: str
    author: str
    type: str
    pinned: bool
    chat: str


class NoteRef(BaseModel):
    """What a write answers with: the address of the note, not the note. The
    caller already holds the content it just sent."""

    scope: str
    note_id: str


class NoteDeleted(BaseModel):
    """`existed` is False when the note was already gone. Delete is idempotent
    and this is how the caller tells the two apart without an error."""

    scope: str
    note_id: str
    existed: bool


class SearchHit(BaseModel):
    """One matching note. `score` is null on the plaintext path and `debug`
    reads "fallback" there, both kept for shape-compat with older callers that
    predate the semantic layer. `author` is carried so the recall site can
    label a model-distilled note differently from an operator-written one."""

    note_id: str
    text: str
    author: str
    score: float | None = None
    debug: str | None = None


def _scope(scope_key: str) -> str:
    try:
        knowledge._parse_scope(scope_key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return scope_key


@router.get("/{scope_key}/notes", response_model=list[NoteSummary], response_model_exclude_unset=True)
async def list_notes(scope_key: str):
    return knowledge.list_notes(_scope(scope_key))


@router.get("/{scope_key}/notes/{note_id}", response_model=NoteDetail, response_model_exclude_unset=True)
async def get_note(scope_key: str, note_id: str):
    _scope(scope_key)
    try:
        body = knowledge.get_note(scope_key, note_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if body is None:
        raise HTTPException(404, f"note not found: {note_id}")
    meta = knowledge.note_meta(scope_key, note_id)
    return {"scope": scope_key, "note_id": note_id, "content": body, **meta}


@router.put("/{scope_key}/notes/{note_id}", response_model=NoteRef, response_model_exclude_unset=True)
async def put_note(scope_key: str, note_id: str, body: PutNoteIn):
    _scope(scope_key)
    try:
        return await knowledge.put_note(
            scope_key, note_id, body.title, body.content, body.hook,
            author=body.author, note_type=body.note_type, pinned=body.pinned,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{scope_key}/notes/{note_id}", response_model=NoteDeleted, response_model_exclude_unset=True)
async def delete_note(scope_key: str, note_id: str):
    _scope(scope_key)
    try:
        return await knowledge.delete_note(scope_key, note_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{scope_key}/search", response_model=list[SearchHit], response_model_exclude_unset=True)
async def search(scope_key: str, body: SearchIn):
    _scope(scope_key)
    return await knowledge.search(scope_key, body.query, body.limit)


@router.post("/recall", response_model=list[SearchHit], response_model_exclude_unset=True)
async def recall(body: RecallIn):
    _scope(body.from_scope)
    _scope(body.target_scope)
    return await knowledge.recall(body.from_scope, body.target_scope, body.query, body.limit)
