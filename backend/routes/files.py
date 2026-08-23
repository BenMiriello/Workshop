"""The one file API, addressed by WORKSPACE rather than by app
(docs/platform-redesign/file-viewer-and-editor-2026-08-22.md sec 6).

  GET /files/tree     ?workspace=&path=   one level, dirs-first, reports what it filtered
  GET /files/content  ?workspace=&path=   one file, with `writable`
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

Every response carries a real model, because a route that answers `dict`
generates `Record<string, never>` in `frontend/src/api/schema.gen.ts` and the
frontend hand-writes the shape instead (ADR 0023 clause 6, and
`scripts/check_opaque_fields.py` is the ratchet).

WRITABILITY IS THE BACKEND'S ANSWER. A read reports `writable` for the path it
just read, and a write to a path the workspace marks read-only is refused with
a 403. The frontend does not compute it and does not need to know which
workspace is writable where.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
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
