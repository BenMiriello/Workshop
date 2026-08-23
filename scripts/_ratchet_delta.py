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
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def extracted_tree(ref: str, repo: Path) -> Iterator[Path]:
    """Yield a temp dir holding `repo`'s tracked tree exactly as of `ref`.

    Raises `subprocess.CalledProcessError` if the ref does not resolve, so a
    caller passing a bad ref gets a loud failure rather than a silently empty
    tree that would make every metric look like an improvement.
    """
    with tempfile.TemporaryDirectory(prefix="ratchet-base-") as tmp:
        dest = Path(tmp)
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
        yield dest
