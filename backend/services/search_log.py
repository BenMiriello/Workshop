"""search_log -- one line per retrieval call, beside the index it read.

WHY THIS EXISTS. "Does anyone actually use search" has been argued from
impressions for as long as the tool has existed, and an impression cannot tell a
tool that is never called from one that is called constantly and never helps.
Both look identical from outside. One line per call makes it a count: how often
retrieval was reached for, what was asked, how much of the corpus was in play,
whether anything came back, and whether the caller then read the whole of what
it found.

That last part is why `open_ref` logs here too rather than somewhere of its own.
A search followed by an open is the surfacing mechanism working; a search
followed by nothing is a passage the reader took on trust or discarded. Neither
is visible unless both ends are recorded against the same turn.

WHAT IS RECORDED, AND WHAT IS NOT. The query text, because a log of retrieval
without the questions is a count with nothing to learn from. The counts, the top
score, and whether the result was empty. NOT the passages, NOT the body
`open_ref` returned: the corpus is already on disk and copying it into a log
beside itself doubles the store to say nothing new.

WHERE. `queries.jsonl` in the owner's own runtime directory, beside the index --
the same per-owner partition the index and the repetition store already use, so
one person's retrieval history cannot land in another's directory.

Never raises. A logging failure must not fail the search that was actually
asked for; a missing line is a gap in a count, and a raised exception is a tool
call the model sees fail.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("workshop.search_log")

LOG_NAME = "queries.jsonl"


def log_path() -> Path | None:
    """`queries.jsonl` beside the index, or None when the index directory
    cannot be resolved (no owner pinned, an unwritable runtime root)."""
    try:
        from services import semantic_index
        return semantic_index.index_dir() / LOG_NAME
    except Exception:
        log.debug("search log path unavailable", exc_info=True)
        return None


def record(tool: str, **fields: Any) -> None:
    """Append one call. `tool` is the tool name; everything else rides as-is.

    Keys the callers use, so a reader does not have to guess the shape:
    `query`, `kinds`, `scope`, `searched`, `corpus`, `top_score`, `empty`,
    `ref`, `part`, `parts`, `turn`, `thread`, `refused`.
    """
    # THE WHOLE BODY IS INSIDE THE GUARD, not just the write. Resolving the
    # path, serialising a field a caller passed, and the append itself can each
    # fail, and any of them escaping would fail the search that was actually
    # asked for -- a tool call the model watches fail, in exchange for a line in
    # a count. A missing line is a gap in a statistic; a raised exception is a
    # broken tool.
    try:
        path = log_path()
        if path is None:
            return
        row: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": tool,
        }
        # Absent is not the same as null: a field the caller had nothing to say
        # about is left out, so a reader counting `empty` is counting calls that
        # reported it rather than calls that defaulted to it.
        row.update({k: v for k, v in fields.items() if v is not None})
        line = json.dumps(row, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        log.debug("could not append to the search log", exc_info=True)


def read(limit: int = 0) -> list[dict]:
    """The recorded calls, oldest first. `limit` > 0 returns the last `limit`.

    For counting retrieval use from a script or a test. Unreadable lines are
    skipped rather than fatal: a half-written last line is what a crash during
    an append looks like, and it must not make the whole history unreadable.
    """
    path = log_path()
    if path is None or not path.exists():
        return []
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
    except OSError:
        return []
    return out[-limit:] if limit > 0 else out
