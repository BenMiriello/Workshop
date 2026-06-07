#!/bin/bash
# pause_guard.sh — shared "pause autonomous work until a date" guard.
#
# Purpose
# -------
# The operator can pause ALL scheduled/autonomous Workshop activity until a
# given date WITHOUT editing the crontab. Every autonomous cron entry point
# sources this file near the top; if a pause is in effect, the helper calls
# `exit 0` from within the sourcing script so the tick ends silently — no
# Discord traffic, no LLM calls, no TODO.md mutations — while leaving the
# recurring crontab schedule fully intact.
#
# Mechanism
# ---------
# A single date file, ~/workshop/.runtime/pause-until, holds one line:
#   YYYY-MM-DD   (the first PT date on which autonomy RESUMES)
# Interpretation: pause is active for every PT day strictly BEFORE that date.
# On the pause-until date itself (and after), autonomy runs normally.
#
# Self-clearing: once the PT clock reaches >= the pause-until date, the guard
# stops matching AND removes the now-stale file, so no manual re-enable is
# needed. Comparison is done in America/Los_Angeles tz (the operator's tz),
# independent of the system tz, using lexical string comparison on the
# ISO YYYY-MM-DD form (valid because zero-padded ISO dates sort lexically).
#
# Usage (in a cron entry-point script):
#   source /home/clawd-a/workshop/scripts/pause_guard.sh
#   workshop_pause_guard            # exits 0 (silently) if paused
# or with a per-script log line:
#   workshop_pause_guard "$LOG" "cron_agent"
#
# This file is intentionally side-effect-free until workshop_pause_guard is
# called, so it is safe to `source` unconditionally.

WORKSHOP_PAUSE_FILE="${WORKSHOP_PAUSE_FILE:-/home/clawd-a/workshop/.runtime/pause-until}"

# workshop_pause_guard [logfile] [label]
#   logfile (optional): append a one-line note when suppressing
#   label   (optional): identifier for the calling script in that note
# Exits 0 if paused (autonomy should not run today). Returns 0 otherwise.
workshop_pause_guard() {
    local logfile="${1:-}"
    local label="${2:-tick}"

    [[ -f "$WORKSHOP_PAUSE_FILE" ]] || return 0

    local today_pt resume_date
    today_pt=$(TZ=America/Los_Angeles date +%F)
    # First non-blank, non-comment line; trim whitespace.
    resume_date=$(awk 'NF && $1 !~ /^#/ {print $1; exit}' "$WORKSHOP_PAUSE_FILE" 2>/dev/null)

    # Malformed / empty file: treat as no pause, prune it so it can't wedge.
    if [[ ! "$resume_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        rm -f "$WORKSHOP_PAUSE_FILE" 2>/dev/null
        return 0
    fi

    # Self-clear: clock has reached/passed the resume date -> remove file, run.
    if [[ "$today_pt" > "$resume_date" || "$today_pt" == "$resume_date" ]]; then
        rm -f "$WORKSHOP_PAUSE_FILE" 2>/dev/null
        return 0
    fi

    # Pause is active (today_pt < resume_date): suppress this tick.
    if [[ -n "$logfile" ]]; then
        local ts
        ts=$(date -Iseconds)
        echo "[$ts] $label SUPPRESSED (autonomy paused until $resume_date PT; today=$today_pt PT)" >> "$logfile" 2>/dev/null
    fi
    exit 0
}
