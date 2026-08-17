"""Docs index bridge (notes-system plan Phase N3; write path added Phase 4 of
docs/platform-redesign/memory-and-documentation-model-2026-08-14.md sec 11).

Makes the committed `docs/` tree discoverable, and now editable, in-app WITHOUT
moving docs into a store. Long-form plans, designs, and decision records stay
versioned markdown in `docs/` (git history and editor-openability are the
point); this router indexes them, reads one raw, and writes one raw. It never
commits: an edit lands in the working tree and the operator commits it, same as
any other change to a tracked file.

Shapes:
  GET /docs        -> {docs: [{path, title, section, mtime}], count}
  GET /docs/file   -> {path, content} for one doc (path validated within docs/)
  PUT /docs/file   -> {path, content, mtime} after writing (same containment check)
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from wsplatform import paths

log = logging.getLogger("workshop.docs_index")

# Prefix is /documents, not /docs: FastAPI's built-in Swagger UI already owns
# /docs (served at /api/docs behind the edge), which would shadow the list route.
router = APIRouter(prefix="/documents", tags=["docs"])

_HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$")


def _root() -> Path:
    return paths.workshop_root()


def _docs_dir() -> Path:
    return _root() / "docs"


def _title_of(f: Path) -> str:
    """The first `# ` heading in the file, else the filename stem."""
    try:
        with f.open() as fh:
            for _ in range(50):  # a heading in the first lines, not a full read
                line = fh.readline()
                if not line:
                    break
                m = _HEADING_RE.match(line)
                if m:
                    return m.group(1).strip()
    except OSError:
        pass
    return f.stem


def _mtime_iso(f: Path) -> str | None:
    try:
        import datetime
        return datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return None


@router.get("")
def list_docs() -> dict:
    """Every `.md` under docs/, each tagged with its top-level section (the first
    path segment under docs/, or "" for a loose top-level file), title (first
    `# ` heading or filename), repo-relative path, and mtime. Read-only."""
    docs = _docs_dir()
    root = _root()
    out: list[dict] = []
    if docs.is_dir():
        for f in sorted(docs.rglob("*.md")):
            if not f.is_file():
                continue
            rel = f.relative_to(docs)
            section = rel.parts[0] if len(rel.parts) > 1 else ""
            out.append({
                "path": str(f.relative_to(root)),
                "title": _title_of(f),
                "section": section,
                "mtime": _mtime_iso(f),
            })
    out.sort(key=lambda d: (d["section"], d["title"].lower(), d["path"]))
    return {"docs": out, "count": len(out)}


def _resolve_within_docs(path: str) -> Path:
    """`path` resolved against the repo root and confirmed to sit inside
    `docs/`. Shared by the read and write routes so a write can never escape
    the containment the read route already enforces: `..`, an absolute path
    outside the repo, and a symlink that resolves outside `docs/` all fail the
    same `relative_to` check. Raises HTTPException(400) rather than returning
    None, since every caller wants the same status either way."""
    docs = _docs_dir().resolve()
    candidate = (_root() / path).resolve()
    try:
        candidate.relative_to(docs)
    except ValueError:
        raise HTTPException(status_code=400, detail="path must be inside docs/")
    return candidate


@router.get("/file")
def read_doc(path: str = Query(..., description="repo-relative path to a doc under docs/")) -> dict:
    """Raw markdown of a single doc. The path must resolve to a `.md` file inside
    docs/ (traversal outside docs/ is rejected as a correctness check, not a
    permission wall). Read-only."""
    candidate = _resolve_within_docs(path)
    if candidate.suffix != ".md" or not candidate.is_file():
        raise HTTPException(status_code=404, detail="doc not found")
    return {"path": str(candidate.relative_to(_root())), "content": candidate.read_text()}


class PutDocIn(BaseModel):
    path: str
    content: str


@router.put("/file")
def write_doc(body: PutDocIn) -> dict:
    """Write raw markdown to an EXISTING `.md` file inside `docs/`. Same
    containment check as `read_doc`, so a write can escape `docs/` in exactly
    the ways a read cannot -- which is none. Deliberately does not create a new
    file (the doc index is built by walking existing files; a write that could
    create one would need to appear in that walk too, which is a second
    feature this phase does not need) and does not run `git commit`: the
    change lands in the working tree, same as an edit made in a text editor,
    and the operator commits it on their own schedule."""
    candidate = _resolve_within_docs(body.path)
    if candidate.suffix != ".md":
        raise HTTPException(status_code=400, detail="path must be a .md file")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="doc not found")
    candidate.write_text(body.content)
    # The semantic index has just gone out of date (doc is in DEFAULT_KINDS).
    # Same best-effort, never-raise posture as knowledge.put_note: the file is
    # already on disk and is the thing that mattered, so a bookkeeping failure
    # here must never turn into a 500 for a write that already succeeded.
    try:
        from services import semantic_index
        semantic_index.mark_stale(f"doc {body.path}")
    except Exception:
        log.debug("semantic index stale-mark skipped for %s", body.path, exc_info=True)
    return {"path": str(candidate.relative_to(_root())), "content": body.content, "mtime": _mtime_iso(candidate)}
