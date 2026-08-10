#!/bin/bash
# root_task_counts.sh: emit canonical task-store status counts for a scope.
#
# Reads the structured store (.tasks.json) via services.tasks, so counts are
# correct regardless of the TODO.md projection format. Post the 2026-07-15 root
# migration (O7) the workshop root is an ordinary structured scope, so the old
# prose-grep counters (^[ ], [~], [proposed]) no longer match and must not be
# used. Prints one space-separated line:
#   todo doing in_review blocked parked done cancelled total
# Usage: root_task_counts.sh [scope-dir]   (default: workshop root)
set -u

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DIR=${1:-$WORKSHOP}
PY=$WORKSHOP/backend/.venv/bin/python

if [[ ! -x "$PY" ]]; then
  echo "0 0 0 0 0 0 0 0"
  exit 0
fi

PYTHONPATH=$WORKSHOP/backend "$PY" - "$DIR" <<'PYEOF'
import sys
from pathlib import Path
try:
    from services import tasks
    ts = tasks.list_tasks(Path(sys.argv[1]))
except Exception:
    print("0 0 0 0 0 0 0 0")
    sys.exit(0)
order = ["todo", "doing", "in_review", "blocked", "parked", "done", "cancelled"]
counts = {k: 0 for k in order}
for t in ts:
    s = t.get("status")
    if s in counts:
        counts[s] += 1
print(" ".join(str(counts[k]) for k in order) + " " + str(len(ts)))
PYEOF
