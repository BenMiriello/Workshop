"""Parse and serialize GitHub-style markdown task checklists (TODO.md).

Per ADR-009 section 4 the TODO surface becomes a structured checklist instead
of a raw textarea. The on-disk file stays markdown so the agent can still
read and edit it; this module provides the parse/serialize/CRUD seam the
HTTP layer needs.

Item shape:
  { "id": str, "text": str, "done": bool, "source": "operator"|"agent"|None,
    "line": int }

IDs are deterministic from line position (f"t{line_index}") and therefore
shift when the file is reorganised. The UI treats them as ephemeral — fetch,
mutate, re-fetch.
"""
from __future__ import annotations

import re
from pathlib import Path

_ITEM_RE = re.compile(r"^\s*-\s+\[([ xX])\]\s+(.*)$")
_SOURCE_TAG_RE = re.compile(r"\s*<!--\s*from:(operator|agent)\s*-->\s*$")
_VALID_SOURCES = {"operator", "agent"}


def parse(text: str) -> list[dict]:
    """Parse a TODO.md body into an ordered list of task items.

    Detects "- [ ] foo" and "- [x] foo" (case-insensitive on x). An optional
    trailing tag "  <!-- from:agent -->" or "  <!-- from:operator -->" marks
    the source. IDs are f"t{line_index}" based on zero-indexed line position
    within the original text so they are stable for a single read/write cycle.
    """
    items: list[dict] = []
    for idx, raw_line in enumerate((text or "").splitlines()):
        m = _ITEM_RE.match(raw_line)
        if not m:
            continue
        check, body = m.group(1), m.group(2)
        source: str | None = None
        tag = _SOURCE_TAG_RE.search(body)
        if tag:
            source = tag.group(1)
            body = body[:tag.start()].rstrip()
        items.append({
            "id": f"t{idx}",
            "text": body.strip(),
            "done": check.lower() == "x",
            "source": source,
            "line": idx,
        })
    return items


def serialize(items: list[dict], *, header: str = "# TODO\n\n") -> str:
    """Re-emit a stable TODO.md from a list of items.

    Preserves the source tag if set. Always emits a trailing newline so the
    file ends cleanly.
    """
    lines: list[str] = []
    for item in items:
        text = (item.get("text") or "").strip()
        box = "x" if item.get("done") else " "
        source = item.get("source")
        suffix = f"  <!-- from:{source} -->" if source in _VALID_SOURCES else ""
        lines.append(f"- [{box}] {text}{suffix}")
    body = "\n".join(lines)
    if body and not body.endswith("\n"):
        body += "\n"
    return f"{header}{body}"


def _read(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _reparse(path: Path) -> list[dict]:
    return parse(_read(path))


def list_items(path: Path) -> list[dict]:
    """Read and parse the TODO file at `path`."""
    return _reparse(path)


def add(text: str, *, source: str = "operator", path: Path) -> dict:
    """Append a new task to the TODO file and return the new item."""
    text = (text or "").strip()
    if not text:
        raise ValueError("text required")
    if source not in _VALID_SOURCES:
        source = "operator"
    items = _reparse(path)
    items.append({"text": text, "done": False, "source": source})
    _write(path, serialize(items))
    refreshed = _reparse(path)
    return refreshed[-1] if refreshed else {"text": text, "done": False, "source": source, "id": "t0", "line": 0}


def toggle(item_id: str, *, path: Path) -> dict | None:
    """Flip the done flag for the matching task. Returns the updated item or
    None if no task matches the id.
    """
    items = _reparse(path)
    for item in items:
        if item["id"] == item_id:
            item["done"] = not item["done"]
            _write(path, serialize(items))
            for refreshed in _reparse(path):
                if refreshed["text"] == item["text"] and refreshed["done"] == item["done"]:
                    return refreshed
            return item
    return None


def edit(item_id: str, *, text: str, path: Path) -> dict | None:
    """Replace the body of the matching task. Returns the updated item or
    None if no task matches.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("text required")
    items = _reparse(path)
    for item in items:
        if item["id"] == item_id:
            item["text"] = text
            _write(path, serialize(items))
            for refreshed in _reparse(path):
                if refreshed["text"] == text:
                    return refreshed
            return item
    return None


def remove(item_id: str, *, path: Path) -> bool:
    """Drop the matching task. Returns True if a task was removed."""
    items = _reparse(path)
    keep = [i for i in items if i["id"] != item_id]
    if len(keep) == len(items):
        return False
    _write(path, serialize(keep))
    return True


def set_done(item_id: str, *, done: bool, path: Path) -> dict | None:
    """Set the done flag explicitly (used by PATCH when the client sends a
    concrete value rather than relying on toggle semantics).
    """
    items = _reparse(path)
    for item in items:
        if item["id"] == item_id:
            if item["done"] == done:
                return item
            item["done"] = done
            _write(path, serialize(items))
            for refreshed in _reparse(path):
                if refreshed["text"] == item["text"] and refreshed["done"] == done:
                    return refreshed
            return item
    return None
