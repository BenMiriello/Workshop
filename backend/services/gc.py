"""Anti-bloat garbage collection (doc 05 section 7).

Copies must not accumulate forever. This service REPORTS what is reapable and
performs only the SAFE reaps on demand:

  1. Merged worktrees: branches whose tip is already contained in the project's
     main branch are dead weight -- reaping them is non-destructive (the work is
     already in main). Safe to apply.
  2. Auto-checkpoint thinning: keep the most recent N checkpoint commits plus any
     tagged commits; DESCRIBE what would be squashed beyond that. We do NOT
     rewrite history by default -- history-rewrite is exactly what git-guard.py
     blocks for agents -- so this is reporting-only unless an explicit force flag
     is passed (which we intentionally leave to the operator path, not here).
  3. Stopped dev-server runtimes: entries in services.dev_servers.RUNNING whose
     process is gone are stale -- clearing them is safe.

Everything is dry-run / reporting by default. Only merged-worktree removal and
stopped-runtime clearing are applied, and only when apply=True.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from services import git_service, worktrees
from wsplatform import paths

log = logging.getLogger("workshop.gc")

# How many recent auto-checkpoint commits to keep at full granularity.
CHECKPOINT_KEEP = 50

# Auto-checkpoint commits are recognizable by the subject prefix the checkpoint
# helper writes (services/checkpoints.py): "checkpoint: ...".
_CHECKPOINT_PREFIX = "checkpoint:"


# --------------------------------------------------------------------------
# (1) Merged worktrees.
# --------------------------------------------------------------------------
def _projects() -> dict:
    reg = worktrees.PROJECTS_REGISTRY
    if not reg.exists():
        return {}
    try:
        return (json.loads(reg.read_text()).get("projects") or {})
    except Exception:
        return {}


def scan_merged_worktrees() -> tuple[list[dict], list[dict]]:
    """`(candidates, skipped)` across all registered projects.

    CANONICAL "MERGED" CHECK: git_service.is_ancestor(project_dir, branch, main)
    against the LOCAL main branch. worktrees.cleanup_merged uses the same check.
    Two definitions existed briefly (cleanup_merged compared against
    origin/<default_branch> via github.is_merged; this function used the local
    main). Local main is canonical because (1) it works for both bound and
    unbound projects; (2) merge_branch's github.merge_pr fast-forwards local
    main to origin/<base> after every remote merge so both refs agree; (3) no
    network call or fetched origin ref is needed. cleanup_merged now delegates
    to this same check so one definition exists.

    A SKIP IS REPORTED, NOT SWALLOWED. Every project this scan cannot examine
    used to `continue` in silence, so "no project could be examined" and "every
    project was examined and had nothing merged" produced the identical empty
    list, and the sweep reported success either way. Each skip now carries the
    project and the reason, and `reap_merged_worktrees` puts them in its report
    where the caller can see them.
    """
    out: list[dict] = []
    skipped: list[dict] = []

    def skip(slug: str, reason: str) -> None:
        skipped.append({"project_slug": slug, "reason": reason})
        log.warning("gc: skipping project %s: %s", slug, reason)

    for slug, entry in _projects().items():
        try:
            # Namespace-correct: the registry's `dir` string is one mount
            # namespace's spelling, and joining it verbatim onto git is how
            # this scan found zero candidates while worktrees sat on disk.
            project_dir = paths.project_dir(slug, entry)
        except paths.TreeUnresolvable as e:
            skip(slug, str(e))
            continue
        try:
            main = git_service.main_branch(project_dir)
        except Exception as e:
            skip(slug, f"no main branch in {project_dir}: {type(e).__name__}: {e}")
            continue
        try:
            branches = worktrees.list_branches(slug)
        except Exception as e:
            skip(slug, f"could not list branches: {type(e).__name__}: {e}")
            continue
        for b in branches:
            branch = b.get("branch")
            if not branch:
                continue
            try:
                if git_service.is_ancestor(project_dir, branch, main):
                    out.append({
                        "project_slug": b.get("project_slug", slug),
                        "branch_slug": b.get("branch_slug"),
                        "branch": branch,
                        "dir": b.get("dir"),
                        "merged": True,
                    })
            except Exception as e:
                skip(slug, f"merge check failed for {branch}: "
                           f"{type(e).__name__}: {e}")
                continue
    return out, skipped


def list_merged_worktrees() -> list[dict]:
    """Just the candidates from `scan_merged_worktrees`, for callers that act on
    them and do not report. Each item: project_slug, branch_slug, branch, dir,
    merged=True."""
    return scan_merged_worktrees()[0]


def reap_merged_worktrees(*, apply: bool = False) -> dict:
    """Report (apply=False) or remove (apply=True) merged worktrees. Removal
    reuses worktrees.discard_branch, which is the same safe teardown the merge
    path uses (worktree remove --force + branch -D)."""
    merged, skipped = scan_merged_worktrees()
    if not apply:
        return {"applied": False, "candidates": merged, "count": len(merged),
                "skipped": skipped, "skipped_count": len(skipped)}
    reaped, failed = [], []
    for m in merged:
        try:
            # A REFUSAL IS NOT A REAP. `discard_branch` snapshots uncommitted
            # work before it tears a tree down and answers `ok: False` when it
            # could not, leaving the tree alone; counting that as reaped would
            # report the sweep as having cleaned up something still sitting
            # there.
            res = worktrees.discard_branch(m["project_slug"], m["branch_slug"])
            if res.get("ok"):
                reaped.append({**m, "snapshot_ref": res.get("snapshot_ref")})
            else:
                failed.append({**m, "error": res.get("detail") or "refused"})
        except Exception as e:
            failed.append({**m, "error": f"{type(e).__name__}: {e}"})
    return {"applied": True, "reaped": reaped, "failed": failed,
            "count": len(reaped),
            "skipped": skipped, "skipped_count": len(skipped)}


# --------------------------------------------------------------------------
# (2) Checkpoint thinning -- REPORT ONLY (no history rewrite by default).
# --------------------------------------------------------------------------
def describe_checkpoint_thinning(repo: Path, *, keep: int = CHECKPOINT_KEEP) -> dict:
    """Identify auto-checkpoint commits beyond the most-recent `keep` that are
    candidates for squashing, EXCLUDING any tagged commit. Returns a description
    only -- nothing is rewritten (history-rewrite is blocked for agents). The
    operator can act on this report through a deliberate, non-agent path."""
    if not (repo / ".git").exists() and not (repo.is_dir() and git_service.is_repo(repo)):
        return {"repo": str(repo), "is_repo": False, "total_checkpoints": 0,
                "kept": 0, "squashable": 0, "candidates": []}
    commits = git_service.log(repo, max_count=5000)
    tagged = git_service.tag_shas(repo)
    checkpoints: list[tuple[str, str]] = []
    for c in commits:
        subject = c["subject"].strip()
        if subject.startswith(_CHECKPOINT_PREFIX):
            checkpoints.append((c["sha"], subject))
    total = len(checkpoints)
    # checkpoints[] is newest-first (git log order). Keep the first `keep`.
    kept = checkpoints[:keep]
    older = checkpoints[keep:]
    squashable = [
        {"sha": sha[:12], "subject": subj}
        for sha, subj in older if sha not in tagged
    ]
    return {
        "repo": str(repo),
        "is_repo": True,
        "total_checkpoints": total,
        "kept": len(kept),
        "squashable": len(squashable),
        "candidates": squashable[:200],
        "note": "report-only; checkpoint history is not rewritten (agents are "
                "blocked from history rewrite by git-guard). Squash these via a "
                "deliberate operator action if desired.",
    }


def checkpoint_report(*, keep: int = CHECKPOINT_KEEP) -> list[dict]:
    """Per-project checkpoint thinning report across registered projects."""
    out: list[dict] = []
    for slug, entry in _projects().items():
        try:
            repo = paths.project_dir(slug, entry)
        except paths.TreeUnresolvable as e:
            # Reported as a row rather than dropped, for the same reason the
            # merged scan reports its skips: a project missing from the report
            # is indistinguishable from a project with nothing to thin.
            out.append({"project_slug": slug, "repo": str(entry.get("dir") or ""),
                        "is_repo": False, "total_checkpoints": 0, "kept": 0,
                        "squashable": 0, "candidates": [], "note": str(e)})
            log.warning("gc: checkpoint report skipping %s: %s", slug, e)
            continue
        rep = describe_checkpoint_thinning(repo, keep=keep)
        rep["project_slug"] = slug
        out.append(rep)
    return out


# --------------------------------------------------------------------------
# (3) Stopped dev-server runtimes.
# --------------------------------------------------------------------------
def _dev_servers():
    try:
        from services import dev_servers  # type: ignore
        return dev_servers
    except Exception:
        return None


def list_stopped_runtimes() -> list[dict]:
    """Runtimes registered in dev_servers.RUNNING whose process is no longer
    alive. Returns [] if the dev-server registry is absent."""
    ds = _dev_servers()
    if ds is None:
        return []
    stopped = []
    for slug, srv in list(getattr(ds, "RUNNING", {}).items()):
        st = ds.status(slug)
        if not st.get("running"):
            stopped.append({"slug": slug, "pid": getattr(srv, "pid", None),
                            "port": getattr(srv, "port", None)})
    return stopped


def reap_stopped_runtimes(*, apply: bool = False) -> dict:
    """Report (apply=False) or clear (apply=True) stale dev-server entries.
    dev_servers.status() already drops a dead entry as a side effect when it
    probes; we call it explicitly so a reported entry is also cleared."""
    ds = _dev_servers()
    if ds is None:
        return {"available": False, "applied": apply, "candidates": [], "count": 0}
    stopped = list_stopped_runtimes()
    if not apply:
        return {"available": True, "applied": False, "candidates": stopped,
                "count": len(stopped)}
    cleared = []
    for s in stopped:
        # status() already evicts a dead entry; ensure it is gone.
        ds.status(s["slug"])
        getattr(ds, "RUNNING", {}).pop(s["slug"], None)
        cleared.append(s)
    return {"available": True, "applied": True, "cleared": cleared,
            "count": len(cleared)}


# --------------------------------------------------------------------------
# Aggregate preview / run.
# --------------------------------------------------------------------------
def preview() -> dict:
    """Everything reapable, no side effects (except dev_servers.status() which
    naturally evicts already-dead entries when probed -- harmless)."""
    return {
        "merged_worktrees": reap_merged_worktrees(apply=False),
        "checkpoints": checkpoint_report(),
        "stopped_runtimes": reap_stopped_runtimes(apply=False),
    }


def run(*, apply: bool = True) -> dict:
    """Perform the SAFE reaps (merged worktrees + stopped runtimes). Checkpoint
    history is REPORTED only, never rewritten here."""
    return {
        "merged_worktrees": reap_merged_worktrees(apply=apply),
        "stopped_runtimes": reap_stopped_runtimes(apply=apply),
        "checkpoints": checkpoint_report(),
    }
