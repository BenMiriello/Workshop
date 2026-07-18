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
#   - Honors the same off-switches as every other autonomous path: the global
#     .runtime/AUTONOMY_DISABLED flag and the pause-until guard. Checked EVERY
#     iteration, so flipping the flag stops work within one poll without killing
#     the runner.
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

WORKSHOP=/home/clawd-a/workshop
DIR="$WORKSHOP"
POLL=60
MAX_TASKS=3
ONCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)       DIR="$2"; shift 2 ;;
    --poll)      POLL="$2"; shift 2 ;;
    --max-tasks) MAX_TASKS="$2"; shift 2 ;;
    --once)      ONCE=1; shift ;;
    *) echo "usage: queue_runner.sh [--dir PATH] [--poll SEC] [--max-tasks N] [--once]" >&2; exit 2 ;;
  esac
done

LOG="$WORKSHOP/.runtime/queue-runner.log"
PIDFILE="$WORKSHOP/.runtime/queue-runner.pid"
AUTONOMY_FLAG="$WORKSHOP/.runtime/AUTONOMY_DISABLED"
RALPH="$WORKSHOP/scripts/project_ralph.sh"

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
AUTH_ENV=/home/clawd-a/.config/clawdbot/auth.env
if [[ -r "$AUTH_ENV" ]]; then set -a; . "$AUTH_ENV"; set +a; fi
unset ANTHROPIC_API_KEY

log "=== runner start pid=$$ dir=$DIR poll=${POLL}s max_tasks=$MAX_TASKS ==="

while :; do
  if [[ -f "$AUTONOMY_FLAG" ]]; then
    log "autonomy disabled (flag present); idling"
  else
    # pause_guard signals "paused" by calling `exit 0` in the shell it is sourced
    # into, and "not paused" by returning 0 — indistinguishable by exit status
    # from a subshell. So run it in a subshell that echoes a marker AFTER the
    # call: the marker is only reached when the guard returned rather than
    # exited. (Reading the status instead would run the queue while paused.)
    if [[ "$( ( source "$WORKSHOP/scripts/pause_guard.sh"
                workshop_pause_guard "$LOG" "queue_runner"; echo RUN ) )" == *RUN* ]]; then
      bash "$RALPH" --dir "$DIR" --max-tasks "$MAX_TASKS" --max-wall 1500 >> "$LOG" 2>&1 || true
    else
      log "paused by pause_guard; idling"
    fi
  fi
  (( ONCE == 1 )) && break
  sleep "$POLL"
done
