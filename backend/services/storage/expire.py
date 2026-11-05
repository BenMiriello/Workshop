"""The retention pass: expire throwaway files, park or remove idle working
copies, reap merged branches, and empty the trash past its recovery window.
When a disk is under its keep-free floor (`short`), throwaway files go after a
day and the trash gives back its oldest entries until the floor is met.

Planned first, then applied, so a dry run reports exactly what an applied run
would do. Nothing that a running process has open, or uses as its working
directory, is touched; a protected app is skipped entirely. The pass's last
result is saved as a receipt beside the usage scan.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from services import gc, git_service
from services.storage import checkouts, kinds, measure, policy, trash
from wsplatform import paths

KEEP_FILE = ".keep"
PRESSED_DAYS = 1.0


def receipt_path() -> Path:
    return paths.runtime_dir() / "storage" / "last-pass.json"


def last() -> dict | None:
    try:
        return json.loads(receipt_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None


def busy_paths() -> set[Path]:
    """Working directories and open files of every process this user can see,
    in host spelling."""
    out: set[Path] = set()
    for pid in filter(str.isdigit, os.listdir("/proc")):
        base = f"/proc/{pid}"
        links = [f"{base}/cwd"]
        try:
            links += [f"{base}/fd/{fd}" for fd in os.listdir(f"{base}/fd")]
        except OSError:
            pass
        for link in links:
            try:
                target = os.readlink(link)
            except OSError:
                continue
            if target.startswith("/"):
                out.add(measure._host(target.removesuffix(" (deleted)")))
    return out


def _in_use(path: Path, busy: set[Path]) -> bool:
    return any(p == path or path in p.parents for p in busy)


def _kept(directory: Path) -> set[str]:
    try:
        return {ln.strip() for ln in (directory / KEEP_FILE).read_text().splitlines() if ln.strip()}
    except OSError:
        return set()


def _size_and_newest(path: Path) -> tuple[int, float]:
    st = path.lstat()
    if path.is_dir() and not path.is_symlink():
        size, newest = measure.walk(path, set(), set())
        return size, max(newest, st.st_mtime)
    return st.st_blocks * 512, st.st_mtime


def _act(trees: dict, decision: dict) -> dict:
    if decision["action"] == checkouts.KEEP:
        return decision
    try:
        return checkouts.act(trees[decision["app"]], decision)
    except (OSError, git_service.GitError) as err:
        return {**decision, "done": False, "error": str(err)}


def throwaway(app: str, root: Path, *, days: float, busy: set[Path], now: float) -> list[dict]:
    """Entries of the app's throwaway directories untouched for `days`. A name
    listed in the directory's `.keep` file is never expired."""
    out = []
    for item in kinds.classify(root):
        directory = root / item.rel
        if item.kind != kinds.THROWAWAY or not directory.is_dir() or directory.is_symlink():
            continue
        keep = _kept(directory) | {KEEP_FILE}
        for child in directory.iterdir():
            if child.name in keep or _in_use(child, busy):
                continue
            size, newest = _size_and_newest(child)
            idle = (now - newest) / 86400
            if idle >= days:
                out.append({"app": app, "path": str(child), "bytes": size,
                            "why": f"untouched for {idle:.0f} days"})
    return out


def short(doc: dict) -> list[dict]:
    """Every disk holding an app, the workshop or a trash that has less free
    than the keep-free floor, with how much less."""
    held = [paths.workshop_root(), *measure.apps()[0].values(), *trash.roots()]
    out = []
    for d in measure.disks([p for p in held if p.exists()]):
        floor = policy.keep_free_bytes(doc, d["total"])
        if d["free"] < floor:
            out.append({"mount": d["mount"], "free": d["free"], "short": floor - d["free"]})
    return out


def plan(doc: dict, *, now: float, pressed: bool = False) -> dict:
    """`pressed`: a disk is under its floor, so throwaway files go after
    `PRESSED_DAYS` rather than the retention period."""
    busy = busy_paths()
    trees, skipped = measure.apps()
    days = min(doc["retention_days"], PRESSED_DAYS) if pressed else doc["retention_days"]
    found: dict = {"throwaway": [], "checkouts": [], "skipped": skipped}
    for app, root in trees.items():
        if (doc["apps"].get(app) or {}).get("protect"):
            found["skipped"].append({"app": app, "reason": "protected"})
            continue
        found["throwaway"] += throwaway(app, root, days=days, busy=busy, now=now)
        try:
            copies = git_service.worktree_list(root)[1:]
        except git_service.GitError:
            continue
        for wt in copies:
            if measure._host(wt["dir"]).is_dir():
                found["checkouts"].append(checkouts.judge(
                    root, app, wt, retention_days=days, busy=busy, now=now))
    return found


def run(*, apply: bool) -> dict:
    doc, now = policy.read(), time.time()
    pressed = short(doc)
    found = plan(doc, now=now, pressed=bool(pressed))
    result = {"started_at": now, "applied": apply, "short": pressed, **found}
    if apply:
        trees = measure.apps()[0]
        for e in found["throwaway"]:
            try:
                e.update(done=True, trash_id=trash.put(
                    Path(e["path"]), app=e["app"], kind=kinds.THROWAWAY, reason=e["why"])["id"])
            except OSError as err:
                e.update(done=False, error=str(err))
        result["checkouts"] = [_act(trees, d) for d in found["checkouts"]]
        reaped = gc.reap_merged_worktrees(apply=True)
        result["branch_service"] = {"reaped": reaped["count"], "failed": reaped["failed"]}
        result["trash"] = trash.empty(
            doc["trash_days"], now=now,
            keep_free=lambda total: policy.keep_free_bytes(doc, total))
    else:
        cutoff = now - doc["trash_days"] * 86400
        result["trash"] = {"would_remove": [e["id"] for e in trash.entries()
                                            if e.get("trashed_at", 0) <= cutoff]}
    result["seconds"] = round(time.time() - now, 1)
    if apply:
        target = receipt_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result))
    return result
