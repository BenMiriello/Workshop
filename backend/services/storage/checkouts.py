"""Which extra working copies to keep, park or remove, and doing it.

A working copy whose branch the worktree service owns (`wt/<app>/...`) is left
to that service's own reaper, which keeps its records straight. Every other one
is judged here:

  * locked, or in use by a running process: kept;
  * merged into the app's trunk with nothing uncommitted: removed;
  * merged, with uncommitted files, and untouched for `MERGED_IDLE_DAYS`:
    removed, its uncommitted files saved first as below. These are what an
    agent leaves behind after landing its work (a dependency symlink, a probe
    file), and waiting out the full retention period let them pile up;
  * otherwise untouched for the retention period: parked, meaning anything
    uncommitted (untracked files included) and any commits the trunk lacks are saved
    to `refs/wip/storage/<name>` first, the same place every machine-taken work
    goes, and the copy is removed;
  * otherwise kept.

"Removed" means moved to the Workshop trash, so even the ignored files a copy
held (dependency trees, data copies) come back on restore.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from services import git_service
from services.storage import measure, trash
from services.worktrees import _canonical_branch, _snapshot_before_teardown

REMOVE, PARK, KEEP = "remove", "park", "keep"
MERGED_IDLE_DAYS = 1.0


def _gitdir_line(tree: Path) -> str | None:
    try:
        text = (tree / ".git").read_text().strip()
    except OSError:
        return None
    return text[len("gitdir: "):] if text.startswith("gitdir: ") else None


def admin_dir(tree: Path) -> Path | None:
    """The copy's own directory under the main repo's `.git/worktrees`."""
    line = _gitdir_line(tree)
    return measure._host(line) if line else None


def _last_active(repo: Path, tree: Path, head: str | None, dirty: list[dict]) -> float:
    times = []
    if head:
        res = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%ct", head],
                             capture_output=True, text=True, timeout=30)
        if res.returncode == 0 and res.stdout.strip():
            times.append(float(res.stdout.strip()))
    for e in dirty:
        try:
            times.append((tree / e["path"]).lstat().st_mtime)
        except OSError:
            continue
    return max(times, default=0.0)


def judge(repo: Path, app: str, wt: dict, *, retention_days: float,
          busy: set[Path], now: float) -> dict:
    tree = measure._host(wt["dir"])
    out = {"app": app, "path": str(tree), "branch": wt.get("branch"), "head": wt.get("head")}
    if (wt.get("branch") or "").startswith(f"wt/{app}/"):
        return {**out, "action": KEEP, "why": "owned by the branch service"}
    if wt.get("locked"):
        return {**out, "action": KEEP, "why": "locked"}
    if any(p == tree or tree in p.parents for p in busy):
        return {**out, "action": KEEP, "why": "in use by a running process"}
    with host_spelled(tree):
        try:
            status = git_service.status(tree)
        except git_service.GitError as e:
            return {**out, "action": KEEP, "why": f"git cannot read it: {e}"}
    dirty = status.get("entries") or []
    main = _canonical_branch(app, repo)
    merged = bool(wt.get("head")) and git_service.is_ancestor(repo, wt["head"], main)
    if merged and not dirty:
        return {**out, "action": REMOVE, "why": f"merged into {main}"}
    idle = (now - _last_active(repo, tree, wt.get("head"), dirty)) / 86400
    if merged and idle >= MERGED_IDLE_DAYS:
        return {**out, "action": REMOVE,
                "why": f"merged into {main}, untouched for {idle:.0f} days"}
    if idle >= retention_days:
        return {**out, "action": PARK, "why": f"untouched for {idle:.0f} days"}
    return {**out, "action": KEEP, "why": f"active {idle:.0f} days ago"}


class host_spelled:
    """While inside, the copy's `.git` pointer names its admin directory as the
    host sees it. A copy an agent made in its sandbox records the sandbox
    spelling, which git on the host cannot follow; the original pointer is put
    back on exit unless `keep()` was called."""

    def __init__(self, tree: Path):
        self.file, self.original = tree / ".git", None
        line = _gitdir_line(tree)
        if line and not Path(line).exists() and admin_dir(tree) and admin_dir(tree).exists():
            self.original = self.file.read_text()

    def __enter__(self):
        if self.original is not None:
            self.file.write_text(f"gitdir: {admin_dir(self.file.parent)}\n")
        return self

    def keep(self) -> None:
        self.original = None

    def __exit__(self, *exc) -> None:
        if self.original is not None:
            self.file.write_text(self.original)


def act(repo: Path, decision: dict) -> dict:
    """Carry out a `remove` or `park` decision."""
    tree = Path(decision["path"])
    name = f"storage/{tree.name.lstrip('.')}-{time.strftime('%Y%m%d')}"
    main = _canonical_branch(decision["app"], repo)
    with host_spelled(tree) as spelled:
        snap = _snapshot_before_teardown(
            tree, name, project_dir=repo,
            branch=decision.get("branch") or decision.get("head"), base_ref=main)
        if snap.get("failed") or not snap.get("snapshottable"):
            return {**decision, "done": False, "error": snap.get("detail") or "not snapshottable"}
        admin = admin_dir(tree)
        entry = trash.put(tree, app=decision["app"], kind="checkout",
                          reason=f"{decision['action']}: {decision['why']}")
        spelled.keep()
    if admin and admin.is_dir() and admin.parent.name == "worktrees":
        shutil.rmtree(admin)
    if decision["action"] == REMOVE and decision.get("branch"):
        git_service.branch_delete(repo, decision["branch"], force=False)
    return {**decision, "done": True, "saved_ref": snap.get("ref"), "trash_id": entry["id"],
            "bytes": entry["bytes"]}
