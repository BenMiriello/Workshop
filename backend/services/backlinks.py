"""backlinks -- the index side of refs (memory-and-documentation-model-
2026-08-14.md sec 10, sec 7.2: "the backlink index is an index, not a grep").

Refs are extracted from note bodies, ticket bodies and chat receipts AT WRITE
TIME (see `services.refs.extract_refs` and its callers in `services.knowledge`,
`services.tasks` and `services.chat_store`) into one keyed store,
`.runtime/backlinks.json`. An entity page reads `referenced_by` directly;
nothing here scans the corpus at render time.

Storage is ONE direction: `{source_ref: [target_ref, ...]}`. `referenced_by`
inverts it in memory, which is cheap at this corpus size (dozens of sources,
not millions) and is what makes edit and delete correct for free -- a source
whose body changes calls `set_source` with its CURRENT full set of refs, which
REPLACES that source's row rather than adding to it. There is no separate
"remove a ref" operation because there is no per-edge state to remove: the row
IS the source's current set, so a note edited to drop a ref, or deleted
entirely (`set_source(ref, [])` / `remove_source`), leaves no trace of the
dropped edge on the next write."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from services import paths

log = logging.getLogger("workshop.backlinks")


def _path() -> Path:
    return paths.runtime_dir() / "backlinks.json"


_cache: tuple[str, float, dict] | None = None


def _load() -> dict[str, list[str]]:
    global _cache
    p = _path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = -1.0
    key = str(p)
    if _cache is not None and _cache[0] == key and _cache[1] == mtime:
        return _cache[2]
    data: dict[str, list[str]] = {}
    if mtime >= 0:
        try:
            raw = json.loads(p.read_text())
            if isinstance(raw, dict):
                data = {
                    str(k): [str(v) for v in vs]
                    for k, vs in raw.items()
                    if isinstance(vs, list)
                }
        except (json.JSONDecodeError, OSError):
            data = {}
    _cache = (key, mtime, data)
    return data


def _save(data: dict[str, list[str]]) -> None:
    global _cache
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(p)
    _cache = (str(p), p.stat().st_mtime, data)


def set_source(source_ref: str, target_refs: list[str]) -> None:
    """Replace `source_ref`'s outgoing refs with exactly `target_refs`. An
    empty list drops the row entirely -- an edit that removed every ref, or a
    delete, must not leave a stale entry a later `referenced_by` would still
    surface."""
    data = dict(_load())
    targets = sorted(set(target_refs))
    if targets:
        data[source_ref] = targets
    else:
        data.pop(source_ref, None)
    _save(data)


def remove_source(source_ref: str) -> None:
    """A source (a deleted note, a deleted ticket) no longer names anything."""
    set_source(source_ref, [])


def referenced_by(target_ref: str) -> list[str]:
    """Every source ref whose current body names `target_ref`, sorted."""
    data = _load()
    return sorted(src for src, targets in data.items() if target_ref in targets)


def remove_sources_with_prefix(prefix: str) -> None:
    """Drop every source row whose ref starts with `prefix`. For a deleted
    thread's per-message chat refs (`workshop:chat/<thread-id>#`), where the
    caller knows the thread is gone but not which of its messages ever carried
    a receipt worth indexing."""
    data = _load()
    remaining = {k: v for k, v in data.items() if not k.startswith(prefix)}
    if len(remaining) != len(data):
        _save(remaining)
