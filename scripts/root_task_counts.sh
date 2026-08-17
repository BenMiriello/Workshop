#!/bin/bash
# root_task_counts.sh: emit canonical task-store status counts for a scope.
#
# Reads the canonical store via services.tasks, so counts are correct regardless
# of the TODO.md projection format. Post the 2026-07-15 root migration (O7) the
# workshop root is an ordinary structured scope, so the old prose-grep counters
# (^[ ], [~], [proposed]) no longer match and must not be used. Prints one
# space-separated line:
#   todo doing in_review blocked parked done cancelled total
# Usage: root_task_counts.sh [scope-dir]   (default: workshop root)
#
# IT MUST REACH POSTGRES, and this is a cron script, so it brings its own
# environment. The `development` and `workshop` scopes keep their tickets in
# Postgres; the connection string lives in secrets.env, which cron does not read
# (only systemd units do), and `services.ownership` fails closed on an unpinned
# owner. Neither was set up here. The read therefore fell back to the retired
# `.tasks.json`, and the operator's 07:00 autonomy report and 14:00 digest spent
# days counting one ticket in the cancelled column that Postgres had as todo.
# Both fallbacks are gone; what is left is getting the environment right.
set -u

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DIR=${1:-$WORKSHOP}
PY=$WORKSHOP/backend/.venv/bin/python

SECRETS="${WORKSHOP_CONFIG_DIR:-$HOME/.config/workshop}/secrets.env"
if [[ -z "${WORKSHOP_DATABASE_URL:-}" && -r "$SECRETS" ]]; then
  set -a; source "$SECRETS"; set +a
fi

if [[ ! -x "$PY" ]]; then
  echo "0 0 0 0 0 0 0 0"
  exit 0
fi

PYTHONPATH=$WORKSHOP/backend "$PY" - "$DIR" <<'PYEOF'
import sys
from pathlib import Path
try:
    from services import auth_store, ownership, tasks
    # The data owner every personal store is partitioned by. Derived from the
    # app's own operator record, never a literal: a literal here is exactly the
    # per-deployment identity scripts/check_no_hardcoding.sh exists to catch.
    owner = auth_store.operator_id()
    if owner is None:
        raise RuntimeError("no operator account on this checkout")
    ownership.set_current_owner(owner)
    ts = tasks.list_tasks(Path(sys.argv[1]))
except Exception as e:
    # Zeros, but never silently: a digest that reports 0/0/0 is visibly broken,
    # whereas a count off by one out of the wrong store reads as the truth.
    print(f"root_task_counts: {type(e).__name__}: {e}", file=sys.stderr)
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
