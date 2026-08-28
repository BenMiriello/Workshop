"""The one file API, addressed by WORKSPACE rather than by app
(docs/platform-redesign/file-viewer-and-editor-2026-08-22.md sec 6).

  GET /files/tree     ?workspace=&path=   one level, dirs-first, reports what it filtered
  GET /files/content  ?workspace=&path=   one file, with `writable`
  GET /files/raw      ?workspace=&path=   one file's bytes, with its media type
  PUT /files/content                       write one text file

`workspace` is a reference `services/app_files.resolve` understands --
`app:<slug>`, `project:<slug>`, `documents` -- and this router owns none of
that vocabulary: an unknown reference comes back from the resolver as
`WorkspaceNotFound` carrying the reason, which is served as a 404 with that
reason as the detail. Adding a workspace kind is a row in `app_files._RESOLVERS`
and changes nothing here.

WHY THE OLD ROUTES ARE STILL HERE. `/apps/{id}/files*` (routes/apps.py) is the
same service through an app id, and `projects.py:get_tree`, `agents.py` and
`docs_index.py` are the three older, disagreeing surfaces sec 2.2 measured.
Retiring them is item 3 of sec 8, deliberately parked so this stage is not a
migration. Nothing new should be built against them.

Every JSON response carries a real model, because a route that answers `dict`
generates `Record<string, never>` in `frontend/src/api/schema.gen.ts` and the
frontend hand-writes the shape instead (ADR 0023 clause 6, and
`scripts/check_opaque_fields.py` is the ratchet). `/files/raw` is the one
exception and is not one at all: its body is bytes, so there is no schema to
declare.

WRITABILITY IS THE BACKEND'S ANSWER. A read reports `writable` for the path it
just read, and a write to a path the workspace marks read-only is refused with
a 403. The frontend does not compute it and does not need to know which
workspace is writable where.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from services import app_files

router = APIRouter(prefix="/files", tags=["files"])


class FileEntry(BaseModel):
    name: str
    path: str
    kind: str  # "file" | "dir"
    size: int | None = None
    modified: str | None = None


class FileTree(BaseModel):
    workspace: str
    path: str
    root: str
    entries: list[FileEntry]
    # Directory names the noise filter (.git/, node_modules/, .venv/,
    # __pycache__/) excluded from THIS listing, so filtering is visible rather
    # than silent. Absent when nothing was filtered at this level.
    filtered: list[str] | None = None


class FileContent(BaseModel):
    workspace: str
    path: str
    content: str
    size: int
    modified: str | None = None
    # About the BYTES: is this text, and small enough to put in an editor.
    editable: bool
    truncated: bool
    encoding: str  # "utf-8" | "binary"
    # About the LOCATION: does this workspace permit a write here at all.
    writable: bool


class FileWriteIn(BaseModel):
    workspace: str
    path: str
    content: str


class FileWriteOut(BaseModel):
    workspace: str
    path: str
    size: int
    modified: str
    writable: bool


class FileRenameIn(BaseModel):
    workspace: str
    path: str
    # A bare filename. `app_files.rename_file` refuses one with a separator:
    # this renames within a directory and is deliberately not a move.
    name: str


class FileRenameOut(FileWriteOut):
    # Refs that named this file by its OLD path and have been rewritten to name
    # the new one, plus the ones that could not be. Empty for a workspace
    # nothing refs, which is every workspace except `documents`.
    rewrote: list[str] = []
    stale: list[str] = []


def _workspace(ref: str) -> app_files.Workspace:
    try:
        return app_files.resolve(ref)
    except app_files.WorkspaceNotFound as e:
        raise HTTPException(404, str(e))


@router.get("/tree", response_model=FileTree, response_model_exclude_unset=True)
async def file_tree(workspace: str = Query(...), path: str = Query("")) -> dict:
    ws = _workspace(workspace)
    try:
        return {"workspace": ws.ref, **app_files.list_tree(ws, path)}
    except app_files.AppFilePathError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "path not found")
    except NotADirectoryError:
        raise HTTPException(400, "path is not a directory")


@router.get("/content", response_model=FileContent, response_model_exclude_unset=True)
async def file_content(workspace: str = Query(...), path: str = Query(...)) -> dict:
    ws = _workspace(workspace)
    try:
        return {"workspace": ws.ref, **app_files.read_file(ws, path)}
    except app_files.AppFilePathError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "file not found")
    except IsADirectoryError:
        raise HTTPException(400, "path is a directory, not a file")


@router.get("/raw")
async def file_raw(workspace: str = Query(...), path: str = Query(...)) -> FileResponse:
    """The BYTES of one file, with its media type, so a browser can display it
    itself. `/content` answers with JSON and reports a binary file's content as
    absent, which is right for an editor and useless for an image or a PDF;
    this is the same file through the door those need.

    Read-only by construction: there is no write counterpart and there does not
    need to be, since `PUT /content` already owns writes and is text-only.
    Containment is `app_files.open_file`, which is the same `safe_path` call
    `/content` makes, so this reaches nothing a text read could not.

    THE TWO HEADERS ARE NOT DECORATION. An SVG is served with its real type so
    an `<img>` will draw it, and an SVG is also a document that can carry
    script. `default-src 'none'` means a tab opened straight at this URL
    executes nothing, and `nosniff` stops a browser from re-typing a file it
    was told about. Both cost one line and close the only way this route could
    run agent-authored markup as same-origin code.

    No response model: the body is bytes, not a schema. That is the one shape
    ADR 0023 clause 6's "declare what you return" does not apply to.
    """
    ws = _workspace(workspace)
    try:
        target, media = app_files.open_file(ws, path)
    except app_files.AppFilePathError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "file not found")
    except IsADirectoryError:
        raise HTTPException(400, "path is a directory, not a file")
    return FileResponse(
        str(target),
        media_type=media,
        headers={
            "Content-Disposition": "inline",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/rename", response_model=FileRenameOut, response_model_exclude_unset=True)
async def file_rename(body: FileRenameIn) -> dict:
    """Rename one file within its own directory, and keep the refs pointing at it.

    THE REFS ARE THE REASON THIS IS NOT A ONE-LINER. `workshop:doc/<path>`
    resolves against the repo root and the backlink index stores the RESOLVED
    target, and NOTHING rewrites a stored target when the thing it names moves
    (services/backlinks.py has three mutators and all three are keyed by the
    SOURCE). So renaming a file under `docs/` used to be a silent way to break
    every link to it: the row survives, and the edge disappears the next time
    the source that wrote it happens to be saved.

    That is the same defect the note retitle fixed on 2026-08-27, in the other
    half of the same store, and it is fixed the same way: the inbound sources
    are read from the backlink index, not scanned for, and each one's body is
    rewritten. A source that cannot be rewritten is REPORTED rather than
    swallowed, because a half-updated link graph the operator does not know
    about is worse than one they do.
    """
    ws = _workspace(body.workspace)
    try:
        row = app_files.rename_file(ws, body.path, body.name)
    except app_files.AppFileReadOnlyError as e:
        raise HTTPException(403, str(e))
    except app_files.AppFilePathError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "no such file")
    except FileExistsError as e:
        raise HTTPException(409, f"{e} already exists")
    except IsADirectoryError:
        raise HTTPException(400, "path is a directory, not a file")

    rewrote: list[str] = []
    stale: list[str] = []
    if row["path"] != body.path:
        from services import doc_refs
        rewrote, stale = doc_refs.repoint(body.path, row["path"])
    return {"workspace": ws.ref, **row, "rewrote": rewrote, "stale": stale}


@router.put("/content", response_model=FileWriteOut, response_model_exclude_unset=True)
async def file_write(body: FileWriteIn) -> dict:
    ws = _workspace(body.workspace)
    try:
        return {"workspace": ws.ref, **app_files.write_file(ws, body.path, body.content)}
    except app_files.AppFileReadOnlyError as e:
        raise HTTPException(403, str(e))
    except app_files.AppFilePathError as e:
        raise HTTPException(400, str(e))
    except app_files.AppFileBinaryError as e:
        raise HTTPException(400, str(e))
    except IsADirectoryError:
        raise HTTPException(400, "path is a directory, not a file")
