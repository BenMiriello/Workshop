"""One-shot: move the four in-source state files under the runtime root.

Layer 0 of docs/platform-redesign/managed-file-references-and-path-scoping-2026-08-18.md
evicts `grants.json`, `resource-grants.json`, `apps.json` and `store-policies.json`
out of `backend/` (the source tree) and into the untracked runtime root, so no
mutable file lives where code lives. The readers already resolve their path
through `wsplatform.paths` with a one-time fallback to the old in-source location,
so a read works before this migration runs. This function does the physical move,
once, at startup — the same shape as `chat_log_turn_rename.run_once`.

WHY A MOVE AND NOT A COPY. `resource-grants.json` is written by the running
backend (~136 KB, live). A copy would leave two files that immediately diverge:
the fallback reader would keep serving the stale source copy until the new one
happened to be written. `os.replace` is atomic and, because `.runtime/` and
`backend/` sit on the same filesystem, it is a rename, not a byte copy — the file
is moved once and is never half-present in both trees (the "a moved file is moved
once, never copied" rule from the stage-2 transcript lesson and §10 of the plan).

WHY IT RUNS AT STARTUP. The move must happen while the process that reads the
files is the one about to serve them, and before any write. `main.py` calls
`run_once()` in the startup path. It is idempotent: once a file is at its new
path the old one is gone, so the next boot moves nothing. A conservation line
(bytes and record count, before and after) is logged for every file it moves,
per the plan's requirement that a data move report what it conserved.

grants.json is (still) tracked in git; after this move the live copy is the
runtime one and the tracked source copy is stale. Deleting the tracked file is
left to a human post-verify (the plan is explicit: move now, delete after the
move is confirmed), so this function does NOT touch git and does NOT delete the
source copy when the new file already exists — it only relocates a file whose new
location is still absent.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from wsplatform import paths

log = logging.getLogger("workshop.state_file_relocate")


def _record_count(path: Path) -> int | None:
    """A best-effort count of the top-level records in a state file, for the
    conservation line. None when the file will not parse as JSON — the byte count
    is still reported, so an unparseable file is not silently 'conserved' as 0."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        # {"grants": [...], "guests": [...]} / {"policies": [...]} / {app_id: {...}}
        lists = [v for v in data.values() if isinstance(v, list)]
        if lists:
            return sum(len(v) for v in lists)
        return len(data)
    if isinstance(data, list):
        return len(data)
    return None


def run_once() -> list[str]:
    """Relocate every evicted state file whose new path is still absent, moving
    old -> new atomically. Returns a human-readable conservation line per file
    actually moved (empty when there was nothing to move)."""
    moved: list[str] = []
    for old, new in paths.state_file_relocations():
        try:
            if new.exists() or not old.exists():
                # Already relocated, or nothing to relocate. Both are the
                # steady state after the first successful boot.
                continue
            before_bytes = old.stat().st_size
            before_records = _record_count(old)
            new.parent.mkdir(parents=True, exist_ok=True)
            os.replace(old, new)
            after_bytes = new.stat().st_size
            after_records = _record_count(new)
            line = (
                f"{old.name}: moved {old} -> {new} "
                f"(bytes {before_bytes}->{after_bytes}, "
                f"records {before_records}->{after_records})"
            )
            moved.append(line)
            log.info("state file relocated: %s", line)
        except OSError as e:
            # A failed move must not take the backend down: the fallback reader
            # keeps serving the old in-source copy until the next boot retries.
            log.error("state file relocate failed for %s: %s", old, e)
    return moved
