"""Workshop's trash: every automatic removal lands here first.

A removal is a rename into a trash directory on the SAME filesystem, so it is
instant, needs no free space and can be undone by the reverse rename. Each entry
is `<trash>/<id>/payload` beside an `entry.json` saying where it came from, which
app and kind it was, why it went and how big it was. Entries older than the
recovery window are deleted for good by `empty`.

The runtime directory's own trash serves its filesystem; any other filesystem
gets a `.workshop-trash-<uid>` directory at the highest point on it this process
can write, and that location is recorded so the trash can be listed as a whole.
A chat's `rm` (`turn_rm`) makes the same directories from inside its sandbox,
where this record is out of reach, so `roots` also finds them at and above the
trees a turn can be given: the workshop's, every app's, and the extra binds.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path

from services.storage import measure
from wsplatform import paths

ENTRY = "entry.json"
PAYLOAD = "payload"


def _home() -> Path:
    return paths.runtime_dir() / "storage" / "trash"


def _known_file() -> Path:
    return paths.runtime_dir() / "storage" / "trash-roots.json"


def _turn_roots() -> list[Path]:
    from services import scoped_exec
    trees = [paths.workshop_root(), *measure.apps()[0].values(),
             *(Path(src) for _, src, _ in scoped_exec._configured_binds())]
    name = f".workshop-trash-{os.getuid()}"
    return [top / name for tree in trees for top in (tree, *tree.parents)
            if (top / name).is_dir()]


def roots() -> list[Path]:
    try:
        extra = json.loads(_known_file().read_text())
    except (OSError, json.JSONDecodeError):
        extra = []
    found = [_home(), *(Path(p) for p in extra if isinstance(p, str)), *_turn_roots()]
    seen: set[Path] = set()
    return [p for p in found if not (p.resolve() in seen or seen.add(p.resolve()))]


def _remember(root: Path) -> None:
    known = [str(p) for p in roots()[1:]]
    if str(root) not in known:
        _known_file().parent.mkdir(parents=True, exist_ok=True)
        _known_file().write_text(json.dumps(known + [str(root)]))


def _root_for(target: Path) -> Path:
    dev = target.lstat().st_dev
    home = _home()
    home.mkdir(parents=True, exist_ok=True)
    if home.stat().st_dev == dev:
        return home
    for parent in reversed(target.parents):
        if not parent.exists() or parent.stat().st_dev != dev:
            continue
        candidate = parent / f".workshop-trash-{os.getuid()}"
        try:
            candidate.mkdir(exist_ok=True)
        except OSError:
            continue
        _remember(candidate)
        return candidate
    raise OSError(f"no writable trash on the filesystem holding {target}")


def put(target: Path, *, app: str, kind: str, reason: str) -> dict:
    """Move `target` into the trash and return its entry."""
    if target.is_dir() and not target.is_symlink():
        size = measure.walk(target, set(), set())[0]
    else:
        size = target.lstat().st_blocks * 512
    entry = {
        "id": f"{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}",
        "origin": str(target), "app": app, "kind": kind, "reason": reason,
        "bytes": size, "trashed_at": time.time(),
    }
    slot = _root_for(target) / entry["id"]
    slot.mkdir()
    (slot / ENTRY).write_text(json.dumps(entry))
    try:
        os.rename(target, slot / PAYLOAD)
    except OSError:
        shutil.rmtree(slot, ignore_errors=True)
        raise
    return {**entry, "slot": str(slot)}


def entries() -> list[dict]:
    out = []
    for root in roots():
        for slot in root.glob("*/" + ENTRY):
            try:
                out.append({**json.loads(slot.read_text()), "slot": str(slot.parent)})
            except (OSError, json.JSONDecodeError):
                continue
    return sorted(out, key=lambda e: -e.get("trashed_at", 0))


def restore(entry_id: str) -> dict:
    """Put an entry back where it came from. Refuses when that path is taken."""
    for e in entries():
        if e["id"] != entry_id:
            continue
        origin = Path(e["origin"])
        if origin.exists() or origin.is_symlink():
            raise FileExistsError(f"{origin} exists; move it aside first")
        origin.parent.mkdir(parents=True, exist_ok=True)
        os.rename(Path(e["slot"]) / PAYLOAD, origin)
        shutil.rmtree(e["slot"])
        return e
    raise KeyError(entry_id)


def _writable(func, path, _exc) -> None:
    """Read-only directories (package caches make them) block deleting what is
    inside; open the parent up and retry."""
    os.chmod(os.path.dirname(path), stat.S_IRWXU)
    func(path)


def empty(older_than_days: float, *, now: float | None = None,
          keep_free: Callable[[int], int] | None = None) -> dict:
    """Delete for good every entry trashed more than `older_than_days` ago.

    The recovery window is kept only while there is room for it. With
    `keep_free` (a disk's total size to the bytes to keep free on it), an entry
    inside the window still goes, oldest first, while its disk has less free
    than that: deleting something only into a trash on the same disk gives
    nothing back, so a full disk would otherwise stay full for the window."""
    cutoff = (now or time.time()) - older_than_days * 86400
    removed, freed, short = [], 0, []
    for e in sorted(entries(), key=lambda e: e.get("trashed_at", 0)):
        aged = e.get("trashed_at", 0) <= cutoff
        if not aged:
            if keep_free is None:
                continue
            du = shutil.disk_usage(e["slot"])
            if du.free >= keep_free(du.total):
                continue
            short.append(e["id"])
        shutil.rmtree(e["slot"], onexc=_writable)
        removed.append(e["id"])
        freed += e.get("bytes", 0)
    return {"removed": removed, "bytes": freed, "for_room": short}
