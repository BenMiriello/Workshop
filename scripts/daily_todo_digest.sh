#!/bin/bash
# Daily TODO digest (task 92) — every morning summarizes TODO state and
# writes to notify-masini.txt so masini surfaces it on next inbound.
# Decoupled from cron_agent.sh per spec.
#
# Install: `0 14 * * * /home/clawd-a/workshop/scripts/daily_todo_digest.sh`
# (14:00 UTC = 7:00 PDT in summer; adjust per DST if needed)
set -u

WORKSHOP=/home/clawd-a/workshop
TODO=$WORKSHOP/TODO.md
NOTIFY=$WORKSHOP/.runtime/notify-masini.txt
STATE=$WORKSHOP/.runtime/digest-state.json
TS=$(date -Iseconds)

mkdir -p "$WORKSHOP/.runtime"

# Counts
UNBLOCKED=$(grep -c '^\[ \]' "$TODO" 2>/dev/null || echo 0)
INPROG=$(grep -c '^\[~\]' "$TODO" 2>/dev/null || echo 0)
TOTAL_DONE=$(grep -c '^\[x\]' "$TODO" 2>/dev/null || echo 0)

# Done since last digest — read previous count from state file
PREV_DONE=0
if [[ -f "$STATE" ]]; then
  PREV_DONE=$(python3 -c "import json,sys; print(json.load(open('$STATE')).get('total_done', 0))" 2>/dev/null || echo 0)
fi
DELTA_DONE=$(( TOTAL_DONE - PREV_DONE ))

# Persist state for next digest
echo "{\"total_done\": $TOTAL_DONE, \"last_digest_ts\": \"$TS\"}" > "$STATE"

# Compose digest line
DIGEST="[$TS] DAILY-DIGEST unblocked=$UNBLOCKED in-progress=$INPROG done-total=$TOTAL_DONE done-since-last=+$DELTA_DONE"
echo "$DIGEST" >> "$NOTIFY"

# Also write a standalone status block (overwrites)
{
  echo "Daily Workshop digest — $TS"
  echo ""
  echo "  Unblocked  [ ] : $UNBLOCKED"
  echo "  In-progress [~]: $INPROG"
  echo "  Done       [x] : $TOTAL_DONE  (+$DELTA_DONE since last digest)"
  echo ""
  echo "Next 3 unblocked items:"
  grep '^\[ \]' "$TODO" 2>/dev/null | head -3 | sed 's/^/  /'
} > "$WORKSHOP/.runtime/last-digest.txt"
