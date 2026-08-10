#!/bin/bash
# autonomy_report.sh — daily visibility tick.
#
# Once per day, summarize what the autonomous loop did in the last 24h and
# drop the summary into the operator notify queue so the chat bridge
# surfaces it on his next Discord inbound. Operator should never wonder
# "did anything happen overnight?" — this answers it.
#
# Sources:
#   - cron_agent.log  (last-24h entries)
#   - .runtime/jobs.jsonl  (unified Job store: cron tasks, events, sweeps)
#   - .runtime/ralph.log  (per-ralph iteration log)
#   - .runtime/work_verifier.log + work_generator.log  (pre-flight stats)
#   - TODO.md  (current queue state)
#
# Output is plain text — no markdown — because the notify queue is sourced
# into Discord directly.

set -u

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export WORKSHOP
NOTIFY="${WORKSHOP_NOTIFY_FILE:-$WORKSHOP/.runtime/notify.txt}"
mkdir -p "$(dirname "$NOTIFY")"

# Pause-until guard: skip silently if autonomy is paused until a future PT
# date (self-clearing). See scripts/pause_guard.sh.
source "$WORKSHOP/scripts/pause_guard.sh"
workshop_pause_guard "$WORKSHOP/.runtime/pause.log" "autonomy_report"

NOW_ISO=$(date -Iseconds)
SINCE_EPOCH=$(date -d 'yesterday' +%s)

# Queue counts from the canonical task store (post-O7 root migration). The old
# ^[ ] / [x] / [~] / [proposed] prose greps no longer match the regenerated
# store-backed TODO.md; read status counts from the store instead.
read -r C_TODO C_DOING C_IN_REVIEW C_BLOCKED C_PARKED C_DONE C_CANCELLED C_TOTAL \
  < <(bash "$WORKSHOP/scripts/root_task_counts.sh")
OPEN=${C_TODO:-0}
DONE=${C_DONE:-0}
BLOCKED=${C_BLOCKED:-0}
IN_REVIEW=${C_IN_REVIEW:-0}

# Job stats in last 24h, sourced from the unified Job store (.runtime/jobs.jsonl).
# Every bounded execution (cron task, drained event, project sweep, heartbeat) is
# one Job; the old agent-runs.jsonl aggregate + agent-runs-completed.jsonl are
# retired. Records are append-only (start + finish lines), so collapse by id.
JOBS_TOTAL_24H=0
JOBS_DONE_24H=0
JOBS_FAILED_24H=0
EVENTS_24H=0
if [[ -r "$WORKSHOP/.runtime/jobs.jsonl" ]]; then
  STATS=$(python3 - <<'PYEOF' "$WORKSHOP/.runtime/jobs.jsonl" "$SINCE_EPOCH"
import json, sys
from datetime import datetime
path, since = sys.argv[1], int(sys.argv[2])
by_id = {}
try:
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            jid = rec.get('id')
            if not jid:
                continue
            by_id.setdefault(jid, {}).update(rec)
except OSError:
    pass
total = done = fail = events = 0
for r in by_id.values():
    ts = r.get('started_at', '')
    try:
        ep = int(datetime.fromisoformat(str(ts).replace('Z', '+00:00')).timestamp())
    except Exception:
        continue
    if ep < since:
        continue
    total += 1
    st = r.get('status')
    if st == 'done':
        done += 1
    elif st == 'failed':
        fail += 1
    if r.get('trigger') == 'event':
        events += 1
print(f"{total} {done} {fail} {events}")
PYEOF
)
  read -r JOBS_TOTAL_24H JOBS_DONE_24H JOBS_FAILED_24H EVENTS_24H <<<"$STATS"
fi

# Last completed task title (for "latest activity" line), from the store.
LAST_ACTIVITY=$(PYTHONPATH=$WORKSHOP/backend "$WORKSHOP/backend/.venv/bin/python" - <<'PYEOF' 2>/dev/null
from pathlib import Path
try:
    from services import tasks
    done = [t for t in tasks.list_tasks(Path(os.environ['WORKSHOP'])) if t.get('status') == 'done']
    done.sort(key=lambda t: t.get('updated') or '', reverse=True)
    if done:
        print((done[0].get('text') or '').splitlines()[0][:200])
except Exception:
    pass
PYEOF
)

# Write the report line into the notify queue.
{
  printf '[%s] DAILY AUTONOMY REPORT — 24h summary\n' "$NOW_ISO"
  printf '  Queue: open=%d in_review=%d blocked=%d done=%d\n' "$OPEN" "$IN_REVIEW" "$BLOCKED" "$DONE"
  printf '  Jobs: total=%d done=%d failed=%d\n' "$JOBS_TOTAL_24H" "$JOBS_DONE_24H" "$JOBS_FAILED_24H"
  printf '  Events processed: %d\n' "$EVENTS_24H"
  if [[ -n "$LAST_ACTIVITY" ]]; then
    printf '  Latest activity: %s\n' "$LAST_ACTIVITY"
  fi
  if (( JOBS_DONE_24H == 0 && OPEN > 0 )); then
    printf '  WARN: 0 completions in 24h despite %d open tasks — autonomy stalled, please check.\n' "$OPEN"
  fi
} >> "$NOTIFY"

echo "wrote daily report to $NOTIFY"
