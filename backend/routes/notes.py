"""Notes API — a native Workshop notes app over a plain-Markdown vault.

Mounted from the built-in catalog (services/app_registry) under the authenticated
user. Declares the capabilities it PROVIDES (see PROVIDES) and exposes them as
real routes over `services/notes_vault` (the only module that touches the
filesystem). Every PROVIDES entry maps to routes here; keep them in sync.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services import notes_vault as vault

# Capabilities this app exposes to the platform, in the "<app>.<verb>" shape used
# across the Workshop (e.g. finance.read). Each maps to routes below.
PROVIDES: list[str] = [
    "notes-app.read",    # list + read notes
    "notes-app.search",  # full-text search
    "notes-app.links",   # backlinks + resolved outgoing links
    "notes-app.write",   # create + update + rename + delete notes
]

router = APIRouter(prefix="/notes-app", tags=["notes-app"])


class NoteCreate(BaseModel):
    path: str
    content: str = ""


class NoteUpdate(BaseModel):
    content: str


class NoteRename(BaseModel):
    to: str


@router.get("/capabilities")
def capabilities() -> dict:
    """List the capabilities this provider exposes."""
    return {"provides": PROVIDES}


@router.get("/notes")
def list_notes() -> dict:
    """List every note in the vault with cheap metadata."""
    notes = vault.list_notes()
    return {"notes": notes, "count": len(notes)}


@router.get("/notes/{note_path:path}")
def get_note(note_path: str) -> dict:
    """Read one note plus its resolved outgoing wikilinks. 404 if missing."""
    try:
        return vault.read_note(note_path)
    except vault.VaultPathError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, f"note not found: {note_path}")


@router.get("/search")
def search_notes(q: str = Query(..., min_length=1), limit: int = Query(50, ge=1, le=500)) -> dict:
    """Case-insensitive full-text search across the vault."""
    results = vault.search(q, limit=limit)
    return {"query": q, "results": results, "count": len(results)}


@router.get("/backlinks/{note_path:path}")
def get_backlinks(note_path: str) -> dict:
    """Notes whose wikilinks resolve to this note. 404 if the target is missing."""
    try:
        links = vault.backlinks(note_path)
    except vault.VaultPathError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, f"note not found: {note_path}")
    return {"path": note_path, "backlinks": links, "count": len(links)}


@router.post("/notes", status_code=201)
def create_note(body: NoteCreate) -> dict:
    """Create a new note. 409 if it already exists, 400 on an unsafe path."""
    try:
        return vault.create_note(body.path, body.content)
    except vault.VaultPathError as exc:
        raise HTTPException(400, str(exc))
    except FileExistsError:
        raise HTTPException(409, f"note already exists: {body.path}")


@router.put("/notes/{note_path:path}")
def update_note(note_path: str, body: NoteUpdate) -> dict:
    """Overwrite an existing note. 404 if missing, 400 on an unsafe path."""
    try:
        return vault.update_note(note_path, body.content)
    except vault.VaultPathError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, f"note not found: {note_path}")


@router.post("/notes/{note_path:path}/rename")
def rename_note(note_path: str, body: NoteRename) -> dict:
    """Move a note and rewrite referring wikilinks. 404 source, 409 dest, 400 unsafe."""
    try:
        return vault.rename_note(note_path, body.to)
    except vault.VaultPathError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, f"note not found: {note_path}")
    except FileExistsError:
        raise HTTPException(409, f"note already exists: {body.to}")


@router.delete("/notes/{note_path:path}")
def delete_note(note_path: str) -> dict:
    """Delete a note. 404 if missing, 400 on an unsafe path."""
    try:
        return vault.delete_note(note_path)
    except vault.VaultPathError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, f"note not found: {note_path}")
