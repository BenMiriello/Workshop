"""Which kind each part of an app's tree is.

`work` is the default for anything not positively identified, so an unknown file
is never a deletion candidate. Only paths git ignores can be `rebuildable` or
`throwaway` by convention; an app widens or narrows that with a `storage` block
in its manifest, whose paths are taken as declared.
"""
from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from services.app_runner import AppError, _read_manifest_file

WORK, REBUILDABLE, THROWAWAY, CHECKOUT = "work", "rebuildable", "throwaway", "checkout"
KINDS = (WORK, REBUILDABLE, THROWAWAY, CHECKOUT)
DECLARABLE = (REBUILDABLE, THROWAWAY)

THROWAWAY_DIRS = frozenset({"tmp", "scratch"})
# A chat's trash (`turn_rm`) inside an app's tree: the trash's own, not the app's.
TRASH_PREFIX = ".workshop-trash-"
REBUILDABLE_DIRS = frozenset({
    "node_modules", ".venv", "venv", "__pycache__", "dist",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".next", ".turbo", ".parcel-cache", ".vite",
})


class Item(NamedTuple):
    rel: str
    kind: str


def _by_name(rel: str) -> str:
    """The outermost conventional directory on the path decides, so `tmp` inside
    a dependency tree is part of that tree."""
    for part in PurePosixPath(rel).parts:
        if part in THROWAWAY_DIRS:
            return THROWAWAY
        if part in REBUILDABLE_DIRS:
            return REBUILDABLE
    return WORK


def ignored(repo: Path) -> list[str]:
    """Paths git ignores, a wholly ignored directory collapsed to one entry.
    An empty list for a tree that is not a repo."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--others", "--ignored",
             "--exclude-standard", "--directory"],
            capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if res.returncode != 0:
        return []
    return [p.rstrip("/") for p in res.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def declared(app_root: Path) -> dict[str, list[str]]:
    """The manifest's `storage` block, `{kind: [relative path, ...]}`. Paths that
    are absolute or climb out of the tree are dropped."""
    try:
        block = (_read_manifest_file(app_root) or {}).get("storage") or {}
    except AppError:
        return {}
    out: dict[str, list[str]] = {}
    for kind in DECLARABLE:
        paths = block.get(kind) if isinstance(block, dict) else None
        for raw in paths if isinstance(paths, list) else []:
            rel = PurePosixPath(str(raw))
            if rel.is_absolute() or ".." in rel.parts or not rel.parts:
                continue
            out.setdefault(kind, []).append(str(rel))
    return out


def classify(app_root: Path) -> list[Item]:
    """The tree's non-`work` items plus its ignored `work` items, declared paths
    first. Everything not listed is `work`."""
    items = [Item(rel, kind) for kind, rels in declared(app_root).items() for rel in rels]
    claimed = [PurePosixPath(i.rel) for i in items]
    for rel in ignored(app_root):
        path = PurePosixPath(rel)
        if path.parts[0].startswith(TRASH_PREFIX):
            continue
        if not any(path == c or c in path.parents for c in claimed):
            items.append(Item(rel, _by_name(rel)))
    return items
