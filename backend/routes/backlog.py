"""Operator → TODO backlog bridge (Phase 5g).

Lets the masini discord-handler (or any caller) append a flagged item to
TODO.md's Inbox section programmatically. Future Discord `/backlog <text>`
or `!todo <text>` command can hit this endpoint.

Design choices:
- Idempotent enough: dedupes consecutive identical items in the same minute.
- Always inserts under `## Inbox`. Creates the section if missing.
- Never edits other sections — operator alone graduates items from Inbox.
- Returns the line that was written so the caller can confirm or quote-reply.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


router = APIRouter(prefix="/backlog", tags=["backlog"])

TODO_PATH = Path(os.environ.get("WORKSHOP_TODO_PATH", "/home/clawd-a/workshop/TODO.md"))
INBOX_HEADING = "## Inbox"
# Match the heading only at the start of a line, not in prose / code references.
INBOX_HEADING_LINE = f"\n{INBOX_HEADING}\n"


class BacklogItem(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    source: str = "discord"  # discord | manual | webhook | etc.
    tag: str | None = None   # optional category, e.g. "bug", "idea", "followup"


def _find_heading_index(content: str, heading: str) -> int:
    """Return the start index of a level-2 heading line, or -1.
    Matches only at line-start (avoids prose references like `the `## Inbox`
    section` triggering false positives)."""
    needle = f"\n{heading}\n"
    idx = content.find(needle)
    if idx != -1:
        return idx + 1  # skip the leading newline
    if content.startswith(f"{heading}\n"):
        return 0
    return -1


def _ensure_inbox(content: str) -> str:
    """Make sure the TODO has an `## Inbox` section. If absent, insert one
    immediately before `## Log` (so the Log stays last), or at end if no Log."""
    if _find_heading_index(content, INBOX_HEADING) != -1:
        return content
    inbox_block = (
        f"{INBOX_HEADING}\n"
        "<!-- Items submitted via /backlog. Operator graduates them by moving to a phase section. -->\n"
    )
    log_idx = _find_heading_index(content, "## Log")
    if log_idx != -1:
        return content[:log_idx] + inbox_block + "\n" + content[log_idx:]
    return content.rstrip() + "\n\n" + inbox_block + "\n"


def _append_to_inbox(content: str, line: str) -> str:
    """Insert the new line at the END of the Inbox section (just before the
    next `## ` heading, or EOF)."""
    idx = _find_heading_index(content, INBOX_HEADING)
    if idx == -1:
        # Should not happen; _ensure_inbox runs first.
        return content + "\n" + line + "\n"

    # Find the next "## " heading after Inbox.
    after = idx + len(INBOX_HEADING)
    rest = content[after:]
    next_heading_offset = rest.find("\n## ")
    if next_heading_offset == -1:
        # Inbox is the last section. Append at file end.
        return content.rstrip() + "\n" + line + "\n"

    insert_at = after + next_heading_offset
    return content[:insert_at].rstrip() + "\n" + line + "\n\n" + content[insert_at:].lstrip("\n")


@router.post("")
async def add_to_backlog(item: BacklogItem):
    if not TODO_PATH.exists():
        raise HTTPException(status_code=500, detail=f"TODO.md not found at {TODO_PATH}")

    content = TODO_PATH.read_text()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    tag_part = f" [{item.tag}]" if item.tag else ""
    line = f"[ ]{tag_part} ({item.source} {ts}) {item.text.strip()}"

    # Dedupe: if the exact same line was the previous one in Inbox, skip the write.
    if line in content:
        return {"ok": True, "deduped": True, "line": line}

    new_content = _ensure_inbox(content)
    new_content = _append_to_inbox(new_content, line)

    TODO_PATH.write_text(new_content)
    return {"ok": True, "deduped": False, "line": line, "todo_path": str(TODO_PATH)}


@router.get("")
async def list_inbox():
    """Read the current Inbox section. Useful for the UI / a future digest."""
    if not TODO_PATH.exists():
        raise HTTPException(status_code=500, detail=f"TODO.md not found at {TODO_PATH}")
    content = TODO_PATH.read_text()
    idx = _find_heading_index(content, INBOX_HEADING)
    if idx == -1:
        return {"items": [], "exists": False}

    after = idx + len(INBOX_HEADING)
    rest = content[after:]
    next_heading_offset = rest.find("\n## ")
    section_body = rest if next_heading_offset == -1 else rest[:next_heading_offset]

    items = []
    for line in section_body.splitlines():
        s = line.strip()
        if s.startswith("[ ]") or s.startswith("[x]") or s.startswith("[~]"):
            items.append(s)
    return {"items": items, "exists": True, "count": len(items)}
