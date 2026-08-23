"""Extract a git ref's tree so a ratchet can measure ITS OWN delta.

WHY THIS EXISTS (2026-08-23). Two whole-tree ratchets --
`check_module_layout.py` and `check_no_app_names.py` -- were failing every
single `scripts/reviewer.sh` run, because they compared the WHOLE tree against
a STATIC pinned baseline. Any debt already present when a worker started --
another agent's concurrent commit, the operator's own edit, a metric that
drifted weeks ago -- failed that worker's review. Measured: 2,066 loop jobs
from 2026-07-18 onward all reported `tasks_done: 0`. Each run collected a
rejection it did not cause, repeated it on retry, tripped the no-progress
thrash guard in `scripts/ralph.sh`, and exited 6, stranding real reviewed work
on locked branches.

THE RULE THIS RESTORES. A run is judged on whether IT made a metric worse, not
on the absolute value of the debt it inherited. The pinned baseline still
governs direction over time (bare runs are unchanged, and
`check_ratchets_not_loosened.py` still blocks re-pinning), so nobody can loosen
the ratchet -- but nobody is blamed for debt that already existed at their own
base commit. Same defect family as the `touches` ticket, where a post-hoc
whole-tree diff blamed a worker for another agent's commits.

HOW. `git archive <ref>` writes exactly the tracked tree at that ref and
nothing else: no working-tree edits, no untracked files, no ignored files, and
(absent a `.gitattributes` export-ignore, which this repo does not have) no
omissions. So a filesystem walk of the extract IS the tracked file list at that
ref, which is what lets `check_no_app_names.py` reuse one scan function for
both sides without needing a git repo in the extract. It costs ~0.1s for this
repo's 1,664 tracked files.

The comparison is always `<ref>` versus the WORKING TREE, not `<ref>` versus
`HEAD`. That matches every other `--only-changed`-style check in reviewer.sh:
uncommitted work is still this run's work and must still be judged.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def extract_into(ref: str, repo: Path, dest: Path) -> Path:
    """Write `repo`'s tracked tree as of `ref` into the existing dir `dest`.

    Raises `subprocess.CalledProcessError` if the ref does not resolve, so a
    caller passing a bad ref gets a loud failure rather than a silently empty
    tree that would make every metric look like an improvement.

    Separated from `extracted_tree` so the SHELL ratchets can reuse the one
    extraction mechanism too (see `__main__` below). A bash checker cannot
    enter a Python context manager, and a second `git archive` invocation
    copy-pasted into shell is exactly how the two sides of a delta stop
    agreeing about what "the base tree" means.
    """
    archive = subprocess.run(
        ["git", "archive", "--format=tar", ref],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    subprocess.run(
        ["tar", "-x", "-C", str(dest)], input=archive, check=True,
        capture_output=True,
    )
    return dest


@contextmanager
def extracted_tree(ref: str, repo: Path) -> Iterator[Path]:
    """Yield a temp dir holding `repo`'s tracked tree exactly as of `ref`."""
    with tempfile.TemporaryDirectory(prefix="ratchet-base-") as tmp:
        yield extract_into(ref, repo, Path(tmp))


def _main(argv: list[str]) -> int:
    """`python3 scripts/_ratchet_delta.py --ref REV --into DIR [--repo DIR]`.

    For `scripts/check_no_hardcoding.sh`, which is bash and manages the temp
    dir itself (mktemp -d + trap). Prints nothing on success; a bad ref exits 2
    with the reason on stderr, which is the same loud failure the Python
    callers get from the raised exception.
    """
    import argparse

    ap = argparse.ArgumentParser(prog="_ratchet_delta")
    ap.add_argument("--ref", required=True)
    ap.add_argument("--into", required=True)
    ap.add_argument("--repo", default=".")
    args = ap.parse_args(argv)
    try:
        extract_into(args.ref, Path(args.repo), Path(args.into))
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"_ratchet_delta: cannot read ref {args.ref!r}: {exc}",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(_main(sys.argv[1:]))
