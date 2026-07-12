#!/bin/bash
# project_ralph.sh — per-project autonomous loop (generalized Ralph).
#
# The Layer-3 "conductor" scoped to ONE project instead of the workshop root.
# Closes the long-standing gap where a project's PROJECT.md brief + its own
# TODO.md never reached the autonomous loop: here the loop runs with its cwd
# INSIDE the project tree, so the brief, AGENTS.md, and the project's task queue
# are exactly what the worker sees.
#
# For each open task in the project's structured store it fires scripts/ralph.sh
# --dir <project> (which reviews the project's OWN git repo via REVIEWER_ROOT).
# Task state is driven through scripts/project_tasks.py so .tasks.json / TODO.md
# never drift.
#
# Usage:
#   project_ralph.sh <slug> [--max-wall SEC] [--max-tasks N]
#
# Exit codes:
#   0  — ran (queue drained, budget hit, or nothing to do)
#   2  — project not found / autonomy off / global off-switch
#   4  — startup failure

set -u

WORKSHOP=/home/clawd-a/workshop
AUTH_ENV=/home/clawd-a/.config/clawdbot/auth.env
AUTONOMY_FLAG=$WORKSHOP/.runtime/AUTONOMY_DISABLED
RALPH=$WORKSHOP/scripts/ralph.sh
PTASKS=$WORKSHOP/scripts/project_tasks.py

SLUG=""
MAX_WALL=3000        # 50 min total for a project sweep
MAX_TASKS=20         # cap tasks per sweep
RALPH_MAX_WALL=1200  # per-task wall budget handed to ralph.sh
FORCE=0              # manual operator-triggered run bypasses the global off-switch

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-wall)  MAX_WALL="$2"; shift 2 ;;
    --max-tasks) MAX_TASKS="$2"; shift 2 ;;
    --force)     FORCE=1; shift ;;
    *)           SLUG="$1"; shift ;;
  esac
done

if [[ -z "$SLUG" ]]; then
  echo "usage: project_ralph.sh <slug> [--max-wall SEC] [--max-tasks N]" >&2
  exit 4
fi

# Subscription auth; defend against accidental API-key auth.
if [[ -r "$AUTH_ENV" ]]; then
  set -a; . "$AUTH_ENV"; set +a
fi
unset ANTHROPIC_API_KEY

# Resolve project dir + autonomy mode from the registry.
read -r DIR MODE < <(python3 - "$SLUG" <<'PYEOF'
import json, sys
from pathlib import Path
slug = sys.argv[1]
reg = Path("/home/clawd-a/workshop/projects.json")
try:
    data = json.loads(reg.read_text())
except Exception:
    print(" ")
    raise SystemExit(0)
p = (data.get("projects") or {}).get(slug)
if not p:
    print(" ")
    raise SystemExit(0)
mode = ((p.get("autonomy") or {}).get("mode")) or "off"
print(f"{p.get('dir','')} {mode}")
PYEOF
)

if [[ -z "$DIR" || ! -d "$DIR" ]]; then
  echo "[project_ralph] project not found or no dir: $SLUG" >&2
  exit 2
fi

RUNTIME="$DIR/.runtime"
mkdir -p "$RUNTIME"
LOG="$RUNTIME/ralph.log"
RUNSTATUS="$RUNTIME/ralph-run.json"

ts() { date -Iseconds; }
log() { echo "[$(ts)] [project_ralph] $*" >> "$LOG"; echo "[project_ralph] $*"; }

SELF_PID=$$   # the project_ralph.sh shell — the process the backend liveness-checks

# Status writer — the backend reads this file to report run state to the UI.
write_status() {
  # $1=status $2=extra-json-fragment (optional)
  python3 - "$RUNSTATUS" "$SLUG" "$1" "${2:-}" "$SELF_PID" <<'PYEOF'
import json, sys, time
path, slug, status, extra, self_pid = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
rec = {"slug": slug, "status": status, "pid": int(self_pid), "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
if extra:
    try:
        rec.update(json.loads(extra))
    except Exception:
        pass
# preserve started time across updates
try:
    prev = json.loads(open(path).read())
    if "started" in prev and status != "starting":
        rec["started"] = prev["started"]
except Exception:
    pass
if status == "starting":
    rec["started"] = rec["updated"]
open(path, "w").write(json.dumps(rec) + "\n")
PYEOF
}

# Global off-switch (same flag the workshop cron honors). A scheduled/continuous
# sweep obeys it; a manual operator-triggered run (--force) is a deliberate
# foreground action and bypasses it (the per-project `off` still blocks below).
if [[ -f "$AUTONOMY_FLAG" && "$FORCE" -ne 1 ]]; then
  log "global autonomy off-switch present; not running"
  write_status "skipped" '{"reason":"global-off-switch"}'
  exit 2
fi

# Per-project autonomy must be enabled (manual = operator-triggered runs count;
# only "off" blocks). A manual Run-now still routes through here; the mode gate
# just refuses to run a project the operator has turned off.
if [[ "$MODE" == "off" ]]; then
  log "project autonomy is off for $SLUG; not running"
  write_status "skipped" '{"reason":"autonomy-off"}'
  exit 2
fi

# One sweep per project at a time.
LOCK=/tmp/workshop-project-ralph-$SLUG.lock
exec 8>"$LOCK"
if ! flock -n 8; then
  log "another sweep already running for $SLUG; aborting"
  exit 4
fi

START_EPOCH=$(date +%s)
write_status "running" "{\"mode\":\"$MODE\"}"
log "=== sweep start slug=$SLUG dir=$DIR mode=$MODE max_wall=${MAX_WALL}s max_tasks=$MAX_TASKS ==="

DONE=0; FAILED=0; BLOCKED=0; TIMEOUT=0
tick_remaining() { echo $(( MAX_WALL - ( $(date +%s) - START_EPOCH ) )); }

n=0
while (( n < MAX_TASKS )); do
  REMAIN=$(tick_remaining)
  if (( REMAIN < RALPH_MAX_WALL + 30 )); then
    log "budget remaining ${REMAIN}s < per-task ${RALPH_MAX_WALL}s — stopping"
    break
  fi

  NEXT=$(python3 "$PTASKS" next-open "$DIR" 2>>"$LOG")
  TASK_ID=$(echo "$NEXT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  TASK_TEXT=$(echo "$NEXT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('text',''))" 2>/dev/null)
  if [[ -z "$TASK_ID" ]]; then
    log "no open tasks; queue drained"
    break
  fi
  n=$((n+1))

  # Mark doing so a concurrent sweep/operator sees it in flight.
  python3 "$PTASKS" set-status "$DIR" "$TASK_ID" doing >/dev/null 2>>"$LOG"
  BASELINE=$(cd "$DIR" && git rev-parse HEAD 2>/dev/null || echo "")
  log "task=$TASK_ID \"$(echo "$TASK_TEXT" | head -c 120 | tr '\n' ' ')\" baseline=$BASELINE"
  write_status "running" "{\"mode\":\"$MODE\",\"task_id\":\"$TASK_ID\",\"done\":$DONE}"

  set +e
  "$RALPH" "$TASK_TEXT" --dir "$DIR" --baseline-rev "$BASELINE" --max-wall "$RALPH_MAX_WALL" >> "$LOG" 2>&1
  RC=$?
  set -e

  case "$RC" in
    0) python3 "$PTASKS" set-status "$DIR" "$TASK_ID" done  >/dev/null 2>>"$LOG"; DONE=$((DONE+1)); log "task=$TASK_ID DONE" ;;
    3) python3 "$PTASKS" set-status "$DIR" "$TASK_ID" blocked >/dev/null 2>>"$LOG"; BLOCKED=$((BLOCKED+1)); log "task=$TASK_ID BLOCKED (classifier)" ;;
    1|2) python3 "$PTASKS" set-status "$DIR" "$TASK_ID" todo >/dev/null 2>>"$LOG"; TIMEOUT=$((TIMEOUT+1)); log "task=$TASK_ID incomplete (rc=$RC) — released to todo" ;;
    *) python3 "$PTASKS" set-status "$DIR" "$TASK_ID" todo >/dev/null 2>>"$LOG"; FAILED=$((FAILED+1)); log "task=$TASK_ID failed (rc=$RC) — released to todo" ;;
  esac
  sleep 1
done

DURATION=$(( $(date +%s) - START_EPOCH ))
log "=== sweep end slug=$SLUG done=$DONE incomplete=$TIMEOUT blocked=$BLOCKED failed=$FAILED (${DURATION}s) ==="
write_status "idle" "{\"mode\":\"$MODE\",\"last\":{\"done\":$DONE,\"incomplete\":$TIMEOUT,\"blocked\":$BLOCKED,\"failed\":$FAILED,\"duration_s\":$DURATION}}"
exit 0
