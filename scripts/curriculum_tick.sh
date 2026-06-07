#!/bin/bash
# curriculum_tick.sh — Voyager-style automatic curriculum.
#
# When TODO.md `[ ]` count drops below THRESHOLD, fires a SINGLE `claude -p`
# whose only job is: scan the codebase, the ## Log, the Google Doc spec, and
# propose 1-5 concrete next-step `[ ]` items. Different prompt than the
# worker tick — this one's a planner, not an implementer.
#
# Rationale (from research brief):
#   "Voyager's automatic curriculum: a separate LLM call dedicated to proposing
#    the next goal. The prompt is *only* 'given current state X and prior
#    accomplishments Y, propose the next concrete task.'"
#
# Solves the empty-queue idle problem cleanly: when the worker has run out of
# work, this surfaces new work without operator intervention.
#
# Runs hourly via cron. No-op if TODO.md already has enough [ ] items.

set -u

WORKSHOP=/home/clawd-a/workshop
TODO=$WORKSHOP/TODO.md
LOG=$WORKSHOP/.runtime/curriculum.log
CLAUDE=/home/clawd-a/.nvm/versions/node/v22.22.3/bin/claude
AUTH_ENV=/home/clawd-a/.config/clawdbot/auth.env

THRESHOLD=3   # if fewer than this many [ ] items, propose more
LOCK=/tmp/workshop-curriculum.lock

mkdir -p "$(dirname "$LOG")"
ts() { date -Iseconds; }
log() { echo "[$(ts)] [curriculum] $*" >> "$LOG"; }

# Auth.
if [[ -r "$AUTH_ENV" ]]; then
  set -a; . "$AUTH_ENV"; set +a
fi
unset ANTHROPIC_API_KEY

# Lock — don't overlap with worker cron.
exec 9>"$LOCK"
if ! flock -n 9; then
  log "another curriculum_tick is running; skipping"
  exit 0
fi

cd "$WORKSHOP" || { log "cannot cd to $WORKSHOP"; exit 1; }

# Pause-until guard: skip silently if autonomy is paused until a future PT
# date (self-clearing). See pause_guard.sh.
source /home/clawd-a/workshop/scripts/pause_guard.sh
workshop_pause_guard "$LOG" "curriculum_tick"

# Operator off-switch — same flag as cron_agent.sh.
if [[ -f "$WORKSHOP/.runtime/AUTONOMY_DISABLED" ]]; then
  log "autonomy disabled (flag present); skipping tick"
  exit 0
fi

OPEN_COUNT=$(grep -c '^\[ \]' "$TODO" 2>/dev/null)
OPEN_COUNT=${OPEN_COUNT:-0}

log "tick: open_count=$OPEN_COUNT threshold=$THRESHOLD"

if (( OPEN_COUNT >= THRESHOLD )); then
  log "queue not low; no-op"
  exit 0
fi

# Pre-run the work_generator + work_verifier as cheap first-line refreshes.
# These are deterministic — if they produce items we don't need to spend
# cloud cycles on the curriculum LLM call.
log "running work_verifier + work_generator first (deterministic)"
"$WORKSHOP/scripts/work_verifier.sh" >/dev/null 2>&1 || true
GENERATED=$("$WORKSHOP/scripts/work_generator.sh" 2>/dev/null | tail -1)
GENERATED=${GENERATED:-0}
log "deterministic generators added: $GENERATED items"

# Re-count. If we now have enough work, exit.
OPEN_COUNT=$(grep -c '^\[ \]' "$TODO" 2>/dev/null)
OPEN_COUNT=${OPEN_COUNT:-0}
if (( OPEN_COUNT >= THRESHOLD )); then
  log "deterministic generators filled queue (now $OPEN_COUNT); skipping LLM call"
  exit 0
fi

# Build the curriculum prompt. Strict instructions: ONLY append [proposed]
# lines to ## Proposed, nothing else. No code edits. No claims of "doing"
# anything.
CURRICULUM_PROMPT=$(cat <<'PROMPT'
You are the **curriculum planner** for the Workshop autonomous build. Your
ONLY job this turn is to propose 1-5 concrete next-step tasks.

Do NOT implement anything. Do NOT edit code. Do NOT modify TODO.md outside of
appending lines to the ## Proposed section.

## Source material (read these to ground your proposals)

1. `~/workshop/TODO.md` — read it fresh, scan the ## Log section for recent
   activity, note any `[~]` blocked items.
2. `~/workshop/shared/learnings.md` — known failure patterns (don't re-propose
   things that hit these).
3. The codebase under `~/workshop/backend/`, `~/workshop/frontend/src/`,
   `~/workshop/scripts/` — what's there? What's wired? What's orphaned?
4. The Google Doc spec (if accessible via `mcp__google-docs__readDocument`):
   doc IDs `1Gck2aHTDzBiUxitv5dK-427I_Ec_cHGGY-b_HeF9Tw0`, tab 3
   `1V7XLcrFEsYq5PGIj8wdF0lJU5MfU34-G8QXG3lFt2VM`, tab 4
   `1WiPcMClOBK4WGtogUmE0IeMDXL--9hsCAYgSgxooK4c`. Skip if classifier-blocked.

## Proposal criteria

Each proposal must be:
- **Concrete**: nameable artifacts (file paths, endpoint paths, function names).
  "Make the build better" is not a proposal. "Add a /agents/{id}/budget/reset
  POST endpoint that zeros out the current hour's call count" is.
- **Single-tick sized**: completable in one cron-fired claude -p invocation
  (~25 min wall-time). If it's bigger, split it.
- **Non-duplicative**: scan the current TODO.md (open + closed) before
  proposing. Don't re-propose [x] items.
- **Spec-grounded**: prefer items that are in the Google Doc but not yet
  in TODO.md, OR follow-ups that close a "still needs / pending" caveat
  in a prior [x] line.
- **Independent**: prefer items not blocked on operator hardware (iOS,
  cloudflared interactive, OW developer-password unknowns).

## Output

Append your proposals to the bottom of `~/workshop/TODO.md` in the form:

    [proposed] <one-line task description with concrete artifacts>

Each on its own line, under the existing `## Proposed` section (create the
section if it doesn't exist). Max 5 proposals. Use the Bash tool with `echo
>> TODO.md` or the Edit tool — either is fine.

Then briefly summarize in your final text output:
- How many proposals you added
- The general theme (UI gaps, integration follow-ups, observability, etc.)

That's it. Don't do anything else. Don't grade your own proposals. Don't ask
"what next" — the worker tick will pick these up automatically.

Begin.
PROMPT
)

log "firing curriculum claude -p"
TICK_START=$(date +%s)
timeout 600 "$CLAUDE" -p "$CURRICULUM_PROMPT" --output-format text --max-turns 8 >> "$LOG" 2>&1
EXIT_CODE=$?
DURATION=$(( $(date +%s) - TICK_START ))

NEW_OPEN_COUNT=$(grep -c '^\[ \]' "$TODO" 2>/dev/null)
NEW_OPEN_COUNT=${NEW_OPEN_COUNT:-0}
NEW_PROPOSED_COUNT=$(grep -c '^\[proposed\]' "$TODO" 2>/dev/null)
NEW_PROPOSED_COUNT=${NEW_PROPOSED_COUNT:-0}
log "tick complete: exit=$EXIT_CODE duration=${DURATION}s open_count=$NEW_OPEN_COUNT proposed_count=$NEW_PROPOSED_COUNT"

exit "$EXIT_CODE"
