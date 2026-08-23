#!/usr/bin/env python3
"""Thin CLI over backend/services/presence.py, so a shell worker can say it is
here and what it is doing.

Step B4 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`.
This shim exists for the same reason `project_tasks.py` does: the participants
that actually hold trees -- `project_ralph.sh`, `ralph.sh`, `queue_runner.sh`,
`cron_agent.sh` -- are separate OS processes, and presence has to be writable
from bash or it cannot see any of them.

    presence.py heartbeat <session_id> [--kind chat|run] [--activity "..."]
                          [--subject-kind ticket|worktree|none] [--subject-id ID]
                          [--scope SCOPE] [--principal WHO] [--pid PID]
                          [--profile heartbeat|pid] [--ttl SECONDS]
                          [--path] [key=val ...]
    presence.py get|release <session_id>
    presence.py list [--all] [--kind K] [--subject-kind K] [--subject-id ID] [--scope S]
    presence.py directory [same filters]
    presence.py prune [--older-than SECONDS]

Every command prints JSON on stdout (except `heartbeat --path`, which prints the
record's path, for the callers that used to read that from `heartbeat.sh`).

The record's `expires_at` is computed by the module from the server clock, never
from anything passed here: a caller supplies a TTL (a duration), never an
instant. `--profile pid` opts the record into the liveness accelerator and is
only correct when the pid outlives the beat -- a long-running loop, not a shell
that exits immediately after writing.

NO VENV RE-EXEC, unlike project_tasks.py, and the write path is why: presence is
file-backed with no relational half, so a beat has no silent-wrong-store failure
mode to guard against, and it stays runnable under whatever python3 a sweep
happens to have. That matters -- a heartbeat that needs the backend's venv is a
heartbeat the sweeps cannot write.

The cost lands on `directory` alone: resolving a TICKET's title reaches
`services/tasks.py`, whose two migrated scopes read Postgres, and a bare python3
with no owner pinned cannot. The join degrades to a null `subject_label` there
rather than guessing, and the REST route (`GET /presence/directory`, served in
the backend where an owner is pinned) is the reader that always resolves. Every
other verb here is unaffected.
"""
from __future__ import annotations

import sys
from pathlib import Path

# backend/ on path so `from services import presence` resolves the same channel
# the API reads.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from services import presence  # noqa: E402

if __name__ == "__main__":
    sys.exit(presence.main(sys.argv[1:]))
