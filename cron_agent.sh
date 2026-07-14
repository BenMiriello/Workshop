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
AUTONOMY_FLAG=$WORKSHOP/.runtime/AUTONOMY_DISABLED
if [[ -f "$AUTONOMY_FLAG" ]]; then
  echo "[$(date -Iseconds)] autonomy disabled (flag: $AUTONOMY_FLAG); skipping tick" >> "$LOG"
  exit 0
fi

TICK_START_EPOCH=$(date +%s)
TICK_START_ISO=$(date -Iseconds)

# Helper: seconds remaining in tick budget.
tick_remaining() { echo $(( TICK_BUDGET - ( $(date +%s) - TICK_START_EPOCH ) )); }

echo "[$TICK_START_ISO] === cron tick start (budget=${TICK_BUDGET}s) ===" >> "$LOG"

# =============================================================================
# Phase 0 — Pre-flight (deterministic, fast)
# =============================================================================
# graduate_proposals → flip [proposed] >6h old to [ ].
# work_verifier      → demote false [x] to [ ].
# work_generator     → scan for stubs + caveats + missing files, append [ ].
# Each emits a single integer count on stdout (for the log).

PRE_GRAD=$("$WORKSHOP/scripts/graduate_proposals.sh" 2>/dev/null | tail -1)
PRE_GRAD=${PRE_GRAD:-0}
PRE_VERIFY=$("$WORKSHOP/scripts/work_verifier.sh" 2>/dev/null | tail -1)
PRE_VERIFY=${PRE_VERIFY:-0}
PRE_GEN=$("$WORKSHOP/scripts/work_generator.sh" 2>/dev/null | tail -1)
PRE_GEN=${PRE_GEN:-0}
echo "[$(date -Iseconds)] pre-flight: graduated=$PRE_GRAD demoted=$PRE_VERIFY generated=$PRE_GEN" >> "$LOG"

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
      timeout 300 "$CLAUDE" -p "$EVENT_PROMPT" --output-format text --max-turns 4 >> "$LOG" 2>&1
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
# Phase 2 — Worker loop (Ralph-style, within-tick iteration)
# =============================================================================
# Pick the FIRST unblocked `[ ]` task. Fire ralph.sh to drive it to done. When
# ralph returns 0 (PASS), loop to pick the next task — but only if enough budget
# remains for another full ralph cycle (RALPH_MAX_WALL + 30s overhead).

TASKS_DONE=0
TASKS_FAILED=0
TASKS_TIMEOUT=0
TASKS_BLOCKED=0

while true; do
  REMAIN=$(tick_remaining)
  if (( REMAIN < RALPH_MAX_WALL + 30 )); then
    echo "[$(date -Iseconds)] worker-loop: budget remaining ${REMAIN}s < ralph wall ${RALPH_MAX_WALL}s — stopping" >> "$LOG"
    break
  fi

  # Pick the first [ ] task. Capture the line number for surgical updates.
  TASK_LINE_NO=$(grep -n '^\[ \]' "$TODO" 2>/dev/null | head -1 | cut -d: -f1)
  if [[ -z "$TASK_LINE_NO" ]]; then
    echo "[$(date -Iseconds)] worker-loop: no unblocked [ ] tasks; idle" >> "$LOG"
    break
  fi
  TASK_LINE=$(sed -n "${TASK_LINE_NO}p" "$TODO")
  TASK_TEXT=${TASK_LINE#[ ] }
  TASK_TRIM=$(echo "$TASK_TEXT" | head -c 200 | tr '\n' ' ')

  # Mark in-progress so concurrent ticks don't grab the same task.
  sed -i "${TASK_LINE_NO}s/^\[ \]/[~]/" "$TODO"

  # Capture baseline rev so ralph's reviewer compares against PRE-task state.
  BASELINE=$(git rev-parse HEAD 2>/dev/null || echo "")

  echo "[$(date -Iseconds)] worker-loop: picking line=$TASK_LINE_NO task=\"$TASK_TRIM\"" >> "$LOG"

  # One Job per dispatched task (trigger=cron: this worker fires on the cron
  # schedule). Run-state is `running`; finished with the ralph outcome below.
  TASK_JOB=$(job_start cron)

  # Fire Ralph against this task.
  set +e
  "$WORKSHOP/scripts/ralph.sh" "$TASK_TEXT" --baseline-rev "$BASELINE" --max-wall "$RALPH_MAX_WALL" >> "$LOG" 2>&1
  RALPH_EXIT=$?
  set -e

  # Outcome → checkbox state. Ralph exit codes:
  #   0 = PASS (sentinel + reviewer ok)
  #   1 = iteration cap hit (incomplete)
  #   2 = wall-time cap hit (incomplete)
  #   3 = classifier block (genuine blocker)
  #   4 = startup failure
  case "$RALPH_EXIT" in
    0)
      # Mark [x]; append log entry.
      sed -i "${TASK_LINE_NO}s/^\[~\]/[x]/" "$TODO"
      LOG_TS=$(date -u +%Y-%m-%dT%H:%MZ)
      printf '[%s] [ralph] %s — completed (ralph passed reviewer)\n' "$LOG_TS" "$(echo "$TASK_TEXT" | head -c 200)" >> "$TODO"
      printf "[%s] %s\n" "$LOG_TS" "$(echo "$TASK_TEXT" | head -c 200)" >> "$WORKSHOP/.runtime/notify-masini.txt"
      TASKS_DONE=$((TASKS_DONE+1))
      ;;
    1|2)
      # Partial progress; leave as [~] with incomplete marker so next tick re-tries.
      LABEL=$([[ "$RALPH_EXIT" == "1" ]] && echo "iter-cap" || echo "wall-cap")
      sed -i "${TASK_LINE_NO}s/^\[~\]/[ ]/" "$TODO"   # release to [ ] so future ticks retry
      LOG_TS=$(date -u +%Y-%m-%dT%H:%MZ)
      printf '[%s] [ralph] %s — incomplete ($LABEL); will retry next tick\n' "$LOG_TS" "$(echo "$TASK_TEXT" | head -c 200)" >> "$TODO"
      TASKS_TIMEOUT=$((TASKS_TIMEOUT+1))
      ;;
    3)
      # Classifier block — mark blocked permanently.
      sed -i "${TASK_LINE_NO}s/^\[~\]/[~]/" "$TODO"
      # Append BLOCKED suffix if not already there.
      if ! sed -n "${TASK_LINE_NO}p" "$TODO" | grep -q 'BLOCKED:'; then
        sed -i "${TASK_LINE_NO}s|.*|& — BLOCKED: classifier (ralph exit 3)|" "$TODO"
      fi
      LOG_TS=$(date -u +%Y-%m-%dT%H:%MZ)
      printf '[%s] [ralph] %s — BLOCKED: classifier\n' "$LOG_TS" "$(echo "$TASK_TEXT" | head -c 200)" >> "$TODO"
      TASKS_BLOCKED=$((TASKS_BLOCKED+1))
      ;;
    *)
      # Startup failure or anything else: revert to [ ] so retried.
      sed -i "${TASK_LINE_NO}s/^\[~\]/[ ]/" "$TODO"
      TASKS_FAILED=$((TASKS_FAILED+1))
      ;;
  esac

  # Close the task Job with the ralph outcome. 0=done, 3=classifier-block
  # (cancelled), 1/2/other=failed (incomplete/failure, carried in tasks_failed).
  case "$RALPH_EXIT" in
    0)   TJ_STATUS=done;      TJ_DONE=1; TJ_FAIL=0 ;;
    3)   TJ_STATUS=cancelled; TJ_DONE=0; TJ_FAIL=0 ;;
    *)   TJ_STATUS=failed;    TJ_DONE=0; TJ_FAIL=1 ;;
  esac
  job_finish "$TASK_JOB" "$TJ_STATUS" "$RALPH_EXIT" "$TJ_DONE" "$TJ_FAIL"

  # Tiny gap so log writes interleave cleanly.
  sleep 1
done

# =============================================================================
# Phase 3 — Final report
# =============================================================================
TICK_END_ISO=$(date -Iseconds)
DURATION=$(( $(date +%s) - TICK_START_EPOCH ))
OPEN_COUNT=$(grep -c '^\[ \]' "$TODO" 2>/dev/null)
OPEN_COUNT=${OPEN_COUNT:-0}

echo "[$TICK_END_ISO] === cron tick end (${DURATION}s) done=$TASKS_DONE incomplete=$TASKS_TIMEOUT blocked=$TASKS_BLOCKED failed=$TASKS_FAILED queue_open=$OPEN_COUNT ===" >> "$LOG"

# Status broadcast: refresh status.md + notify-masini on substantive change.
if (( TASKS_DONE > 0 || TASKS_TIMEOUT > 0 || TASKS_BLOCKED > 0 )); then
  printf "Workshop cron status\nLast tick: %s\nDuration: %ds\nDone: %d  Incomplete: %d  Blocked: %d  Failed: %d  Open: %d\n" \
    "$TICK_END_ISO" "$DURATION" "$TASKS_DONE" "$TASKS_TIMEOUT" "$TASKS_BLOCKED" "$TASKS_FAILED" "$OPEN_COUNT" > "$WORKSHOP/status.md"
  if (( TASKS_DONE > 0 )); then
    printf "[%s] cron-tick: %d task(s) completed, %d open remain\n" "$TICK_END_ISO" "$TASKS_DONE" "$OPEN_COUNT" >> "$WORKSHOP/.runtime/notify-masini.txt"
  fi
fi

# The per-tick aggregate that used to be appended to .runtime/agent-runs.jsonl
# is retired: every dispatched task and drained event is now its own Job in
# .runtime/jobs.jsonl (see job_start/job_finish above), which is the activity
# source of record. No aggregate row is written.

exit 0
