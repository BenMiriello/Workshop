"""The retired memory prefixes, as redirects.

MEM-15 merged four spellings of one concept into one. `/knowledge/...` and
`/notes...` are gone as real routes and answer here instead, with a 307, which
is the redirect that PRESERVES THE METHOD AND THE BODY: a 301 or 302 turns a PUT
into a GET, and two of these paths are writes.

WHY REDIRECT RATHER THAN DELETE. The generated client and this repo's own
frontend were updated in the same commit, so nothing in the tree calls the old
paths. What is NOT in the tree is every agent that learned a path from an older
orientation manifest, every saved request, and the MCP surface a model may have
been told about. A 307 costs one hop and turns "the endpoint vanished" into "the
endpoint moved", which is the difference between a confusing failure and none.

An app's notes prefix is not here. That prefix belongs to an installed app, not to the
platform's memory, and merging them would put an app inside a platform surface.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter(include_in_schema=False)

# 307 PRESERVES METHOD AND BODY. `/knowledge/{scope}/notes/{id}` is a PUT and a
# DELETE as well as a GET, and a 302 would silently turn the write into a GET
# against the new path and report success for a write that never happened.
_MOVED = 307


def _to(request: Request, path: str) -> RedirectResponse:
    """Redirect to `path`, carrying the query string, which is not optional:
    `/notes?scope=x&q=y` is the only shape the notes listing is ever called in."""
    q = request.url.query
    return RedirectResponse(f"{path}?{q}" if q else path, status_code=_MOVED)


@router.get("/notes")
async def notes_listing(request: Request) -> RedirectResponse:
    return _to(request, "/api/memory/notes")


@router.get("/notes/scopes")
async def notes_scopes(request: Request) -> RedirectResponse:
    return _to(request, "/api/memory/scopes")


@router.get("/notes/scopes/tree")
async def notes_scopes_tree(request: Request) -> RedirectResponse:
    return _to(request, "/api/memory/scopes/tree")


@router.api_route("/knowledge/{scope_key}/notes", methods=["GET"])
async def knowledge_notes(scope_key: str, request: Request) -> RedirectResponse:
    return _to(request, f"/api/memory/scopes/{scope_key}/notes")


@router.api_route("/knowledge/{scope_key}/notes/{note_id}",
                  methods=["GET", "PUT", "DELETE"])
async def knowledge_note(scope_key: str, note_id: str, request: Request) -> RedirectResponse:
    return _to(request, f"/api/memory/scopes/{scope_key}/notes/{note_id}")
