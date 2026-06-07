#!/bin/bash
# commute_notes.sh — post-commute pattern-learning note writer.
#
# Run once per weekday at 19:30 PT (after the PM commute window closes).
# Steps:
#   1. Pull today's LLM decisions + alert log from the backend.
#   2. Build a summary prompt.
#   3. POST to /relay/local (Ollama qwen2.5:32b) for a one-paragraph note.
#   4. Write ~/workshop/commute/notes/YYYY-MM-DD.md.
#
# Re-running on the same day overwrites the note (intentional — "refresh").

set -u

BACKEND=${WORKSHOP_BACKEND:-http://127.0.0.1:8000}
NOTES_DIR=/home/clawd-a/workshop/commute/notes
LOG=/home/clawd-a/workshop/.runtime/commute/tick.log
TODAY=$(date +%Y-%m-%d)
OUTFILE="$NOTES_DIR/$TODAY.md"

mkdir -p "$NOTES_DIR"
mkdir -p "$(dirname "$LOG")"

TS=$(date -Iseconds)

# Pause-until guard: skip silently if autonomy is paused until a future PT
# date (self-clearing). See scripts/pause_guard.sh.
source /home/clawd-a/workshop/scripts/pause_guard.sh
workshop_pause_guard "$LOG" "commute_notes"

echo "[$TS] commute-notes: generating $TODAY note" >> "$LOG"

# --- 1. Gather today's data ---
DECISIONS=$(curl -s "$BACKEND/commute/llm/recent?limit=50" --max-time 15 || echo "[]")
ALERTS=$(curl -s "$BACKEND/commute/alerts/recent?limit=20" --max-time 15 || echo "[]")
SCHEDULE=$(curl -s "$BACKEND/commute/schedule" --max-time 10 || echo "{}")
TARGETS=$(curl -s "$BACKEND/commute/targets" --max-time 10 || echo "{}")

# --- 2. Build the prompt ---
PROMPT=$(TODAY="$TODAY" DECISIONS="$DECISIONS" ALERTS="$ALERTS" \
         SCHEDULE="$SCHEDULE" TARGETS="$TARGETS" \
         python3 - <<'PYEOF'
import json, os

today         = os.environ.get("TODAY", "")
decisions_raw = os.environ.get("DECISIONS", "[]")
alerts_raw    = os.environ.get("ALERTS", "[]")
schedule_raw  = os.environ.get("SCHEDULE", "{}")
targets_raw   = os.environ.get("TARGETS", "{}")

def _load(s):
    try:
        return json.loads(s)
    except Exception:
        return {} if s.startswith("{") else []

decisions = _load(decisions_raw)
alerts    = _load(alerts_raw)
schedule  = _load(schedule_raw)
targets   = _load(targets_raw)

today_decisions = [d for d in decisions if isinstance(d, dict) and str(d.get("ts","")).startswith(today)]
today_alerts    = [a for a in alerts    if isinstance(a, dict) and str(a.get("ts","")).startswith(today)]

sched_lines = []
for s in schedule.get("schedules", []):
    if s.get("direction") == "to-work":
        sched_lines.append(f"AM: depart home {s.get('target_depart_home','?')} → arrive work {s.get('target_arrive_work','?')}")
    elif s.get("direction") == "to-home":
        sched_lines.append(f"PM: depart work {s.get('target_depart_work','?')} → arrive home {s.get('target_arrive_home','?')}")

flex = targets.get("policy", {}).get("flex_minutes_late", 15)

lines = [
    f"You are writing {today}'s commute log entry for benmiriello's personal notes.",
    "Write ONE concise paragraph (3-5 sentences) summarising how today's commute went.",
    "Cover: on-time vs delayed, what the monitoring system flagged, any patterns worth remembering.",
    "Be factual. Plain prose only, no bullet points. Do not begin with 'I'.",
    "",
    "Planned schedule:",
]
lines += sched_lines or ["(no schedule active)"]
lines += [f"Flex window: {flex} min late before alert.", ""]

if today_decisions:
    lines.append(f"Monitoring checkpoints today ({len(today_decisions)}):")
    for d in today_decisions[-10:]:
        tag = "ALERT" if d.get("interject") else "ok"
        lines.append(f"  [{d.get('checkpoint','?')}] {tag} late_risk={d.get('late_risk')} — {str(d.get('reasoning',''))[:120]}")
    lines.append("")
else:
    lines.append("No monitoring checkpoints recorded today (schedule inactive or data unavailable).")
    lines.append("")

if today_alerts:
    lines.append(f"Alerts sent today ({len(today_alerts)}):")
    for a in today_alerts:
        lines.append(f"  {a.get('ts','?')}: {str(a.get('text',''))[:120]}")
    lines.append("")
else:
    lines.append("No alerts dispatched today.")
    lines.append("")

lines.append("Write the summary paragraph now.")
print("\n".join(lines))
PYEOF
)

if [[ -z "${PROMPT:-}" ]]; then
    echo "[$TS] commute-notes: prompt build failed; aborting" >> "$LOG"
    exit 1
fi

echo "[$TS] commute-notes: prompt built (${#PROMPT} chars)" >> "$LOG"

# --- 3. Call /relay/local (Ollama qwen2.5:32b) ---
RELAY_BODY=$(python3 -c "import json,sys; print(json.dumps({'task': sys.stdin.read(), 'max_tokens': 512}))" <<< "$PROMPT")

RELAY_RES=$(curl -s -X POST "$BACKEND/relay/local" \
    -H "Content-Type: application/json" \
    -d "$RELAY_BODY" \
    --max-time 180 || echo '{"error":"relay timeout"}')

SUMMARY=$(python3 -c "
import json, sys
raw = sys.stdin.read()
try:
    d = json.loads(raw)
    txt = d.get('response') or d.get('error') or raw[:400]
except Exception:
    txt = raw[:400]
print(txt.strip())
" <<< "$RELAY_RES")

echo "[$TS] commute-notes: summary (${#SUMMARY} chars)" >> "$LOG"

# --- 4. Write the note ---
{
    printf "# Commute note — %s\n\n" "$TODAY"
    printf "%s\n\n" "$SUMMARY"
    printf -- "---\n_Generated %s by commute_notes.sh_\n" "$TS"
} > "$OUTFILE"

echo "[$TS] commute-notes: wrote $OUTFILE" >> "$LOG"
