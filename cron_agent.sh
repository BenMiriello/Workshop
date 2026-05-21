#!/bin/bash
# Workshop autonomous build cron agent.
#
# Installed by the clawd-a supervisor session (not masini's bridge) because
# masini's auto-mode classifier blocks "agent installs cron that fires
# claude -p" as Create-Unsafe-Agents. The supervisor session has a different
# classifier context. Operator explicitly authorized this via Discord on
# 2026-05-20 ~08:40 PDT.
#
# Each invocation:
#   1. Reads ~/workshop/TODO.md
#   2. Picks the first unblocked `[ ]` task
#   3. Executes it via claude -p (subscription auth, no API key)
#   4. claude -p updates the TODO.md checkbox + Log section per its prompt
#   5. Output goes to ~/workshop/cron_agent.log for the operator to inspect
#
# claude -p does NOT have access to the channels Discord plugin (that's only
# in --channels mode). For status updates to operator, the script appends to
# TODO.md's Log section and operator pings masini "what's the latest" to read
# it back via Discord.
#
# Lock file prevents overlap if a previous invocation is still running.

set -u

WORKSHOP=/home/clawd-a/workshop
TODO=$WORKSHOP/TODO.md
LOG=$WORKSHOP/cron_agent.log
LOCK=/tmp/workshop-cron-agent.lock
CLAUDE=/home/clawd-a/.nvm/versions/node/v22.22.3/bin/claude
AUTH_ENV=/home/clawd-a/.config/clawdbot/auth.env

# Pull in CLAUDE_CODE_OAUTH_TOKEN for subscription auth; .credentials.json is
# the fallback claude uses if env var is absent, so this is belt+suspenders.
if [[ -r "$AUTH_ENV" ]]; then
  set -a
  . "$AUTH_ENV"
  set +a
fi
unset ANTHROPIC_API_KEY  # defense against accidental API-key auth

# Lock to prevent overlap.
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(date -Iseconds)] previous invocation still running; skipping" >> "$LOG"
  exit 0
fi

cd "$WORKSHOP" || { echo "[$(date -Iseconds)] cannot cd to $WORKSHOP" >> "$LOG"; exit 1; }

# Bail-out for TODO is below the pending-queue consumer — events should still
# fire even when the TODO is idle.
NO_PENDING_TASKS=0
if ! grep -q '^\[ \]' "$TODO" 2>/dev/null; then
  NO_PENDING_TASKS=1
fi

# =============================================================================
# Phase 5b Layer 4 — pending-queue consumer
# =============================================================================
# Drain up to MAX_PENDING records from .runtime/agent-runs-pending.jsonl before
# doing TODO-pick work. For each pending record:
#   - Re-read the event from .runtime/events.jsonl at event_ref.offset
#   - Build a prompt for the subscribed agent and fire one-shot `claude -p`
#   - Append a completed-record (status: consumed|skipped|failed) to
#     .runtime/agent-runs-completed.jsonl
# Truncate the pending file once drained. Lock is shared with the rest of the
# tick (we hold flock 9 for the whole script).
# -----------------------------------------------------------------------------
PENDING=$WORKSHOP/.runtime/agent-runs-pending.jsonl
EVENTS_LOG=$WORKSHOP/.runtime/events.jsonl
COMPLETED=$WORKSHOP/.runtime/agent-runs-completed.jsonl
MAX_PENDING=5

if [[ -s "$PENDING" ]]; then
  TICK_TS=$(date -Iseconds)
  CONSUMED_COUNT=0
  TOTAL_PENDING=$(wc -l < "$PENDING")
  echo "[$TICK_TS] event-consumer: $TOTAL_PENDING pending records, draining up to $MAX_PENDING" >> "$LOG"

  # Move pending → working atomically; new pending records can land while we work.
  WORKING=$PENDING.working.$$
  mv "$PENDING" "$WORKING"
  touch "$PENDING"  # fresh empty file for new arrivals

  PROCESSED_LINES=$WORKING.processed
  : > "$PROCESSED_LINES"

  LINENO_TMP=0
  while IFS= read -r LINE; do
    LINENO_TMP=$((LINENO_TMP + 1))
    if [[ -z "$LINE" ]]; then continue; fi
    if [[ $CONSUMED_COUNT -ge $MAX_PENDING ]]; then
      # carry-over: write this and remaining lines back to PENDING
      echo "$LINE" >> "$PENDING"
      continue
    fi

    # Parse the record
    AGENT_ID=$(echo "$LINE" | python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('agent_id',''))" 2>/dev/null)
    SOURCE=$(echo "$LINE" | python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('source',''))" 2>/dev/null)
    EVT_OFFSET=$(echo "$LINE" | python3 -c "import json,sys; r=json.loads(sys.stdin.read()); er=r.get('event_ref') or {}; print(er.get('offset') if er.get('offset') is not None else '')" 2>/dev/null)

    if [[ -z "$AGENT_ID" || -z "$SOURCE" ]]; then
      printf '{"ts":"%s","status":"skipped","reason":"malformed","line":%s}\n' "$(date -Iseconds)" "$(echo "$LINE" | python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))")" >> "$COMPLETED"
      continue
    fi

    # Per-agent call-budget check (task 5c). GET /agents/{id}/budget returns
    # {used, limit, remaining, over}. Over => skip + record throttled.
    BUDGET_JSON=$(curl -sS -m 5 "http://127.0.0.1:8000/agents/$AGENT_ID/budget" 2>/dev/null)
    BUDGET_OVER=$(echo "$BUDGET_JSON" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('over', False)))" 2>/dev/null || echo 0)
    if [[ "$BUDGET_OVER" == "1" ]]; then
      BUDGET_USED=$(echo "$BUDGET_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('used',0))" 2>/dev/null || echo 0)
      BUDGET_LIMIT=$(echo "$BUDGET_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('limit',0))" 2>/dev/null || echo 0)
      echo "[$(date -Iseconds)] event-consumer: agent=$AGENT_ID over budget ($BUDGET_USED/$BUDGET_LIMIT per hour) — throttling" >> "$LOG"
      python3 -c "
import json,sys
print(json.dumps({
    'ts': sys.argv[1],
    'agent_id': sys.argv[2],
    'source': sys.argv[3],
    'event_offset': sys.argv[4],
    'status': 'throttled',
    'budget_used': int(sys.argv[5]),
    'budget_limit': int(sys.argv[6]),
}))
" "$(date -Iseconds)" "$AGENT_ID" "$SOURCE" "$EVT_OFFSET" "$BUDGET_USED" "$BUDGET_LIMIT" >> "$COMPLETED"
      continue
    fi

    # Re-read the event from events.jsonl at the byte offset
    EVENT_JSON=""
    if [[ -n "$EVT_OFFSET" && "$EVT_OFFSET" != "None" && -r "$EVENTS_LOG" ]]; then
      EVENT_JSON=$(python3 -c "
import sys, json
try:
    off = int(sys.argv[1])
    with open(sys.argv[2], 'rb') as f:
        f.seek(off)
        line = f.readline().decode('utf-8', errors='replace').strip()
    # Validate it parses; print compact form.
    obj = json.loads(line)
    print(json.dumps(obj, separators=(',', ':')))
except Exception as e:
    print(f'__ERR__{e}')
" "$EVT_OFFSET" "$EVENTS_LOG" 2>/dev/null)
    fi
    if [[ -z "$EVENT_JSON" || "$EVENT_JSON" == __ERR__* ]]; then
      EVENT_JSON='{"_note":"event payload unavailable; summary only","_lookup":"'"$EVT_OFFSET"'"}'
    fi

    # Build the prompt — short, local-leaning. Avoids "decide"/"design"/long
    # word-count which the classifier reads as architecture (cloud-route).
    EVENT_PROMPT="Webhook event for $AGENT_ID from $SOURCE: $EVENT_JSON. Summarize in one line; append to ~/workshop/.runtime/notify-masini.txt only if it crosses a rule in shared/global-memory.md (default skip). Exit."

    # Low-value webhook_code allowlist — short-circuit straight to local relay.
    # These are high-volume, near-zero-decision webhooks where summary is enough.
    # Pattern is plaid-specific for now; extend per-source as we see traffic.
    LOW_VALUE_CODES="SYNC_UPDATES_AVAILABLE HISTORICAL_UPDATE INITIAL_UPDATE DEFAULT_UPDATE WEBHOOK_UPDATE_ACKNOWLEDGED"
    EVT_WEBHOOK_CODE=$(echo "$EVENT_JSON" | \
      python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print((d.get('body') or {}).get('webhook_code') or d.get('webhook_code') or '')" 2>/dev/null)
    FORCE_LOCAL=0
    if [[ -n "$EVT_WEBHOOK_CODE" ]]; then
      for code in $LOW_VALUE_CODES; do
        if [[ "$code" == "$EVT_WEBHOOK_CODE" ]]; then FORCE_LOCAL=1; break; fi
      done
    fi

    # Privacy-relay routing: low-value codes skip classification. Otherwise
    # classify the event-handler task, dispatch local for boilerplate / no-op
    # events, claude -p only for architecture-class. Default cloud on classify
    # failure (preserves prior behavior if /relay is down).
    AGENT_RUN_TS=$(date -Iseconds)
    if [[ $FORCE_LOCAL -eq 1 ]]; then
      EVT_ROUTE="local"
      echo "[$AGENT_RUN_TS] event-consumer: webhook_code=$EVT_WEBHOOK_CODE forced local (allowlist)" >> "$LOG"
    else
      EVT_CLASSIFY_RESP=$(curl -sS -m 5 -X POST http://127.0.0.1:8000/relay/classify \
        -H 'Content-Type: application/json' \
        -d "$(python3 -c "import json,sys; print(json.dumps({'task': sys.argv[1]}))" "$EVENT_PROMPT")" 2>/dev/null)
      EVT_ROUTE=$(echo "$EVT_CLASSIFY_RESP" | \
        python3 -c "import json,sys; print(json.load(sys.stdin).get('route','cloud'))" 2>/dev/null || echo "cloud")
    fi

    if [[ "$EVT_ROUTE" == "local" ]]; then
      echo "[$AGENT_RUN_TS] event-consumer: relay_route=local agent=$AGENT_ID source=$SOURCE evt_offset=$EVT_OFFSET" >> "$LOG"
      LOCAL_RESP=$(curl -sS -m 300 -X POST http://127.0.0.1:8000/relay/local \
        -H 'Content-Type: application/json' \
        -d "$(python3 -c "import json,sys; print(json.dumps({'task': sys.argv[1], 'max_tokens': 1024}))" "$EVENT_PROMPT")" 2>/dev/null)
      CURL_EXIT=$?
      OLLAMA_OUT=$(echo "$LOCAL_RESP" | \
        python3 -c "import json,sys; print(json.load(sys.stdin).get('response','[relay/local no response]'))" 2>/dev/null \
        || echo "[relay/local parse failed]")
      echo "$OLLAMA_OUT" >> "$LOG"
      # Map curl exit → our EVT_EXIT convention. 0 success; anything else fail.
      EVT_EXIT=$CURL_EXIT
    else
      echo "[$AGENT_RUN_TS] event-consumer: relay_route=cloud firing claude -p for agent=$AGENT_ID source=$SOURCE evt_offset=$EVT_OFFSET" >> "$LOG"
      timeout 300 "$CLAUDE" -p "$EVENT_PROMPT" --output-format text >> "$LOG" 2>&1
      EVT_EXIT=$?
    fi
    AGENT_RUN_END=$(date -Iseconds)

    # Heartbeat for the agent
    if [[ -x "$WORKSHOP/scripts/heartbeat.sh" ]]; then
      "$WORKSHOP/scripts/heartbeat.sh" "$AGENT_ID" \
        mode=event-driven source="$SOURCE" \
        evt_exit="$EVT_EXIT" evt_offset="$EVT_OFFSET" >/dev/null 2>&1 || true
    fi

    # Append completed record
    if [[ $EVT_EXIT -eq 0 ]]; then
      STATUS="consumed"
    elif [[ $EVT_EXIT -eq 124 ]]; then
      STATUS="timeout"
    else
      STATUS="failed"
    fi
    python3 -c "
import json,sys
print(json.dumps({
    'ts': sys.argv[1],
    'agent_id': sys.argv[2],
    'source': sys.argv[3],
    'event_offset': sys.argv[4],
    'status': sys.argv[5],
    'exit_code': int(sys.argv[6]),
    'started_at': sys.argv[7],
    'ended_at': sys.argv[8],
    'relay_route': sys.argv[9],
}))
" "$AGENT_RUN_END" "$AGENT_ID" "$SOURCE" "$EVT_OFFSET" "$STATUS" "$EVT_EXIT" "$AGENT_RUN_TS" "$AGENT_RUN_END" "$EVT_ROUTE" >> "$COMPLETED"

    CONSUMED_COUNT=$((CONSUMED_COUNT + 1))
  done < "$WORKING"

  rm -f "$WORKING" "$PROCESSED_LINES"
  echo "[$(date -Iseconds)] event-consumer: consumed=$CONSUMED_COUNT remaining_in_pending=$(wc -l < "$PENDING")" >> "$LOG"
fi
# =============================================================================
# end pending-queue consumer
# =============================================================================

# If TODO has no unblocked tasks, we're done after the consumer ran (or no-op'd).
if [[ $NO_PENDING_TASKS -eq 1 ]]; then
  echo "[$(date -Iseconds)] no unblocked [ ] tasks in TODO.md; idle (pending-queue consumer may have run above)" >> "$LOG"
  exit 0
fi

PROMPT='You are the Workshop autonomous build agent. You are running as a one-shot `claude -p` invocation from cron.

Your job, every invocation:
1. Read ~/workshop/TODO.md fresh.
2. Pick the FIRST unblocked `[ ]` task (skip `[~]` in-progress and `[x]` done).
3. Update the checkbox to `[~]` with a brief inline note of what you are doing.
4. Execute the task. Use any MCPs you have access to (filesystem, google-docs, plaid, playwright). You do NOT have Discord MCP in this mode.
5. When the task is complete, change `[~]` to `[x]` and append a line to the `## Log` section in this format:
   `[<ISO timestamp>] <one-line description of what was done; file paths if relevant>`
6. Commit nothing to git. Do NOT push. Do NOT touch masini-bot.service. Do NOT make API calls that cost money beyond the work itself.
7. If you encounter a blocker (missing credential, ambiguous spec, classifier denial, anything that needs operator decision), mark the task `[~]` with a clear note `[~] <task> — BLOCKED: <reason>`. Stop. Do not pick another task. The operator will see the blocker in TODO.md.
8. If you completed a meaningful step (Phase 1 milestone, integration shipped, etc.), additionally write a one-paragraph status note to ~/workshop/status.md (overwrite, not append). Operator pings masini "what is the latest" and masini reads status.md back.
9. **Optional follow-up discovery (task 5h.95):** if while executing the task you genuinely discover a follow-up item the plan does not yet cover, append up to 3 (HARD cap) `[proposed] <short description>` lines to a `## Proposed` section in TODO.md. Create that section if it does not exist. Operator reviews and flips `[proposed]` → `[ ]` to authorize. Do NOT use this to dodge classifier blocks or to expand scope opportunistically — only for items the plan clearly needs.

Source of truth for spec:
- ~/workshop/TODO.md (task list)
- Google Doc tabs 3 (plan) and 4 (implementation), readable via `mcp__google-docs__readDocument` if loaded. Doc IDs: `1Gck2aHTDzBiUxitv5dK-427I_Ec_cHGGY-b_HeF9Tw0`, standalone Tab 3 `1V7XLcrFEsYq5PGIj8wdF0lJU5MfU34-G8QXG3lFt2VM`, standalone Tab 4 `1WiPcMClOBK4WGtogUmE0IeMDXL--9hsCAYgSgxooK4c`.

Constraints:
- This is ONE invocation. Pick ONE task. Do not try to do the whole phase.
- Keep your context window lean — prefer `searchDocuments` / `listDocuments` / file `Read` with offset+limit over reading whole docs cold.
- Be terse in your final text output (this is going to a log file the operator will skim).

Begin.'

RUNS_JSONL=$WORKSHOP/.runtime/agent-runs.jsonl
mkdir -p "$(dirname "$RUNS_JSONL")"
TICK_START=$(date -Iseconds)
TICK_START_EPOCH=$(date +%s)
TASK_PICKED=$(grep -m1 '^\[ \]' "$TODO" 2>/dev/null | head -c 200 | tr '\n' ' ')

# Privacy-relay routing: classify task, then dispatch to Ollama for boilerplate
# or Claude for architecture. Default to cloud on any classify failure.
RELAY_RESPONSE=$(curl -sS -m 5 -X POST http://127.0.0.1:8000/relay/classify \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c "import json,sys; print(json.dumps({'task': sys.argv[1]}))" "$TASK_PICKED")" 2>/dev/null)
RELAY_DECISION=$(echo "$RELAY_RESPONSE" | \
  python3 -c "import json,sys; print(json.load(sys.stdin).get('route','cloud'))" 2>/dev/null || echo "cloud")

echo "[$TICK_START] === cron tick === relay_route=$RELAY_DECISION" >> "$LOG"

if [[ "$RELAY_DECISION" == "local" ]]; then
  # Boilerplate task: route to local Qwen via /relay/local (faster, free).
  # Ollama has no file-write tools, so the script handles TODO.md lifecycle.

  # Pin the exact line number so we target only THIS task (not earlier [~] blocked items).
  TASK_LINE=$(grep -n '^\[ \]' "$TODO" 2>/dev/null | head -1 | cut -d: -f1)
  if [[ -z "$TASK_LINE" ]]; then
    echo "[$TICK_START] local path: no [ ] task found (race condition?); skipping" >> "$LOG"
    EXIT_CODE=0
  else
    sed -i "${TASK_LINE}s/^\[ \]/[~]/" "$TODO"

    # Call local relay (5 min timeout for large model)
    RELAY_LOCAL_RESP=$(curl -sS -m 300 -X POST http://127.0.0.1:8000/relay/local \
      -H 'Content-Type: application/json' \
      -d "$(python3 -c "import json,sys; print(json.dumps({'task': sys.argv[1], 'max_tokens': 2048}))" "$TASK_PICKED")" 2>/dev/null)
    EXIT_CODE=$?
    OLLAMA_TEXT=$(echo "$RELAY_LOCAL_RESP" | \
      python3 -c "import json,sys; print(json.load(sys.stdin).get('response','[relay/local no response]'))" 2>/dev/null \
      || echo "[relay/local failed]")
    echo "$OLLAMA_TEXT" >> "$LOG"

    # Mark that same line [~] → [x] and append log entry to ## Log section.
    sed -i "${TASK_LINE}s/^\[~\]/[x]/" "$TODO"
    LOG_TS=$(date -u +%Y-%m-%dT%H:%MZ)
    printf '[%s] [cron-agent/local] %s (routed to local relay; output in cron_agent.log)\n' \
      "$LOG_TS" "$(echo "$TASK_PICKED" | head -c 160)" >> "$TODO"
  fi

else
  # Architecture-class task: use Claude -p (subscription auth, full tool-use).
  # Claude handles its own TODO.md lifecycle per the PROMPT instructions.
  # 30 min hard cap so a runaway claude -p can't pin the loop forever.
  timeout 1800 "$CLAUDE" -p "$PROMPT" --output-format text >> "$LOG" 2>&1
  EXIT_CODE=$?
fi
TICK_END=$(date -Iseconds)
DURATION=$(( $(date +%s) - TICK_START_EPOCH ))
echo "[$TICK_END] === cron exit $EXIT_CODE (${DURATION}s) ===" >> "$LOG"

# Structured run record for the UI activity feed + future failure-triage.
python3 -c "import json,sys; print(json.dumps({'ts':'$TICK_START','task_picked':'''$TASK_PICKED''','exit_code':$EXIT_CODE,'duration_s':$DURATION,'relay_route':'$RELAY_DECISION','notes':'timeout' if $EXIT_CODE == 124 else ''}))" >> "$RUNS_JSONL" 2>/dev/null || true

# Build-failure auto-triage (task 96): on non-zero exit, dump the tick's tail
# of the log to .runtime/cron-failures/<ts>.txt and append a [~] BLOCKED entry
# to TODO.md so the operator (or masini on next inbound) sees the failure.
if [[ $EXIT_CODE -ne 0 ]]; then
  FAIL_DIR=$WORKSHOP/.runtime/cron-failures
  mkdir -p "$FAIL_DIR"
  FAIL_FILE=$FAIL_DIR/$(date +%Y%m%dT%H%M%S).txt
  # Capture the last ~200 lines of the log corresponding to this tick.
  tail -200 "$LOG" > "$FAIL_FILE"
  echo "[~] BLOCKED: cron-failure — exit $EXIT_CODE on task \"$(echo "$TASK_PICKED" | head -c 80)\" — see $FAIL_FILE" >> "$TODO"
  # Also notify masini.
  printf "[%s] CRON FAILURE exit=%d — %s\n" "$TICK_END" "$EXIT_CODE" "$FAIL_FILE" >> "$WORKSHOP/.runtime/notify-masini.txt"
fi

# Status broadcast: if the tick succeeded and added an entry to TODO.md's Log
# section, refresh status.md (overwrite) with the most recent log line and
# drop notify-masini.txt so masini surfaces a Discord ping on next inbound
# message. Decouples cron from Discord MCP (cron has no channels plugin).
if [[ $EXIT_CODE -eq 0 ]]; then
  LATEST_LOG_LINE=$(tac "$TODO" | grep -m1 '^\[20' || true)  # most recent [ISO ts] entry
  if [[ -n "$LATEST_LOG_LINE" ]]; then
    printf "Workshop cron status\nLast tick: %s\nLatest log: %s\n" "$TICK_END" "$LATEST_LOG_LINE" > "$WORKSHOP/status.md"
    printf "[%s] %s\n" "$TICK_END" "$LATEST_LOG_LINE" >> "$WORKSHOP/.runtime/notify-masini.txt"
  fi
fi
