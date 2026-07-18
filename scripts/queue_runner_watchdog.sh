#!/bin/bash
# queue_runner_watchdog.sh — the restart half of the durable worker loop.
#
# queue_runner.sh is a resident process, so it dies with a reboot, an OOM kill,
# or a stray `kill`. This is the supervisor that brings it back: cron fires it on
# a short cadence and it starts the runner if (and only if) one is not already
# holding the tree's lock. queue_runner.sh's own flock makes that check race-free,
# so this stays a blind, idempotent "ensure running" call.
#
# Deliberately cron + nohup rather than a systemd unit, per repo learnings.
#
# Install (already in crontab):
#   */5 * * * * /home/clawd-a/workshop/scripts/queue_runner_watchdog.sh

set -u

WORKSHOP=/home/clawd-a/workshop
LOG="$WORKSHOP/.runtime/queue-runner.log"
DIR="${1:-$WORKSHOP}"

ts() { date -Iseconds; }

LOCK="/tmp/workshop-queue-runner-$(printf '%s\n' "$DIR" | md5sum | cut -c1-12).lock"

# Probe the lock without blocking. Held => a runner is alive; nothing to do.
if exec 9>"$LOCK" && flock -n 9; then
  flock -u 9
  exec 9>&-
  echo "[$(ts)] [watchdog] no runner for $DIR — starting one" >> "$LOG"
  setsid nohup bash "$WORKSHOP/scripts/queue_runner.sh" --dir "$DIR" >/dev/null 2>&1 &
else
  exec 9>&- 2>/dev/null || true
fi
exit 0
