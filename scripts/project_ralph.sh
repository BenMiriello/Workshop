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
DIR_ARG=""           # --dir: sweep an explicit scope dir (agent scope / any tree)
ASSIGNEE=""          # --assignee agent:<slug>: claim only this delegate's (or unassigned) tasks
MAX_WALL=3000        # 50 min total for a project sweep
MAX_TASKS=20         # cap tasks per sweep
RALPH_MAX_WALL=1200  # per-task wall budget handed to ralph.sh
FORCE=0              # manual operator-triggered run bypasses the global off-switch
JOB_ID=""            # adopt an already-open Job (manual run) instead of creating one

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)       DIR_ARG="$2"; shift 2 ;;
    --assignee)  ASSIGNEE="$2"; shift 2 ;;
    --max-wall)  MAX_WALL="$2"; shift 2 ;;
    --max-tasks) MAX_TASKS="$2"; shift 2 ;;
    --job-id)    JOB_ID="$2"; shift 2 ;;
    --force)     FORCE=1; shift ;;
    *)           SLUG="$1"; shift ;;
  esac
done
ASSIGNEE_FLAG=(); [[ -n "$ASSIGNEE" ]] && ASSIGNEE_FLAG=(--assignee "$ASSIGNEE")

# Job store shim (services.jobs). Run from the repo root so `backend.services`
# is importable regardless of this script's caller cwd. The run-state of a sweep
# is a Job in `running` status; we finish() it on exit. Replaces the old
# per-project .runtime/ralph-run.json.
job_start() {  # $1 = trigger kind ; echoes the new job id (coordinator sweep Job)
  (cd "$WORKSHOP" && python3 -m backend.services.jobs start \
    --project-slug "$SLUG" --agent-id "$ASSIGNEE" --trigger "$1" --status running 2>/dev/null) || true
}
job_start_child() {  # $1=task_id $2=parent_job ; echoes the child Job id
  (cd "$WORKSHOP" && python3 -m backend.services.jobs start \
    --project-slug "$SLUG" --agent-id "$ASSIGNEE" --trigger loop --status running \
    --task-id "$1" --parent-id "$2" 2>/dev/null) || true
}
job_finish() {  # $1=job_id $2=status $3=tasks_done $4=tasks_failed [$5=evidence-json]
  [[ -z "$1" ]] && return 0
  local ev=(); [[ -n "${5:-}" ]] && ev=(--evidence "$5")
  (cd "$WORKSHOP" && python3 -m backend.services.jobs finish \
    --id "$1" --status "$2" --tasks-done "${3:-0}" --tasks-failed "${4:-0}" "${ev[@]}" \
    >/dev/null 2>&1) || true
}
job_finish_child() {  # $1=child_job $2=status $3=exit_code
  [[ -z "$1" ]] && return 0
  (cd "$WORKSHOP" && python3 -m backend.services.jobs finish \
    --id "$1" --status "$2" --exit-code "${3:-0}" >/dev/null 2>&1) || true
}

if [[ -z "$SLUG" && -z "$DIR_ARG" ]]; then
  echo "usage: project_ralph.sh <slug> | --dir <path> [--assignee agent:<slug>] [--max-wall SEC] [--max-tasks N]" >&2
  exit 4
fi

# Subscription auth; defend against accidental API-key auth.
if [[ -r "$AUTH_ENV" ]]; then
  set -a; . "$AUTH_ENV"; set +a
fi
unset ANTHROPIC_API_KEY

# A --dir sweep targets an explicit scope tree (agent scope, or any tree) and
# trusts the caller's decision to run it (mode gate does not apply). Otherwise
# resolve the project dir + autonomy mode from the registry.
if [[ -n "$DIR_ARG" ]]; then
  DIR="$DIR_ARG"; MODE="on"
else
read -r DIR MODE < <(python3 - "$SLUG" <<'PYEOF'
import json, sys
from pathlib import Path
slug = sys.argv[1]
import os
reg = Path(os.environ.get("WORKSHOP_PROJECTS_REGISTRY",
                          "/home/clawd-a/workshop/.runtime/projects.json"))
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
fi

if [[ -z "$DIR" || ! -d "$DIR" ]]; then
  echo "[project_ralph] project not found or no dir: ${SLUG:-$DIR_ARG}" >&2
  exit 2
fi

RUNTIME="$DIR/.runtime"
mkdir -p "$RUNTIME"
LOG="$RUNTIME/ralph.log"

ts() { date -Iseconds; }
log() { echo "[$(ts)] [project_ralph] $*" >> "$LOG"; echo "[project_ralph] $*"; }

# Global off-switch (same flag the workshop cron honors). A scheduled/continuous
# sweep obeys it; a manual operator-triggered run (--force) is a deliberate
# foreground action and bypasses it, along with the per-project mode gate below.
if [[ -f "$AUTONOMY_FLAG" && "$FORCE" -ne 1 ]]; then
  log "global autonomy off-switch present; not running"
  job_finish "$JOB_ID" cancelled 0 0
  exit 2
fi

# Per-project mode gate. A background (non-force) sweep only runs for a project
# whose mode is not "off". A manual --force run is always allowed (an operator
# "run now" is a deliberate action; "off = manual only").
if [[ "$MODE" == "off" && "$FORCE" -ne 1 ]]; then
  log "project autonomy is off for $SLUG; not running"
  job_finish "$JOB_ID" cancelled 0 0
  exit 2
fi

# One sweep per project at a time.
LOCK=/tmp/workshop-project-ralph-$SLUG.lock
exec 8>"$LOCK"
if ! flock -n 8; then
  log "another sweep already running for $SLUG; aborting"
  job_finish "$JOB_ID" cancelled 0 0
  exit 4
fi

# Adopt the caller's Job (manual run created one already) or open a loop Job for
# this self-launched sweep. This is the run-state the backend reports.
if [[ -z "$JOB_ID" ]]; then
  JOB_ID=$(job_start loop)
fi

START_EPOCH=$(date +%s)
log "=== sweep start slug=$SLUG dir=$DIR mode=$MODE job=$JOB_ID max_wall=${MAX_WALL}s max_tasks=$MAX_TASKS ==="

DONE=0; FAILED=0; BLOCKED=0; TIMEOUT=0
tick_remaining() { echo $(( MAX_WALL - ( $(date +%s) - START_EPOCH ) )); }

n=0
while (( n < MAX_TASKS )); do
  REMAIN=$(tick_remaining)
  if (( REMAIN < RALPH_MAX_WALL + 30 )); then
    log "budget remaining ${REMAIN}s < per-task ${RALPH_MAX_WALL}s — stopping"
    break
  fi

  # Dependency-aware, assignee-scoped next task (reconciles the store first).
  NEXT=$(python3 "$PTASKS" next-ready "$DIR" "${ASSIGNEE_FLAG[@]}" 2>>"$LOG")
  TASK_ID=$(echo "$NEXT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  TASK_TEXT=$(echo "$NEXT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('text',''))" 2>/dev/null)
  if [[ -z "$TASK_ID" ]]; then
    log "no ready tasks"
    break
  fi
  n=$((n+1))

  # Per-task child Job under the coordinator sweep Job (task_id + parent_id link
  # the Jobs feed and the task's history into one graph).
  CHILD_JOB=$(job_start_child "$TASK_ID" "$JOB_ID")
  # Atomic claim: todo->doing recording the child job id. A concurrent sweep or
  # CLI that already claimed it makes this return {}; skip without running.
  CLAIMED=$(python3 "$PTASKS" claim "$DIR" "$TASK_ID" "$CHILD_JOB" "${ASSIGNEE_FLAG[@]}" 2>>"$LOG")
  CLAIMED_ID=$(echo "$CLAIMED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  if [[ -z "$CLAIMED_ID" ]]; then
    log "task=$TASK_ID already claimed elsewhere — skipping"
    job_finish_child "$CHILD_JOB" cancelled 0
    continue
  fi
  BASELINE=$(cd "$DIR" && git rev-parse HEAD 2>/dev/null || echo "")
  log "task=$TASK_ID job=$CHILD_JOB \"$(echo "$TASK_TEXT" | head -c 120 | tr '\n' ' ')\" baseline=$BASELINE"

  set +e
  "$RALPH" "$TASK_TEXT" --dir "$DIR" --baseline-rev "$BASELINE" --max-wall "$RALPH_MAX_WALL" >> "$LOG" 2>&1
  RC=$?
  set -e

  case "$RC" in
    0) python3 "$PTASKS" set-status "$DIR" "$TASK_ID" done >/dev/null 2>>"$LOG"; DONE=$((DONE+1)); job_finish_child "$CHILD_JOB" done "$RC"; log "task=$TASK_ID DONE" ;;
    3) python3 "$PTASKS" block "$DIR" "$TASK_ID" "classifier block" >/dev/null 2>>"$LOG"; BLOCKED=$((BLOCKED+1)); job_finish_child "$CHILD_JOB" failed "$RC"; log "task=$TASK_ID BLOCKED (classifier)" ;;
    1|2) python3 "$PTASKS" set-status "$DIR" "$TASK_ID" todo >/dev/null 2>>"$LOG"; TIMEOUT=$((TIMEOUT+1)); job_finish_child "$CHILD_JOB" done "$RC"; log "task=$TASK_ID incomplete (rc=$RC) — released to todo" ;;
    *) python3 "$PTASKS" set-status "$DIR" "$TASK_ID" todo >/dev/null 2>>"$LOG"; FAILED=$((FAILED+1)); job_finish_child "$CHILD_JOB" failed "$RC"; log "task=$TASK_ID failed (rc=$RC) — released to todo" ;;
  esac
  sleep 1
done

DURATION=$(( $(date +%s) - START_EPOCH ))
# Terminal status of the coordinator sweep. If open (non-terminal) tasks remain
# and NONE are ready (every one is blocked / in_review), the sweep is waiting on
# the operator: finish awaiting_input with evidence. Home's AttentionStrip + Job
# detail surface it; the dispatcher skips an unchanged awaiting scope next tick.
OUT=$(cd "$WORKSHOP" && python3 - "$DIR" "$ASSIGNEE" <<'PYEOF'
import json, sys
from pathlib import Path
sys.path.insert(0, "/home/clawd-a/workshop/backend")
from services import tasks
root = Path(sys.argv[1]); assignee = sys.argv[2] or None
ready = tasks.next_ready(root, assignee=assignee)
open_tasks = tasks.list_tasks(root, include_done=False)
waiting = [t for t in open_tasks if t["status"] in ("blocked", "in_review")]
print(json.dumps({"ready": bool(ready), "open": len(open_tasks), "waiting": len(waiting),
                  "detail": [{"id": t["id"], "status": t["status"],
                              "blocked_reason": t.get("blocked_reason")} for t in waiting][:20]}))
PYEOF
)
READY=$(echo "$OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('ready'))" 2>/dev/null)
OPEN=$(echo "$OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('open',0))" 2>/dev/null)
WAITING=$(echo "$OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('waiting',0))" 2>/dev/null)
log "=== sweep end scope=${SLUG:-$DIR} done=$DONE incomplete=$TIMEOUT blocked=$BLOCKED failed=$FAILED open=${OPEN:-0} waiting=${WAITING:-0} (${DURATION}s) ==="
if [[ "$READY" == "False" && "${OPEN:-0}" -gt 0 && "${OPEN:-0}" == "${WAITING:-0}" ]]; then
  EV=$(echo "$OUT" | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin).get('detail',[])))" 2>/dev/null)
  job_finish "$JOB_ID" awaiting_input "$DONE" "$FAILED" "$EV"
  log "sweep awaiting_input: ${WAITING} task(s) blocked/in_review, nothing ready"
else
  job_finish "$JOB_ID" done "$DONE" "$FAILED"
fi
exit 0
