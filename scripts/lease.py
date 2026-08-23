#!/usr/bin/env python3
"""Thin CLI over backend/services/leases.py, so a shell worker can take, renew
and give up a lease.

Step B3 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`.
Section 15 says of this step that "its most important caller is bash":
`project_ralph.sh`, `ralph.sh` and `queue_runner.sh` are separate OS processes
and cannot import anything, so a lease that is only reachable from Python is a
lease none of the participants that actually hold trees can take.

    lease.py acquire ticket|worktree <subject_id> --holder-id ID
             [--holder-kind chat|run] [--ttl SECONDS] [--min-generation N]
    lease.py renew   ticket|worktree <subject_id> <generation> [--ttl S] [--work]
    lease.py release ticket|worktree <subject_id> <generation>
    lease.py release-holder <holder_id> [--holder-kind chat|run]
    lease.py get     ticket|worktree <subject_id>
    lease.py list [--subject-kind K] [--holder-kind K] [--holder-id ID] [--all]

Every command prints JSON on stdout. The EXIT CODE is the part bash should read:

    0  the verb took
    1  it did not (someone else holds the subject, or your generation is stale)
    2  usage error
    3  there is no lease store on this installation

3 is separated from 1 on purpose. "Nobody could take a lease here" is a
different fact from "you lost the race", and a sweep on a file-backed project
tree legitimately carries on without a lease store, exactly as it did before
this table existed.

RE-EXEC INTO THE BACKEND VENV, unlike `scripts/presence.py` and for the same
reason `scripts/project_tasks.py` does it. Presence is file-backed and has no
wrong-store failure mode, so it deliberately runs under whatever python3 a sweep
has. A lease is a Postgres row reached through sqlalchemy, which the SYSTEM
python3 does not have -- so without this re-exec every `lease.py acquire` from a
sweep would report exit 3 and the sweep would conclude, silently and wrongly,
that this installation has no lease store. Checked before choosing where the
logic lives: `python3 -c 'import sqlalchemy'` fails on the interpreter every
sweep uses. A checkout with no venv still runs and still answers 3, which is the
honest answer there.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))

if (importlib.util.find_spec("sqlalchemy") is None
        and not os.environ.get("WORKSHOP_LEASE_CLI_REEXEC")):
    _VENV_PY = _BACKEND / ".venv" / "bin" / "python3"
    if _VENV_PY.exists():
        os.environ["WORKSHOP_LEASE_CLI_REEXEC"] = "1"
        os.execv(str(_VENV_PY),
                 [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

try:
    from services import leases  # noqa: E402
except ModuleNotFoundError:  # no venv on this checkout: answer 3, not a traceback
    print('{"error": "no lease store", "detail": "sqlalchemy is not importable"}')
    sys.exit(3)

if __name__ == "__main__":
    sys.exit(leases.main(sys.argv[1:]))
