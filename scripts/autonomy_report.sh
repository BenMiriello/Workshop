#!/bin/bash
# autonomy_report.sh — daily visibility tick.
#
# Once per day, summarize what the autonomous loop did in the last 24h and
# drop the summary into ~/workshop/.runtime/notify-masini.txt so masini
# surfaces it on his next Discord inbound. Operator should never wonder
# "did anything happen overnight?" — this answers it.
#
# Sources:
#   - cron_agent.log  (last-24h entries)
#   - .runtime/agent-runs.jsonl  (structured tick records)
#   - .runtime/agent-runs-completed.jsonl  (event consumer records)
#   - .runtime/ralph.log  (per-ralph iteration log)
#   - .runtime/work_verifier.log + work_generator.log  (pre-flight stats)
#   - TODO.md  (current queue state)
#
# Output is plain text — no markdown — because notify-masini.txt is sourced
# into Discord directly.

set -u

WORKSHOP=/home/clawd-a/workshop
NOTIFY=$WORKSHOP/.runtime/notify-masini.txt
mkdir -p "$(dirname "$NOTIFY")"

# Pause-until guard: skip silently if autonomy is paused until a future PT
# date (self-clearing). See scripts/pause_guard.sh.
source /home/clawd-a/workshop/scripts/pause_guard.sh
workshop_pause_guard "$WORKSHOP/.runtime/pause.log" "autonomy_report"

NOW_ISO=$(date -Iseconds)
SINCE_EPOCH=$(date -d 'yesterday' +%s)

# Count [x] vs [ ] vs [~] vs [proposed] in TODO.md.
OPEN=$(grep -c '^\[ \]' "$WORKSHOP/TODO.md" 2>/dev/null); OPEN=${OPEN:-0}
DONE=$(grep -c '^\[x\]' "$WORKSHOP/TODO.md" 2>/dev/null); DONE=${DONE:-0}
BLOCKED=$(grep -c '^\[~\]' "$WORKSHOP/TODO.md" 2>/dev/null); BLOCKED=${BLOCKED:-0}
PROPOSED=$(grep -c '^\[proposed\]' "$WORKSHOP/TODO.md" 2>/dev/null); PROPOSED=${PROPOSED:-0}

# Count cron ticks in last 24h. Filter agent-runs.jsonl by ts.
TICKS_24H=0
TICK_DONE_24H=0
TICK_FAIL_24H=0
TICK_INCOMPLETE_24H=0
if [[ -r "$WORKSHOP/.runtime/agent-runs.jsonl" ]]; then
  STATS=$(python3 - <<'PYEOF' "$WORKSHOP/.runtime/agent-runs.jsonl" "$SINCE_EPOCH"
import json, sys
from datetime import datetime
path, since = sys.argv[1], int(sys.argv[2])
ticks = done = fail = incomplete = 0
try:
    with open(path, 'r') as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = rec.get('ts','')
            try:
                ep = int(datetime.fromisoformat(ts.replace('Z','+00:00')).timestamp())
            except Exception:
                continue
            if ep < since: continue
            ticks += 1
            done += int(rec.get('tasks_done', 0) or 0)
            fail += int(rec.get('tasks_failed', 0) or 0)
            incomplete += int(rec.get('tasks_timeout', 0) or 0) + int(rec.get('tasks_blocked', 0) or 0)
except OSError:
    pass
print(f"{ticks} {done} {fail} {incomplete}")
PYEOF
)
  read -r TICKS_24H TICK_DONE_24H TICK_FAIL_24H TICK_INCOMPLETE_24H <<<"$STATS"
fi

# Count event consumes in last 24h.
EVENTS_24H=0
if [[ -r "$WORKSHOP/.runtime/agent-runs-completed.jsonl" ]]; then
  EVENTS_24H=$(python3 - <<'PYEOF' "$WORKSHOP/.runtime/agent-runs-completed.jsonl" "$SINCE_EPOCH"
import json, sys
from datetime import datetime
path, since = sys.argv[1], int(sys.argv[2])
n = 0
try:
    with open(path, 'r') as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = rec.get('ts','')
            try:
                ep = int(datetime.fromisoformat(ts.replace('Z','+00:00')).timestamp())
            except Exception:
                continue
            if ep >= since: n += 1
except OSError:
    pass
print(n)
PYEOF
)
fi

# Last completed task title (for "latest activity" line).
LAST_ACTIVITY=$(grep -E '^\[20' "$WORKSHOP/TODO.md" 2>/dev/null | head -1 | head -c 200)

# Write the report line into notify-masini.txt.
{
  printf '[%s] DAILY AUTONOMY REPORT — 24h summary\n' "$NOW_ISO"
  printf '  Queue: open=%d done=%d blocked=%d proposed=%d\n' "$OPEN" "$DONE" "$BLOCKED" "$PROPOSED"
  printf '  Ticks: total=%d task-done=%d task-incomplete=%d task-failed=%d\n' "$TICKS_24H" "$TICK_DONE_24H" "$TICK_INCOMPLETE_24H" "$TICK_FAIL_24H"
  printf '  Events processed: %d\n' "$EVENTS_24H"
  if [[ -n "$LAST_ACTIVITY" ]]; then
    printf '  Latest activity: %s\n' "$LAST_ACTIVITY"
  fi
  if (( TICK_DONE_24H == 0 && OPEN > 0 )); then
    printf '  WARN: 0 completions in 24h despite %d open tasks — autonomy stalled, please check.\n' "$OPEN"
  fi
} >> "$NOTIFY"

echo "wrote daily report to $NOTIFY"
