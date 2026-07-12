#!/usr/bin/env python3
"""Thin CLI over backend/services/tasks.py so shell (project_ralph.sh) can drive
a project's structured task store WITHOUT desyncing it from TODO.md.

The store (projects/<slug>/.tasks.json) is authoritative; TODO.md is its export.
project_ralph.sh must not sed-edit TODO.md directly (that would drift), so it
picks and marks tasks through this helper instead.

Usage:
  project_tasks.py next-open <project_dir>
      -> prints JSON {"id","text","owner"} of the first `todo` task, or {} if none.
  project_tasks.py set-status <project_dir> <task_id> <todo|doing|done|blocked>
      -> updates the task; prints JSON of the updated task or {}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# backend/ on path so `from services import tasks` resolves the same store the
# API uses.
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))

from services import tasks  # noqa: E402


def _next_open(root: Path) -> dict:
    for t in tasks.list_tasks(root, include_done=False):
        if t.get("status") == "todo":
            return {"id": t["id"], "text": t.get("text", ""), "owner": t.get("owner", "operator")}
    return {}


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: project_tasks.py <next-open|set-status> <project_dir> [task_id status]", file=sys.stderr)
        return 2
    cmd, root = argv[1], Path(argv[2])
    if cmd == "next-open":
        print(json.dumps(_next_open(root)))
        return 0
    if cmd == "set-status":
        if len(argv) < 5:
            print("usage: project_tasks.py set-status <project_dir> <task_id> <status>", file=sys.stderr)
            return 2
        task_id, status = argv[3], argv[4]
        try:
            updated = tasks.update(root, task_id, status=status)
        except ValueError as e:
            print(json.dumps({"error": str(e)}))
            return 1
        print(json.dumps(updated or {}))
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
