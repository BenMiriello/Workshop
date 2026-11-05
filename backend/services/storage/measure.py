"""Measure what each app holds, by kind.

The scan is a list of UNITS, each a directory owned by one app (or the shared
row) and of one kind: an app's checkouts, its classified items, and its root,
whose remainder is `work`. Every unit is walked once and skips every other unit
nested inside it, and a file reached twice (a hardlink between a dependency
tree and a cache, or between a checkout and its main tree) is counted once, at
the first unit that reaches it. Sizes are allocated blocks, as `du` reports.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

from services.scoped_exec import MOUNTPOINT
from services.storage import kinds
from wsplatform import paths

SHARED = "shared"

# Host-wide tool caches: (name, env override, the tool's own answer, default).
# Asking the tool first honours its config files as well as its env var.
TOOL_CACHES = (
    ("uv", "UV_CACHE_DIR", ["uv", "cache", "dir"], "~/.cache/uv"),
    ("pip", "PIP_CACHE_DIR", ["pip", "cache", "dir"], "~/.cache/pip"),
    ("npm", "npm_config_cache", ["npm", "config", "get", "cache"], None),
    ("huggingface", "HF_HOME", None, "~/.cache/huggingface"),
    ("torch", "TORCH_HOME", None, "~/.cache/torch"),
)


class Unit(NamedTuple):
    app: str
    kind: str
    path: Path
    label: str


def _host(path: str) -> Path:
    """A path recorded inside an agent's sandbox, rebased onto the host tree it
    is bound from, when only the rebased spelling exists. A path this user may
    not stat (a container's own `/root/...` open file, seen through /proc)
    counts as not existing rather than raising."""
    if not os.path.exists(path) and path.startswith(MOUNTPOINT + "/"):
        rebased = paths.workshop_root() / path[len(MOUNTPOINT) + 1:]
        if os.path.exists(rebased):
            return rebased
    return Path(path)


def checkouts(repo: Path) -> list[Path]:
    """Every working copy of `repo` except the main one, in host spelling."""
    try:
        res = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    trees = [line[len("worktree "):] for line in res.stdout.splitlines()
             if line.startswith("worktree ")]
    return [p for p in map(_host, trees[1:]) if p.is_dir()]


def apps() -> tuple[dict[str, Path], list[dict]]:
    """`({slug: tree}, skipped)` for every registered app with a tree here."""
    try:
        records = json.loads(paths.projects_registry().read_text()).get("projects") or {}
    except (OSError, json.JSONDecodeError):
        records = {}
    found, skipped = {}, []
    for slug, record in records.items():
        try:
            found[slug] = paths.project_dir(slug, record)
        except paths.TreeUnresolvable as e:
            skipped.append({"app": slug, "reason": str(e)})
    return found, skipped


def units(trees: dict[str, Path]) -> list[Unit]:
    out: list[Unit] = []
    for slug, root in trees.items():
        out += [Unit(slug, kinds.CHECKOUT, p, str(p)) for p in checkouts(root)]
        out += [Unit(slug, i.kind, root / i.rel, i.rel) for i in kinds.classify(root)]
        out.append(Unit(slug, kinds.WORK, root, "."))
    for name, env, ask, default in TOOL_CACHES:
        p = tool_cache(env, ask, default)
        if p is not None and p.is_dir():
            out.append(Unit(SHARED, kinds.REBUILDABLE, p.resolve(), name))
    return out


def tool_cache(env: str, ask: list[str] | None, default: str | None) -> Path | None:
    if os.environ.get(env):
        return Path(os.environ[env]).expanduser()
    if ask and shutil.which(ask[0]):
        try:
            res = subprocess.run(ask, capture_output=True, text=True, timeout=30)
            if res.returncode == 0 and res.stdout.strip():
                return Path(res.stdout.strip()).expanduser()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return Path(default).expanduser() if default else None


def walk(root: Path, skip: set[str], seen: set[tuple[int, int]]) -> tuple[int, float]:
    """`(bytes, newest mtime)` under `root`, not entering `skip` or symlinks."""
    total, newest, stack = 0, 0.0, [str(root)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if e.is_dir(follow_symlinks=False):
                if e.path not in skip:
                    stack.append(e.path)
                continue
            if (st.st_dev, st.st_ino) in seen:
                continue
            seen.add((st.st_dev, st.st_ino))
            total += st.st_blocks * 512
            newest = max(newest, st.st_mtime)
    return total, newest


def disks(roots: list[Path]) -> list[dict]:
    out: dict[int, dict] = {}
    for root in roots:
        root = root.resolve()
        dev = root.stat().st_dev
        if dev in out:
            continue
        mount = root
        while mount.parent != mount and mount.parent.stat().st_dev == dev:
            mount = mount.parent
        du = shutil.disk_usage(mount)
        out[dev] = {"mount": str(mount), "total": du.total, "used": du.used, "free": du.free}
    return list(out.values())


def scan() -> dict:
    started = time.monotonic()
    trees, skipped = apps()
    plan = units(trees)
    every = {str(u.path) for u in plan}
    seen: set[tuple[int, int]] = set()
    report: dict[str, dict] = {}
    for u in plan:
        size, newest = walk(u.path, every - {str(u.path)}, seen)
        row = report.setdefault(u.app, {"kinds": dict.fromkeys(kinds.KINDS, 0), "items": []})
        row["kinds"][u.kind] += size
        if u.kind != kinds.WORK or u.label != ".":
            row["items"].append({"path": u.label, "kind": u.kind, "bytes": size, "newest": newest})
    for slug, row in report.items():
        row["root"] = str(trees[slug]) if slug in trees else None
        row["total"] = sum(row["kinds"].values())
        row["items"].sort(key=lambda i: -i["bytes"])
    return {
        "scanned_at": time.time(),
        "seconds": round(time.monotonic() - started, 1),
        "apps": report,
        "disks": disks(sorted({u.path for u in plan if u.path.exists()})),
        "skipped": skipped,
    }
