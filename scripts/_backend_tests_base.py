"""Which backend test failures also exist at a base revision.

Split out of check_backend_tests.py, which uses it for `--since`. The failing
ids are re-run, only those, in a throwaway `git worktree` of the base revision,
using the callers' `run_pytest` and `parse_results`, so the environment is the main run's.
A test that passes there and fails now is a regression; one that fails in both
is not this change's doing.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def failing_at(rev: str, ids: list[str], root: Path, timeout: int,
               run_pytest, parse_results) -> set[str] | None:
    """The subset of `ids` that fail at `rev`; None when that cannot be known.

    None means the caller must treat every id as a regression: a check that
    could not run never forgives anything. `instance_isolation::` ids come from
    the whole-session guard, cannot be selected, and are never forgiven.
    """
    selectable = [i for i in ids if "::" in i and not i.startswith("instance_isolation::")]
    if not selectable:
        return set()
    work = Path(tempfile.mkdtemp(prefix="backend-tests-base-"))
    tree = work / "tree"
    try:
        add = subprocess.run(
            ["git", "-C", str(root), "worktree", "add", "-q", "--detach", str(tree), rev],
            capture_output=True, text=True)
        if add.returncode != 0 or not (tree / "backend").is_dir():
            return None
        out = run_pytest(tree / "backend", selectable, timeout)
        failed, errored, passed = parse_results(out)
        if passed + len(failed) + len(errored) == 0:
            return None
        return failed | errored
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    finally:
        subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force",
                        str(tree)], capture_output=True)
        subprocess.run(["git", "-C", str(root), "worktree", "prune"],
                       capture_output=True)
        shutil.rmtree(work, ignore_errors=True)
