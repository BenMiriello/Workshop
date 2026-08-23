#!/usr/bin/env python3
"""Thin CLI over backend/services/run_control.py, so a sweep can be spoken to.

Step B6 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`
(section 8). `scripts/project_ralph.sh` is a bash loop with no stdin, so the
channel a chat gets for free -- a queue and an injectable running turn -- has to
be a FILE it reads at the top of each iteration.

    control.py status|pending|clear|forget <run-id>
    control.py take <run-id> [--format json|text]
    control.py submit <run-id> "<text>" [--verb queue|steer|yield] [--actor WHO]
                                        [--request-id ID]
    control.py cancel <run-id> <request-id>
    control.py yield <run-id> [--actor WHO] [--reason "..."]
    control.py yielding <run-id>              # exit 0 when a yield is standing
    control.py snapshot <run-id> --repo DIR   # -> refs/wip/<run-id>

READS NEVER FAIL. A malformed, truncated or unreadable channel prints an empty
answer and exits 0, because the caller is a sweep and a bookkeeping file must
never be able to stop one.

NO VENV RE-EXEC, exactly like `scripts/presence.py` and unlike
`scripts/project_tasks.py`: the module behind this one is stdlib-only, so it
runs under whatever python3 a sweep happens to have. A control channel the
sweeps cannot read would be no channel at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

# backend/ on path so `from services import run_control` resolves the same
# channel the API writes.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from services import run_control  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_control.main(sys.argv[1:]))
