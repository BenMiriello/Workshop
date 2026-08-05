#!/bin/bash
# Workshop autonomous build cron agent — REVISED for true autonomy.
#
# What changed vs the v1 cron:
#   - Ralph-style re-fire loop: each picked task is driven to done via
#     scripts/ralph.sh, which keeps iterating until reviewer.sh approves or
#     iteration / wall-time caps hit. No more "one task per tick" limit.
#   - Within-tick iteration: when ralph finishes a task, if budget remains
#     and the queue isn't empty, pick the next task and ralph again.
#   - Mandatory completion gate: reviewer.sh runs before any [x] flips. No
#     more "marked done with caveats" failure mode.
#   - Pre-flight: graduate_proposals + work_verifier + work_generator run at
#     the top of every tick. The queue is fueled before the worker fires.
#   - Curriculum fallback: if queue is still empty after the deterministic
#     generators, the curriculum_tick (cron'd separately) will eventually
#     refill it. This script does NOT call curriculum directly — that's a
#     different cron entry on its own cadence to avoid burning a worker tick
#     on planning.
#
# Layer-3 contract preserved:
#   - Each invocation reads ~/workshop/TODO.md fresh.
#   - Each invocation drains up to 5 pending-queue events first (Layer 4).
#   - Output goes to ~/workshop/cron_agent.log; status to ~/workshop/status.md.
#   - notify-masini.txt is dropped per substantive change so masini surfaces
#     to Discord on next inbound.

set -u

WORKSHOP=/home/clawd-a/workshop
TODO=$WORKSHOP/TODO.md
LOG=$WORKSHOP/cron_agent.log
LOCK=/tmp/workshop-cron-agent.lock
CLAUDE=/home/clawd-a/.nvm/versions/node/v22.22.3/bin/claude
AUTH_ENV=/home/clawd-a/.config/clawdbot/auth.env

# Total wall-time budget for this tick (event-consumer + worker loop). Cron
# fires every 20min, so leave a 5-min safety margin to make sure we exit before
# the next tick attempts to start.
TICK_BUDGET=1500

# Per-task ralph budget. Tasks longer than this get re-picked on the next tick.
RALPH_MAX_WALL=1200

# Sub auth env (subscription token; defense against accidental API-key auth)
if [[ -r "$AUTH_ENV" ]]; then
  set -a; . "$AUTH_ENV"; set +a
fi
unset ANTHROPIC_API_KEY

# Job store shim (services.jobs). Every bounded execution this tick drives — a
# ralph worker task, a drained event — is recorded as a Job in .runtime/jobs.jsonl
# (replacing the old agent-runs.jsonl aggregate + agent-runs-completed.jsonl).
# Run from the repo root so `backend.services` imports regardless of cwd.
jobs_py() { (cd "$WORKSHOP" && python3 -m backend.services.jobs "$@"); }
job_start() {  # $1=trigger  $2=agent_id(optional) ; echoes new job id
  local a=(start --trigger "$1" --status running)
  [[ -n "${2:-}" ]] && a+=(--agent-id "$2")
  jobs_py "${a[@]}" 2>/dev/null || true
}
job_finish() {  # $1=id $2=status $3=exit_code(optional) $4=tasks_done(optional) $5=tasks_failed(optional)
  [[ -z "${1:-}" ]] && return 0
  local a=(finish --id "$1" --status "$2")
  [[ -n "${3:-}" ]] && a+=(--exit-code "$3")
  [[ -n "${4:-}" ]] && a+=(--tasks-done "$4")
  [[ -n "${5:-}" ]] && a+=(--tasks-failed "$5")
  jobs_py "${a[@]}" >/dev/null 2>&1 || true
}

# Lock — prevent overlapping ticks.
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(date -Iseconds)] previous invocation still running; skipping" >> "$LOG"
  exit 0
fi

cd "$WORKSHOP" || { echo "[$(date -Iseconds)] cannot cd to $WORKSHOP" >> "$LOG"; exit 1; }

# Pause-until guard: if the operator has paused autonomy until a future PT
# date, exit silently (self-clearing on the resume date). See pause_guard.sh.
source /home/clawd-a/workshop/scripts/pause_guard.sh
workshop_pause_guard "$LOG" "cron_agent"

# Operator off-switch. Touched by the /agents/autonomy/disable endpoint or by
# hand. While present, the cron worker exits early without doing any work,
# without touching crontab or stopping the backend.
# ONE switch, read through services/autonomy_override (see scripts/autonomy_gate.sh).
# This used to test .runtime/AUTONOMY_DISABLED directly, which meant the loop and
# the API could disagree about whether autonomy was on. Fails closed.
source "$WORKSHOP/scripts/autonomy_gate.sh"
if autonomy_engaged; then
  echo "[$(date -Iseconds)] autonomy engaged ($(autonomy_reason)); skipping tick" >> "$LOG"
  exit 0
fi

TICK_START_EPOCH=$(date +%s)
TICK_START_ISO=$(date -Iseconds)

# Helper: seconds remaining in tick budget.
tick_remaining() { echo $(( TICK_BUDGET - ( $(date +%s) - TICK_START_EPOCH ) )); }

echo "[$TICK_START_ISO] === cron tick start (budget=${TICK_BUDGET}s) ===" >> "$LOG"

# =============================================================================
# Phase 0 — RETIRED (2026-07-15 root migration, Stage 3 / O7).
# =============================================================================
# The legacy pre-flight (graduate_proposals / work_verifier / work_generator)
# manipulated the root TODO.md text and fed the Phase 2 grep/sed worker-loop —
# the "old, oddly-conceived" system that produced the autogen/caveat spam. Both
# are gone: the root `self` scope is now the structured task store (.tasks.json),
# worked through the SAME dispatcher path as every other project (Phase 0.5).
# There is one task system and one loop now. The old prose TODO.md is archived at
# docs/archive/root-todo-legacy-2026-07-15.md.

# =============================================================================
# Phase 0.5 — Dispatcher (orchestration O3): launch background sweeps for armed
# project/agent loop + cron triggers. It decides + spawns detached sweeps and
# returns fast (never blocks this tick); it re-checks AUTONOMY_DISABLED itself.
# This is what makes a project's `loop` trigger actually run unattended.
# =============================================================================
bash "$WORKSHOP/scripts/dispatch_triggers.sh" >> "$LOG" 2>&1 || true

# =============================================================================
# Phase 1 — Event-consumer (Layer 4, unchanged behavior)
# =============================================================================
# Drain up to MAX_PENDING pending events. Same logic as v1: re-read events.jsonl
# at offset, classify, dispatch local or cloud. Each drained event is now a Job
# (trigger=event) rather than an agent-runs-completed.jsonl append. The pending
# QUEUE itself (agent-runs-pending.jsonl) is untouched: it is consumed here, not
# a ledger.
PENDING=$WORKSHOP/.runtime/agent-runs-pending.jsonl
EVENTS_LOG=$WORKSHOP/.runtime/events.jsonl
MAX_PENDING=5

if [[ -s "$PENDING" ]]; then
  TICK_TS=$(date -Iseconds)
  CONSUMED_COUNT=0
  TOTAL_PENDING=$(wc -l < "$PENDING")
  echo "[$TICK_TS] event-consumer: $TOTAL_PENDING pending records, draining up to $MAX_PENDING" >> "$LOG"

  WORKING=$PENDING.working.$$
  mv "$PENDING" "$WORKING"
  touch "$PENDING"

  while IFS= read -r LINE; do
    if [[ -z "$LINE" ]]; then continue; fi
    if [[ $CONSUMED_COUNT -ge $MAX_PENDING ]]; then
      echo "$LINE" >> "$PENDING"
      continue
    fi
    AGENT_ID=$(echo "$LINE" | python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('agent_id',''))" 2>/dev/null)
    SOURCE=$(echo "$LINE" | python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('source',''))" 2>/dev/null)
    EVT_OFFSET=$(echo "$LINE" | python3 -c "import json,sys; r=json.loads(sys.stdin.read()); er=r.get('event_ref') or {}; print(er.get('offset') if er.get('offset') is not None else '')" 2>/dev/null)
    if [[ -z "$AGENT_ID" || -z "$SOURCE" ]]; then
      echo "[$(date -Iseconds)] event-consumer: skipping malformed pending record" >> "$LOG"
      continue
    fi

    # Per-agent budget check.
    BUDGET_JSON=$(curl -sS -m 5 "http://127.0.0.1:8000/agents/$AGENT_ID/budget" 2>/dev/null)
    BUDGET_OVER=$(echo "$BUDGET_JSON" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('over', False)))" 2>/dev/null || echo 0)
    if [[ "$BUDGET_OVER" == "1" ]]; then
      echo "[$(date -Iseconds)] event-consumer: agent=$AGENT_ID over budget — throttling" >> "$LOG"
      THROTTLE_JOB=$(job_start event "$AGENT_ID")
      job_finish "$THROTTLE_JOB" cancelled
      continue
    fi

    # Re-read the event from events.jsonl.
    EVENT_JSON=""
    if [[ -n "$EVT_OFFSET" && "$EVT_OFFSET" != "None" && -r "$EVENTS_LOG" ]]; then
      EVENT_JSON=$(python3 -c "
import sys, json
try:
    off = int(sys.argv[1])
    with open(sys.argv[2], 'rb') as f:
        f.seek(off)
        line = f.readline().decode('utf-8', errors='replace').strip()
    obj = json.loads(line)
    print(json.dumps(obj, separators=(',', ':')))
except Exception:
    print('')
" "$EVT_OFFSET" "$EVENTS_LOG" 2>/dev/null)
    fi
    [[ -z "$EVENT_JSON" ]] && EVENT_JSON='{"_note":"event payload unavailable"}'

    EVENT_PROMPT="Webhook event for $AGENT_ID from $SOURCE: $EVENT_JSON. Summarize in one line; append to ~/workshop/.runtime/notify-masini.txt only if it crosses a rule in shared/global-memory.md (default skip). Exit."

    # Low-value-code shortcut.
    LOW_VALUE_CODES="SYNC_UPDATES_AVAILABLE HISTORICAL_UPDATE INITIAL_UPDATE DEFAULT_UPDATE WEBHOOK_UPDATE_ACKNOWLEDGED"
    EVT_WEBHOOK_CODE=$(echo "$EVENT_JSON" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print((d.get('body') or {}).get('webhook_code') or d.get('webhook_code') or '')" 2>/dev/null)
    FORCE_LOCAL=0
    if [[ -n "$EVT_WEBHOOK_CODE" ]]; then
      for code in $LOW_VALUE_CODES; do
        if [[ "$code" == "$EVT_WEBHOOK_CODE" ]]; then FORCE_LOCAL=1; break; fi
      done
    fi

    if [[ $FORCE_LOCAL -eq 1 ]]; then
      EVT_ROUTE="local"
    else
      EVT_CLASSIFY_RESP=$(curl -sS -m 5 -X POST http://127.0.0.1:8000/relay/classify \
        -H 'Content-Type: application/json' \
        -d "$(python3 -c "import json,sys; print(json.dumps({'task': sys.argv[1]}))" "$EVENT_PROMPT")" 2>/dev/null)
      EVT_ROUTE=$(echo "$EVT_CLASSIFY_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('route','cloud'))" 2>/dev/null || echo "cloud")
    fi

    # Open the event Job (run-state) before dispatching.
    EVT_JOB=$(job_start event "$AGENT_ID")
    AGENT_RUN_TS=$(date -Iseconds)
    if [[ "$EVT_ROUTE" == "local" ]]; then
      LOCAL_RESP=$(curl -sS -m 300 -X POST http://127.0.0.1:8000/relay/local \
        -H 'Content-Type: application/json' \
        -d "$(python3 -c "import json,sys; print(json.dumps({'task': sys.argv[1], 'max_tokens': 1024}))" "$EVENT_PROMPT")" 2>/dev/null)
      EVT_EXIT=$?
      echo "$LOCAL_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('response','[no response]'))" 2>/dev/null >> "$LOG" || true
    else
      # No --max-turns: the 300s timeout is the bound. A step cap here cut
      # handlers off mid-task for no benefit the timeout doesn't already give.
      timeout 300 "$CLAUDE" -p "$EVENT_PROMPT" --output-format text >> "$LOG" 2>&1
      EVT_EXIT=$?
    fi
    AGENT_RUN_END=$(date -Iseconds)

    [[ -x "$WORKSHOP/scripts/heartbeat.sh" ]] && "$WORKSHOP/scripts/heartbeat.sh" "$AGENT_ID" mode=event-driven source="$SOURCE" >/dev/null 2>&1 || true

    if [[ $EVT_EXIT -eq 0 ]]; then EVT_JOB_STATUS="done";
    elif [[ $EVT_EXIT -eq 124 ]]; then EVT_JOB_STATUS="failed";
    else EVT_JOB_STATUS="failed"; fi
    job_finish "$EVT_JOB" "$EVT_JOB_STATUS" "$EVT_EXIT"
    CONSUMED_COUNT=$((CONSUMED_COUNT + 1))
  done < "$WORKING"

  rm -f "$WORKING"
  echo "[$(date -Iseconds)] event-consumer: consumed=$CONSUMED_COUNT remaining=$(wc -l < "$PENDING")" >> "$LOG"
fi

# =============================================================================
# Phase 2 — RETIRED (2026-07-15 root migration). The Ralph-style grep/sed
# worker-loop that picked `^\[ \]` lines out of the root TODO.md and drove
# ralph.sh against them is gone. Root work now flows through the SAME path as
# every project: a task in the root `.tasks.json`, dispatched by Phase 0.5
# (dispatch_triggers.sh -> project_ralph.sh) under the usage budget, with the
# reviewer acceptance gate and a proper Job graph. One task system, one loop.
# =============================================================================

# =============================================================================
# Phase 3 — Tick end
# =============================================================================
# Per-task/-event accounting is now each Job in .runtime/jobs.jsonl (the activity
# source of record); the tick just logs that it ran. Sweeps launched by the
# dispatcher run detached and report themselves.
TICK_END_ISO=$(date -Iseconds)
DURATION=$(( $(date +%s) - TICK_START_EPOCH ))
echo "[$TICK_END_ISO] === cron tick end (${DURATION}s) ===" >> "$LOG"

exit 0
