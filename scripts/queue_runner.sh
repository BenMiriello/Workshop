#!/bin/bash
# queue_runner.sh — the durable worker loop for the workshop task queue.
#
# Before this script the ONLY things that could start a sweep were the 20-minute
# cron tick (which dispatched nothing: no project carries a `loop` trigger, so
# dispatch_triggers.sh planned zero sweeps every tick for weeks) and a manual
# "Run now" from the UI. A filed ticket therefore sat in .tasks.json forever.
# This is the missing piece: a resident loop that drains the queue on a short
# poll interval, so filing a ticket is enough to get it worked.
#
# Contract:
#   - ONE runner per tree (flock). A second invocation exits 0 immediately, so
#     the watchdog can fire it blindly as an "ensure running" call.
#   - Honors the same off-switch as every other autonomous path: the ONE
#     autonomy override, read through scripts/autonomy_gate.sh, plus the
#     pause-until guard. Checked EVERY iteration, so releasing or engaging the
#     brake takes effect within one poll without killing the runner.
#     (It used to test .runtime/AUTONOMY_DISABLED directly. That file is gone;
#     when there were two switches in two formats, the API could report autonomy
#     clear while every tick here skipped.)
#   - Each iteration is a bounded project_ralph.sh sweep. The sweep owns
#     claim/lease/fencing/acceptance/receipt/budget admission; this loop owns
#     only "keep sweeping".
#   - Idle is cheap: with an empty queue the sweep exits in ~1s having done
#     nothing, so the poll costs a process spawn, not a model call.
#
# Usage:
#   scripts/queue_runner.sh [--dir PATH] [--poll SEC] [--max-tasks N] [--once]
#
# Start it (detached, survives logout):
#   setsid nohup bash ~/workshop/scripts/queue_runner.sh >/dev/null 2>&1 &
#
# Deliberately NOT a systemd unit: per repo learnings the backend's move to a
# --user service is the exception, and autonomous workers here are started with
# nohup + a cron watchdog so a wedged runner is recovered without touching
# systemctl. scripts/queue_runner_watchdog.sh is the restart half.

set -u

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# NO DEFAULT: a target must be named (ADR 0014). This used to be DIR="$WORKSHOP",
# so the resident loop swept the platform's own source tree unless told otherwise.
DIR=""
POLL=60
MAX_TASKS=3
ONCE=0

# Per-task wall budget in SECONDS (20 min). We EXPORT this to project_ralph.sh as
# RALPH_MAX_WALL and also size the sweep budget from it, so the two can never
# drift apart — the failure mode being fixed here is exactly a mismatch between
# them. One number, both sides.
PER_TASK_WALL="${PER_TASK_WALL:-1200}"

# SUPERVISED BY DEFAULT, and deliberately so. This loop is the unattended path:
# nobody is watching the run, so the last step is the operator's. A passing task
# lands `in_review` rather than `done`; every gate before that (the reviewer, the
# committed-work check, the acceptance criteria) is unchanged and still has to
# pass. `--unsupervised` is the opt-out, and it is a thing the operator turns on
# after reading a week of in_review results, not a default anyone inherits.
SUPERVISED=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)          DIR="$2"; shift 2 ;;
    --poll)         POLL="$2"; shift 2 ;;
    --max-tasks)    MAX_TASKS="$2"; shift 2 ;;
    --once)         ONCE=1; shift ;;
    --unsupervised) SUPERVISED=0; shift ;;
    *) echo "usage: queue_runner.sh --dir PATH [--poll SEC] [--max-tasks N] [--once] [--unsupervised]" >&2; exit 2 ;;
  esac
done
SUPERVISED_FLAG=(); (( SUPERVISED == 1 )) && SUPERVISED_FLAG=(--supervised)

# Developer-mode gate (ADR 0014): the target must be named, and the platform
# repo is permitted only under WORKSHOP_DEV_MODE=1. Checked before the flock so
# a refused runner never holds a tree's lock.
source "$WORKSHOP/scripts/dev_mode_gate.sh"
assert_run_target "$DIR" "queue_runner.sh" || exit 2

LOG="$WORKSHOP/.runtime/queue-runner.log"
PIDFILE="$WORKSHOP/.runtime/queue-runner.pid"
# One switch, read through services/autonomy_override. Fails closed.
source "$WORKSHOP/scripts/autonomy_gate.sh"
AUTONOMY_OVERRIDE="$WORKSHOP/.runtime/autonomy-override.json"

# Off = the legacy flag file OR the 2026-07-18 JSON overlay engaged. Corrupt
# JSON reads as off (fail-closed per autonomy-model §6).
is_autonomy_off() {
  autonomy_engaged && return 0
  [[ -f "$AUTONOMY_OVERRIDE" ]] || return 1
  if python3 -c "import json,sys; sys.exit(0 if json.load(open('$AUTONOMY_OVERRIDE')).get('engaged') else 1)" 2>/dev/null; then
    return 0
  fi
  if ! python3 -c "import json; json.load(open('$AUTONOMY_OVERRIDE'))" 2>/dev/null; then
    return 0
  fi
  return 1
}
RALPH="$WORKSHOP/scripts/project_ralph.sh"
# Touching this file interrupts the idle sleep and starts the next sweep
# immediately. Tasks enqueued via the API or letta tools write it; the runner
# clears it on wake. The poll is kept as the recovery path for missed signals.
WAKE_FILE="$WORKSHOP/.runtime/queue-runner.wake"

mkdir -p "$WORKSHOP/.runtime"
ts() { date -Iseconds; }
log() { echo "[$(ts)] [queue-runner] $*" >> "$LOG"; }

# Single runner per tree. Keyed on DIR so a per-project runner can coexist with
# the root one. Non-blocking: losing the lock means one is already up.
LOCK="/tmp/workshop-queue-runner-$(printf '%s\n' "$DIR" | md5sum | cut -c1-12).lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(ts)] [queue-runner] already running for $DIR; nothing to do" >> "$LOG"
  exit 0
fi

echo $$ > "$PIDFILE"
# Clear the pidfile on the way out however we exit, so the watchdog never reads a
# stale pid as "healthy".
cleanup() { rm -f "$PIDFILE"; log "runner exiting (pid $$)"; }
trap cleanup EXIT
trap 'log "caught TERM/INT — draining out"; exit 0' TERM INT

# Subscription auth, matching cron_agent.sh. Never an API key.
AUTH_ENV="${WORKSHOP_AUTH_ENV:-$HOME/.config/workshop/auth.env}"
if [[ -r "$AUTH_ENV" ]]; then set -a; . "$AUTH_ENV"; set +a; fi
unset ANTHROPIC_API_KEY

log "=== runner start pid=$$ dir=$DIR poll=${POLL}s max_tasks=$MAX_TASKS ==="

while :; do
  if is_autonomy_off; then
    log "autonomy disabled (flag or override present); idling"
  else
    # pause_guard signals "paused" by calling `exit 0` in the shell it is sourced
    # into, and "not paused" by returning 0 — indistinguishable by exit status
    # from a subshell. So run it in a subshell that echoes a marker AFTER the
    # call: the marker is only reached when the guard returned rather than
    # exited. (Reading the status instead would run the queue while paused.)
    if [[ "$( ( source "$WORKSHOP/scripts/pause_guard.sh"
                workshop_pause_guard "$LOG" "queue_runner"; echo RUN ) )" == *RUN* ]]; then
      # Size the sweep budget to the task cap we are asking for. project_ralph.sh
      # refuses to START a task it cannot finish inside the remaining budget
      # (`REMAIN < RALPH_MAX_WALL + 30`), which is correct — but a budget smaller
      # than MAX_TASKS * per-task means the sweep quits early and MAX_TASKS is
      # never reachable. With a flat 1500s, any task over ~270s ended the sweep
      # after ONE task, so the queue did ~1 real ticket per poll while claiming a
      # cap of 3. Trivial tasks hid it (3 drained in one 97s sweep); real tickets
      # take minutes and did not.
      local_wall=$(( MAX_TASKS * PER_TASK_WALL + 120 ))
      RALPH_MAX_WALL="$PER_TASK_WALL" \
        bash "$RALPH" --dir "$DIR" --max-tasks "$MAX_TASKS" --max-wall "$local_wall" \
             "${SUPERVISED_FLAG[@]}" >> "$LOG" 2>&1 || true
    else
      log "paused by pause_guard; idling"
    fi
  fi
  (( ONCE == 1 )) && break
  # Interruptible idle: check for a wake signal every second, fall through
  # after POLL seconds so we also recover from any missed signals.
  _idle_end=$(( SECONDS + POLL ))
  while (( SECONDS < _idle_end )); do
    sleep 1
    if [[ -f "$WAKE_FILE" ]]; then
      rm -f "$WAKE_FILE"
      log "woken early by enqueue signal"
      break
    fi
  done
done
