"""One-shot: copy the operator's knowledge notes and queue export out of the
source tree and under the runtime root.

The operator's knowledge layer was `shared/knowledge/` and the workshop queue
was `shared/queue/`, both inside the repository, so every note an agent wrote
and every ticket export the queue regenerated was a tracked change to the
product. Both now resolve to `paths.operator_workspace(<under>)`. This copies an
existing install's files across so nothing it had is lost.

COPY, NEVER MOVE, NEVER OVERWRITE. The old files are left where they are (they
are gitignored now, so they no longer show up as changes), and a file that
already exists at the destination is kept as it is. A copy that dies halfway is
finished by the next boot, because only files missing at the destination are
copied.

ONCE. A marker beside the destination records that the copy ran, so a note the
operator deletes after the upgrade is not brought back from the old location on
the next boot. Runs at startup from `main.py`, before anything reads the stores,
the same shape as `state_file_relocate.run_once`.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable

from wsplatform import paths

log = logging.getLogger("workshop.workspace_relocate")


def _queue_dest() -> Path:
    from services import task_scope
    return task_scope.WORKSHOP_SCOPE_DIR


def _knowledge_dest() -> Path:
    return paths.operator_workspace("knowledge")


def _any_file(_: Path) -> bool:
    return True


def _queue_export_only(rel: Path) -> bool:
    # The queue's tickets live in the database; `TODO.md` is its export. The
    # `.tasks.json` beside it is the pre-cutover backup that `db/backfill.py`
    # reads in place, and copying it would hand the new directory a second store.
    return rel.as_posix() == "TODO.md"


def _specs() -> list[tuple[str, Callable[[], Path], Callable[[Path], bool]]]:
    return [
        ("knowledge", _knowledge_dest, _any_file),
        ("queue", _queue_dest, _queue_export_only),
    ]


def marker_path(under: str) -> Path:
    return paths.runtime_dir() / "workspace" / f".{under}.relocated"


def _copy_missing(old: Path, new: Path, include: Callable[[Path], bool]) -> tuple[int, int, int]:
    """Copy every included file under `old` that is absent under `new`.
    Returns (copied, kept_existing, bytes_copied)."""
    copied = kept = nbytes = 0
    for src in sorted(p for p in old.rglob("*") if p.is_file()):
        rel = src.relative_to(old)
        if not include(rel):
            continue
        dst = new / rel
        if dst.exists():
            kept += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        nbytes += src.stat().st_size
    return copied, kept, nbytes


def run_once() -> list[str]:
    """Copy each legacy store whose marker is absent. Returns one line per store
    that was processed on this call (empty once every marker exists)."""
    lines: list[str] = []
    for under, dest, include in _specs():
        marker = marker_path(under)
        try:
            if marker.exists():
                continue
            old, new = paths.legacy_workspace(under), dest()
            if old.resolve() == new.resolve():
                continue
            copied = kept = nbytes = 0
            if old.is_dir():
                copied, kept, nbytes = _copy_missing(old, new, include)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(f"{old} -> {new}\n", encoding="utf-8")
            if copied or kept:
                line = (f"{under}: copied {copied} file(s), {nbytes} bytes, "
                        f"{old} -> {new}; kept {kept} already present")
                lines.append(line)
                log.info("workspace relocated: %s", line)
        except OSError as e:
            # No marker is written, so the next boot retries. The readers see
            # the new (possibly partial) location meanwhile; nothing is deleted.
            log.error("workspace relocate failed for %s: %s", under, e)
    return lines
