#!/bin/bash
# commute_tick.sh — single-poll + LLM-decision tick for the commute service.
#
# Pattern:
#   1. POST /commute/poll        — refresh GTFS-RT state from 511.org
#   2. POST /commute/llm/decide  — relay-routed LLM decides interject Y/N
#      (interject=Y => alerter dispatches to Discord, subject to cooldown)
#
# Designed to be called by cron at multiple cadences:
#   - every 5 minutes during the AM/PM active windows (high resolution)
#   - every 30 minutes outside windows (cheap heartbeat; LLM mostly skips)
#
# All logging to ~/workshop/.runtime/commute/tick.log so the same file
# accumulates a single readable timeline.

set -u

BACKEND=${WORKSHOP_BACKEND:-http://127.0.0.1:8000}
LOG=/home/clawd-a/workshop/.runtime/commute/tick.log
LOCK=/tmp/workshop-commute-tick.lock
CHECKPOINT="${1:-during}"  # pre-1h | pre-30m | during | post (default: during)

mkdir -p "$(dirname "$LOG")"

# Lock so overlapping ticks don't pile up (e.g. if one stalls on Ollama).
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(date -Iseconds)] previous tick still running; skipping" >> "$LOG"
  exit 0
fi

TS=$(date -Iseconds)

# --- Multi-day pause-until guard ------------------------------------------
# If the operator has paused all autonomy until a future PT date, suppress
# this commute tick too (self-clearing on the resume date). This supersedes
# the single-day skip-dates mechanism below. See scripts/pause_guard.sh.
source /home/clawd-a/workshop/scripts/pause_guard.sh
workshop_pause_guard "$LOG" "commute_tick checkpoint=$CHECKPOINT"
# -------------------------------------------------------------------------

# --- One-day suppression (holidays / WFH / not commuting) ----------------
# A skip-dates file lets the operator pause commute updates for specific days
# WITHOUT touching the recurring crontab. Each line is a single YYYY-MM-DD in
# the operator's tz (America/Los_Angeles). If today matches, this tick exits
# silently before any poll / LLM-decide / brief / drain — no Discord traffic.
#
# Self-clearing: the guard only matches the literal date string, so once the
# clock rolls to the next PT day the entry no longer matches and the schedule
# resumes automatically. We also prune any dates strictly in the past on every
# run so the file doesn't accumulate stale entries.
SKIP_FILE=/home/clawd-a/workshop/.runtime/commute/skip-dates.txt
TODAY_PT=$(TZ=America/Los_Angeles date +%F)
if [[ -f "$SKIP_FILE" ]]; then
    # Prune past dates (keep today + future; drop blanks/comments-safe).
    PRUNED=$(awk -v today="$TODAY_PT" 'NF && $1 !~ /^#/ && $1 >= today {print $1}' "$SKIP_FILE" 2>/dev/null)
    printf '%s\n' "$PRUNED" | sed '/^$/d' > "${SKIP_FILE}.tmp.$$" && mv "${SKIP_FILE}.tmp.$$" "$SKIP_FILE"
    if grep -qxF "$TODAY_PT" "$SKIP_FILE" 2>/dev/null; then
        echo "[$TS] tick checkpoint=$CHECKPOINT SUPPRESSED (skip-date $TODAY_PT PT)" >> "$LOG"
        exit 0
    fi
fi
# -------------------------------------------------------------------------

echo "[$TS] tick checkpoint=$CHECKPOINT" >> "$LOG"

# 1) poll fresh data
POLL_RES=$(curl -s -X POST "$BACKEND/commute/poll" --max-time 30 || echo "{\"error\":\"poll http failure\"}")
echo "[$TS] poll: $(echo "$POLL_RES" | head -c 300)" >> "$LOG"

# 2) decide via LLM (auto-routed) — owns late-risk alerting
DECIDE_RES=$(curl -s -X POST "$BACKEND/commute/llm/decide" \
    -H "Content-Type: application/json" \
    -d "{\"checkpoint\":\"$CHECKPOINT\"}" \
    --max-time 180 || echo "{\"error\":\"decide http failure\"}")
echo "[$TS] decide: $(echo "$DECIDE_RES" | head -c 400)" >> "$LOG"

# 2b) proactive brief at the pre-* checkpoints (regardless of late risk).
# Operator request: get a quick "here's where you are" message at the
# 1h-out and 30m-out marks so you know the system is alive + on-target.
# The /commute/brief endpoint cooldowns per (schedule, day, checkpoint),
# so repeated cron firings within the same window won't double-send.
if [[ "$CHECKPOINT" == "pre-1h" || "$CHECKPOINT" == "pre-30m" ]]; then
    BRIEF_RES=$(curl -s -X POST "$BACKEND/commute/brief" \
        -H "Content-Type: application/json" \
        -d "{\"checkpoint\":\"$CHECKPOINT\"}" \
        --max-time 30 || echo "{\"error\":\"brief http failure\"}")
    echo "[$TS] brief: $(echo "$BRIEF_RES" | head -c 300)" >> "$LOG"
fi

# 3) drain notify-commute.txt
# The alerter writes one JSON line per alert to notify-commute.txt when the
# backend env doesn't carry DISCORD_BOT_TOKEN (current state — systemd unit
# edit to import the channels .env is operator-side; we don't fabricate that).
# This drainer sources the channels-bridge .env (mode 0600, owned by us),
# pulls the token from there, and POSTs each queued line to Discord. Atomic:
# moves the queue file aside before draining so any concurrent backend write
# during the drain isn't lost.
NOTIFY_FILE=/home/clawd-a/workshop/.runtime/commute/notify-commute.txt
DISCORD_ENV=/home/clawd-a/.claude/channels/discord/.env
if [[ -s "$NOTIFY_FILE" && -r "$DISCORD_ENV" ]]; then
    set -a
    . "$DISCORD_ENV"
    set +a
    if [[ -n "${DISCORD_BOT_TOKEN:-}" ]]; then
        DRAIN_TMP="${NOTIFY_FILE}.draining.$$"
        mv "$NOTIFY_FILE" "$DRAIN_TMP"
        : > "$NOTIFY_FILE"  # recreate so backend writes here keep working
        DRAINED=0; FAILED=0
        while IFS= read -r LINE; do
            [[ -z "$LINE" ]] && continue
            CH=$(echo "$LINE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('channel_id',''))" 2>/dev/null)
            TEXT=$(echo "$LINE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('text',''))" 2>/dev/null)
            [[ -z "$CH" || -z "$TEXT" ]] && { FAILED=$((FAILED+1)); continue; }
            BODY=$(python3 -c "import json,sys; print(json.dumps({'content': sys.argv[1]}))" "$TEXT")
            HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
                "https://discord.com/api/v10/channels/$CH/messages" \
                -H "Authorization: Bot $DISCORD_BOT_TOKEN" \
                -H "Content-Type: application/json" \
                -d "$BODY" --max-time 10)
            if [[ "$HTTP" =~ ^2 ]]; then
                DRAINED=$((DRAINED+1))
            else
                # Re-queue failed lines at the END of the live file (keeps order
                # of any newly arrived alerts).
                echo "$LINE" >> "$NOTIFY_FILE"
                FAILED=$((FAILED+1))
                echo "[$TS] drain: discord http $HTTP for channel $CH" >> "$LOG"
            fi
        done < "$DRAIN_TMP"
        rm -f "$DRAIN_TMP"
        echo "[$TS] drain: sent=$DRAINED failed=$FAILED" >> "$LOG"
    else
        echo "[$TS] drain: notify-file has alerts but DISCORD_BOT_TOKEN unset in $DISCORD_ENV" >> "$LOG"
    fi
fi

exit 0
