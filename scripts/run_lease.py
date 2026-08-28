#!/usr/bin/env python3
"""Thin CLI over backend/services/run_worktree.py, so a shell worker can take,
renew and give up the lease on the WORKTREE it was handed.

The run-side twin of `scripts/lease.py`, which covers the generic lease verbs.
This one knows the extra thing a shell caller cannot compute for itself: which
lease subject a DIRECTORY is (`services/tree_access.subject_id_for`), so the
sweep and a chat contending for the same tree name it identically.

    run_lease.py enter   --run-id ID --dir DIR [--ttl S]
    run_lease.py beat    --subject-id S --generation N [--parent-pid PID]
                         [--interval S] [--log PATH]
    run_lease.py release --subject-id S --generation N
    run_lease.py get     --dir DIR [--run-id ID]

Exit codes: 0 the verb took, 1 it did not (someone else holds it, or we were
fenced), 2 usage, 3 no lease store / the tree cannot be named.

RE-EXEC INTO THE BACKEND VENV, exactly as `scripts/lease.py` does and for the
measured reason: the `python3` on a sweep's PATH has no sqlalchemy, so without
this every call would report "no lease store" and the sweep would conclude,
silently and wrongly, that this installation cannot hold leases. On 2026-08-27
the same missing re-exec was found to have made `alloc_task_worktree` fail at
import since 2026-08-25, which put every unattended run back in the shared tree.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))

if (importlib.util.find_spec("sqlalchemy") is None
        and not os.environ.get("WORKSHOP_RUN_LEASE_REEXEC")):
    _VENV_PY = _BACKEND / ".venv" / "bin" / "python3"
    if _VENV_PY.exists():
        os.environ["WORKSHOP_RUN_LEASE_REEXEC"] = "1"
        os.execv(str(_VENV_PY),
                 [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

try:
    from services import run_worktree  # noqa: E402
except ModuleNotFoundError:  # no venv on this checkout: answer 3, not a traceback
    print('{"error": "no lease store", "detail": "sqlalchemy is not importable"}')
    sys.exit(3)

if __name__ == "__main__":
    sys.exit(run_worktree.main(sys.argv[1:]))
