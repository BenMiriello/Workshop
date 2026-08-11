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
# The tree to supervise must be NAMED (ADR 0014). This used to default to
# $WORKSHOP, which made the always-on supervisor a standing autonomous writer on
# the platform's own source — door 9 of the survey in
# docs/platform-redesign/dev-mode-and-development-scope-2026-08-09.md §2.
#
# Install (crontab, currently commented out — re-enable with an explicit tree):
#   */5 * * * * <workshop>/scripts/queue_runner_watchdog.sh /path/to/tree

set -u

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG="$WORKSHOP/.runtime/queue-runner.log"
DIR="${1:-}"

ts() { date -Iseconds; }

# Deployment config, WORKSHOP_DEV_MODE among it. Cron has no systemd
# EnvironmentFile, so without this the flag the backend reads as ON is unset
# here and the gate below refuses a tree the operator has explicitly permitted.
# THE TWO HALVES MUST AGREE: every cron entrypoint that can reach a run target
# loads the same file the units do. Existing environment wins.
source "$WORKSHOP/scripts/lib/instance.sh"

# Same gate the runner applies, checked here too so cron logs the refusal once
# rather than spawning a runner every five minutes that exits immediately.
source "$WORKSHOP/scripts/dev_mode_gate.sh"
if ! assert_run_target "$DIR" "queue_runner_watchdog.sh" 2>>"$LOG"; then
  exit 2
fi

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
