"""`rm` and `unlink` for a chat turn: what they remove goes to the Workshop trash.

Installed first on a turn's PATH by `turn_trash`, so a plain `rm` anywhere in
the turn (a script, `xargs rm`, `find -exec rm`) is undoable and runs without
asking (`tool_policy`). It keeps rm's own options and messages. Three kinds of
path are deleted outright, because they hold nothing to lose: scratch (`/tmp`,
`tmp/`, `scratch/`), dependency and cache folders (`REBUILDABLE_DIRS`, the same
names `storage.kinds` treats as rebuildable), and what is already in a trash.

Everything else is renamed into `<top>/.workshop-trash-<uid>/<id>/payload`
beside an `entry.json`, the layout `storage.trash` reads, lists, restores and
empties. `<top>` is the highest folder on the same disk this process may write,
so the rename is instant and needs no space. A path the turn sees through its
sandbox mount is recorded in host spelling (`WORKSHOP_MOUNT_SOURCE`), so the
platform restores it where it really was.

A removal that cannot reach the trash is refused and left in place; it is never
turned into a permanent delete. Copied whole into the turn, so it imports
nothing of the backend.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import sys
import time
from pathlib import Path

SCRATCH_DIRS = frozenset({"tmp", "scratch"})
SCRATCH_ROOTS = ("/tmp/", "/var/tmp/")
REBUILDABLE_DIRS = frozenset({
    "node_modules", ".venv", "venv", "__pycache__", "dist",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".next", ".turbo", ".parcel-cache", ".vite",
})
TRASH_PREFIX = ".workshop-trash-"
MOUNTPOINT = "/workspace"
_LONG = {"--recursive": "r", "--force": "f", "--dir": "d", "--verbose": "v"}
_IGNORED_LONG = ("--interactive", "--one-file-system", "--preserve-root", "--no-preserve-root")


class Usage(Exception):
    pass


def parse(args: list[str]) -> tuple[set[str], list[str]]:
    flags, paths, ended = set(), [], False
    for a in args:
        if ended or a == "-" or not a.startswith("-"):
            paths.append(a)
        elif a == "--":
            ended = True
        elif a.startswith("--"):
            if a in _LONG:
                flags.add(_LONG[a])
            elif not a.startswith(_IGNORED_LONG):
                raise Usage(f"unrecognized option '{a}'")
        else:
            for c in a[1:]:
                if c in "rR":
                    flags.add("r")
                elif c in "fdv":
                    flags.add(c)
                elif c not in "iI":
                    raise Usage(f"invalid option -- '{c}'")
    return flags, paths


def outright(path: Path) -> bool:
    """Scratch, rebuildable, or already in a trash: deleted, not trashed."""
    text = str(path)
    return (text.startswith(SCRATCH_ROOTS) or text in ("/tmp", "/var/tmp")
            or any(p in SCRATCH_DIRS or p in REBUILDABLE_DIRS or p.startswith(TRASH_PREFIX)
                   for p in path.parts))


def _open_up(func, path, _exc) -> None:
    os.chmod(os.path.dirname(path), stat.S_IRWXU)
    func(path)


def delete(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        on_error = "onexc" if sys.version_info >= (3, 12) else "onerror"
        shutil.rmtree(path, **{on_error: _open_up})
    else:
        os.unlink(path)


def size(path: Path) -> int:
    if not path.is_dir() or path.is_symlink():
        return path.lstat().st_blocks * 512
    total = 0
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                total += os.lstat(os.path.join(root, name)).st_blocks * 512
            except OSError:
                continue
    return total


def host(path: Path) -> str:
    source = os.environ.get("WORKSHOP_MOUNT_SOURCE")
    text = str(path)
    if source and (text == MOUNTPOINT or text.startswith(MOUNTPOINT + "/")):
        return source.rstrip("/") + text[len(MOUNTPOINT):]
    return text


def trash_root(path: Path) -> Path:
    dev = path.lstat().st_dev
    for top in reversed(path.parents):
        try:
            if top.stat().st_dev != dev or not os.access(top, os.W_OK):
                continue
        except OSError:
            continue
        root = top / f"{TRASH_PREFIX}{os.getuid()}"
        root.mkdir(exist_ok=True)
        ignore = root / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n")
        return root
    raise OSError("there is no folder on its disk this turn may write to")


def to_trash(path: Path) -> None:
    entry = {
        "id": f"{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}",
        "origin": host(path), "app": os.environ.get("WORKSHOP_CHAT_SCOPE", ""),
        "kind": "removed",
        "reason": "rm in chat: " + (os.environ.get("WORKSHOP_THREAD_TITLE") or "untitled"),
        "bytes": size(path), "trashed_at": time.time(),
    }
    slot = trash_root(path) / entry["id"]
    slot.mkdir()
    (slot / "entry.json").write_text(json.dumps(entry))
    try:
        os.rename(path, slot / "payload")
    except OSError:
        shutil.rmtree(slot, ignore_errors=True)
        raise


def remove(arg: str, flags: set[str], name: str) -> str | None:
    """Remove one operand as `rm` would. An error message, or None."""
    path = Path(os.path.abspath(arg))
    if not os.path.lexists(path):
        return None if "f" in flags else f"cannot remove '{arg}': No such file or directory"
    if path.is_dir() and not path.is_symlink() and "r" not in flags:
        if "d" not in flags:
            return f"cannot remove '{arg}': Is a directory"
        try:
            os.rmdir(path)
        except OSError as e:
            return f"cannot remove '{arg}': {e.strerror}"
        return None
    if path == Path("/") or path == path.parent:
        return f"it is dangerous to operate recursively on '{arg}'"
    try:
        delete(path) if outright(path) else to_trash(path)
    except OSError as e:
        return (f"cannot move '{arg}' to the trash ({e.strerror or e}); it was left "
                f"in place. /bin/{name} deletes it for good, which asks the operator.")
    if "v" in flags:
        print(f"removed '{arg}'")
    return None


def main(argv: list[str]) -> int:
    name = os.path.basename(argv[0])
    try:
        if name == "unlink":
            flags, paths = set(), argv[1:]
            if len(paths) != 1:
                raise Usage("unlink takes exactly one file")
        else:
            flags, paths = parse(argv[1:])
            if not paths:
                if "f" in flags:
                    return 0
                raise Usage("missing operand")
    except Usage as e:
        print(f"{name}: {e}", file=sys.stderr)
        return 1
    status = 0
    for arg in paths:
        error = remove(arg, flags, name)
        if error:
            print(f"{name}: {error}", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
