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


# ---- response models (ADR 0023 clause 6) -----------------------------------
# Read off services/notes_vault.py, the only module that touches the vault
# filesystem. FastAPI FILTERS a return value through the declared model and
# drops any key it does not name, so an under-declared field here is a live API
# field that vanishes.
#
# `modified` is a float of epoch seconds straight off `stat().st_mtime`, which
# is an ADR 0023 clause 4 violation twice over (raw epoch, and named without the
# `*_at` suffix). It is recorded rather than converted: changing it is a
# wire-shape change to every notes surface, and the ADR's migration posture says
# such a change lands with its frontend edit in the same commit.


class Capabilities(BaseModel):
    """What this provider exposes, in the "<app>.<verb>" shape used across the
    Workshop. Every entry maps to a route in this file."""

    provides: list[str]


class NoteMeta(BaseModel):
    """A note as the LIST sees it: cheap metadata, no content. `title` is the
    note's first-line name, which is also what `update_note` syncs the filename
    to."""

    path: str
    title: str
    size: int
    modified: float


class NoteList(BaseModel):
    notes: list[NoteMeta]
    count: int


class NoteLink(BaseModel):
    """One outgoing wikilink. `raw` is what was written between the brackets,
    `target` is it stripped of any `#heading` / `|alias`, and `resolved` is the
    vault-relative path it points at -- null when nothing matches, which is what
    makes a broken link visible rather than silently dropped."""

    raw: str
    target: str | None = None
    resolved: str | None = None


class Note(BaseModel):
    """One note in full, with its outgoing links already resolved so the reader
    does not need a second call to render them."""

    path: str
    title: str
    content: str
    modified: float
    links: list[NoteLink]


class SearchHit(BaseModel):
    """One MATCHING LINE, not one matching note: a note that matches twice
    appears twice, which is why `limit` bounds lines rather than files."""

    path: str
    line: int
    snippet: str


class SearchResults(BaseModel):
    query: str
    results: list[SearchHit]
    count: int


class Backlink(BaseModel):
    """A note whose wikilink resolves here, and the line it does it on. At most
    one entry per referring note, even when it links several times."""

    path: str
    line: int
    context: str


class Backlinks(BaseModel):
    path: str
    backlinks: list[Backlink]
    count: int


class NoteWritten(BaseModel):
    """The result of a create, update or rename. `links_updated` and
    `notes_rewritten` appear only when the write MOVED the file -- an explicit
    rename, or an update whose new first line changed the name -- since only a
    move rewrites referring wikilinks."""

    path: str
    title: str
    modified: float
    links_updated: int | None = None
    notes_rewritten: int | None = None


class NoteDeleted(BaseModel):
    path: str
    deleted: bool


@router.get("/capabilities", response_model=Capabilities, response_model_exclude_unset=True)
def capabilities() -> dict:
    """List the capabilities this provider exposes."""
    return {"provides": PROVIDES}


@router.get("/notes", response_model=NoteList, response_model_exclude_unset=True)
def list_notes() -> dict:
    """List every note in the vault with cheap metadata."""
    notes = vault.list_notes()
    return {"notes": notes, "count": len(notes)}


@router.get("/notes/{note_path:path}", response_model=Note, response_model_exclude_unset=True)
def get_note(note_path: str) -> dict:
    """Read one note plus its resolved outgoing wikilinks. 404 if missing."""
    try:
        return vault.read_note(note_path)
    except vault.VaultPathError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, f"note not found: {note_path}")


@router.get("/search", response_model=SearchResults, response_model_exclude_unset=True)
def search_notes(q: str = Query(..., min_length=1), limit: int = Query(50, ge=1, le=500)) -> dict:
    """Case-insensitive full-text search across the vault."""
    results = vault.search(q, limit=limit)
    return {"query": q, "results": results, "count": len(results)}


@router.get("/backlinks/{note_path:path}", response_model=Backlinks, response_model_exclude_unset=True)
def get_backlinks(note_path: str) -> dict:
    """Notes whose wikilinks resolve to this note. 404 if the target is missing."""
    try:
        links = vault.backlinks(note_path)
    except vault.VaultPathError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, f"note not found: {note_path}")
    return {"path": note_path, "backlinks": links, "count": len(links)}


@router.post("/notes", response_model=NoteWritten, status_code=201, response_model_exclude_unset=True)
def create_note(body: NoteCreate) -> dict:
    """Create a new note. 409 if it already exists, 400 on an unsafe path."""
    try:
        return vault.create_note(body.path, body.content)
    except vault.VaultPathError as exc:
        raise HTTPException(400, str(exc))
    except FileExistsError:
        raise HTTPException(409, f"note already exists: {body.path}")


@router.put("/notes/{note_path:path}", response_model=NoteWritten, response_model_exclude_unset=True)
def update_note(note_path: str, body: NoteUpdate) -> dict:
    """Overwrite an existing note. 404 if missing, 400 on an unsafe path."""
    try:
        return vault.update_note(note_path, body.content)
    except vault.VaultPathError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, f"note not found: {note_path}")


@router.post("/notes/{note_path:path}/rename", response_model=NoteWritten, response_model_exclude_unset=True)
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


@router.delete("/notes/{note_path:path}", response_model=NoteDeleted, response_model_exclude_unset=True)
def delete_note(note_path: str) -> dict:
    """Delete a note. 404 if missing, 400 on an unsafe path."""
    try:
        return vault.delete_note(note_path)
    except vault.VaultPathError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, f"note not found: {note_path}")
