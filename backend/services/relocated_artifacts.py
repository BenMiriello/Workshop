"""Build outputs that break when the tree is seen at another path.

An agent's sandbox mounts the tree at /workspace while the backend runs it from
the real host path. Anything a build writes that records an absolute path then
fails for whichever side did not create it. A Python venv does it in every
console script's shebang, so `./.venv/bin/pip` exits 127 under the backend.

`STACK_ARTIFACTS` is the one table of such outputs, one row per ecosystem:
where it lives, relative to the service directory or the project root, a probe
that says whether it is broken from here, and optionally a repair. The runner
repairs a broken one before the build, and removes it only when the repair
cannot make it work, so the build recreates it. To cover another ecosystem,
add a row.

REPAIR COMES FIRST because removal is not a cheap rebuild. One `pip install`
from the sandbox writes a single /workspace shebang into an otherwise working
venv; removing the venv for it threw away every package installed outside the
requirements file and reinstalled gigabytes of wheels, so after a host reboot an
app sat in `building` for minutes and then crashed on an import the old venv had.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger("workshop.relocated_artifacts")


def _shebang(entry: Path) -> list[str] | None:
    """The shebang's interpreter and arguments, or None for a non-script."""
    if entry.is_symlink() or not entry.is_file():
        return None
    try:
        with open(entry, "rb") as f:
            first = f.readline(512).decode("utf-8", "replace").strip()
    except OSError:
        return None
    if not first.startswith("#!") or not first[2:].split():
        return None
    return first[2:].split()


def _scripts(bin_dir: Path) -> list[Path]:
    try:
        return list(bin_dir.iterdir())
    except OSError:
        return []


def _scripts_name_missing_interpreter(bin_dir: Path) -> bool:
    for entry in _scripts(bin_dir):
        argv = _shebang(entry)
        if argv and not Path(argv[0]).exists():
            return True
    return False


def _portable_launcher(interpreter: str, args: list[str]) -> str:
    """A launcher that finds the venv's interpreter beside the script itself, so
    it works at every path the tree is mounted at. The second line is shell to
    /bin/sh and an inert string literal to Python (the form pip itself writes
    when a shebang cannot hold the interpreter path)."""
    extra = "".join(f" {a}" for a in args)
    return ("#!/bin/sh\n"
            f"'''exec' \"$(dirname -- \"$(realpath -- \"$0\")\")/{interpreter}\""
            f"{extra} \"$0\" \"$@\"\n"
            "' '''\n")


def _repoint_venv_scripts(root: Path) -> None:
    """Rewrite each console script whose shebang names this venv's interpreter at
    a path not visible from here. A shebang naming anything else is left alone,
    so the probe still reports it and the venv is removed."""
    bin_dir = root / "bin"
    for entry in _scripts(bin_dir):
        argv = _shebang(entry)
        if not argv or Path(argv[0]).exists():
            continue
        named = Path(argv[0])
        own = named.parent.name == "bin" and named.parent.parent.name == root.name
        if not own or not (bin_dir / named.name).exists():
            continue
        try:
            body = entry.read_bytes().split(b"\n", 1)
            rest = body[1] if len(body) > 1 else b""
            entry.write_bytes(_portable_launcher(named.name, argv[1:]).encode() + rest)
        except OSError as e:
            log.warning("could not repoint %s: %s", entry, e)


@dataclass(frozen=True)
class StackArtifact:
    name: str
    directory: str
    is_broken: Callable[[Path], bool]
    repair: Callable[[Path], None] | None = None


STACK_ARTIFACTS: tuple[StackArtifact, ...] = (
    StackArtifact(
        name="python venv",
        directory=".venv",
        is_broken=lambda root: _scripts_name_missing_interpreter(root / "bin"),
        repair=_repoint_venv_scripts,
    ),
)


def drop_broken(project_dir: Path, service_dir: Path) -> list[Path]:
    """Repair every table artifact that is broken from here, and remove the ones
    the repair cannot fix. Returns what it removed."""
    removed: list[Path] = []
    for artifact in STACK_ARTIFACTS:
        for base in dict.fromkeys((service_dir, project_dir)):
            root = base / artifact.directory
            if not root.is_dir() or not artifact.is_broken(root):
                continue
            if artifact.repair is not None:
                artifact.repair(root)
                if not artifact.is_broken(root):
                    log.info("repaired %s %s in place", artifact.name, root)
                    continue
            log.warning("removing broken %s %s so the build recreates it",
                        artifact.name, root)
            shutil.rmtree(root, ignore_errors=True)
            removed.append(root)
    return removed
