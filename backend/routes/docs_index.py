"""Docs index bridge (notes-system plan Phase N3).

Makes the committed `docs/` tree discoverable in-app WITHOUT moving docs into a
store. Long-form plans, designs, and decision records stay versioned markdown in
`docs/` (git history and editor-openability are the point); this router only adds
a read-only index and a raw-markdown read so the Notes UI can surface documents
next to notes. It never writes.

Shapes:
  GET /docs        -> {docs: [{path, title, section, mtime}], count}
  GET /docs/file   -> {path, content} for one doc (path validated within docs/)
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/docs", tags=["docs"])

_HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$")


def _root() -> Path:
    return Path(os.environ.get("WORKSHOP_ROOT", "/home/clawd-a/workshop"))


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


@router.get("/file")
def read_doc(path: str = Query(..., description="repo-relative path to a doc under docs/")) -> dict:
    """Raw markdown of a single doc. The path must resolve to a `.md` file inside
    docs/ (traversal outside docs/ is rejected as a correctness check, not a
    permission wall). Read-only."""
    docs = _docs_dir().resolve()
    candidate = (_root() / path).resolve()
    try:
        candidate.relative_to(docs)
    except ValueError:
        raise HTTPException(status_code=400, detail="path must be inside docs/")
    if candidate.suffix != ".md" or not candidate.is_file():
        raise HTTPException(status_code=404, detail="doc not found")
    return {"path": str(candidate.relative_to(_root())), "content": candidate.read_text()}
