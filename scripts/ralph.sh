#!/bin/bash
# ralph.sh — completion-sentinel re-fire loop.
#
# The Ralph Wiggum technique adapted to this codebase. Wraps `claude -p` such
# that the model keeps iterating against a task until either:
#   (a) it emits the sentinel `<promise>COMPLETE</promise>` AND reviewer.sh
#       approves the diff;
#   (b) the iteration cap (default 10) is hit;
#   (c) the wall-time cap (default 25min) is hit;
#   (d) a classifier block is detected.
#
# Each iteration sees the PREVIOUS iteration's diff via `git status` and the
# reviewer's verdict, so it can self-correct. Continuity across iterations is
# preserved with `claude --continue` after the first turn. Cap on cost: each
# iteration runs with `--max-turns 6` so a single claude tick can't burn
# unbounded budget.
#
# Usage:
#   ralph.sh "<task description>" [--baseline-rev <rev>] [--max-iter N] [--max-wall S]
#
# Exit codes:
#   0  — reviewer approved (COMPLETE)
#   1  — iteration cap hit without approval
#   2  — wall-time cap hit
#   3  — classifier block hit
#   4  — startup failure
#   5  — environment failure on iter 1 (API outage/auth): release, do NOT burn an attempt

set -u

WORKSHOP=/home/clawd-a/workshop
CLAUDE=/home/clawd-a/.nvm/versions/node/v22.22.3/bin/claude
AUTH_ENV=/home/clawd-a/.config/clawdbot/auth.env
REVIEWER=$WORKSHOP/scripts/reviewer.sh

ts() { date -Iseconds; }

# Parse args.
TASK=""
BASELINE=""
DIR="$WORKSHOP"          # working tree to drive (default = workshop root)
MAX_ITER=10
MAX_WALL=1500   # 25 minutes
MAX_TURNS_PER_ITER=6
BILL_PROJECT=""   # --bill-project <scope>: record this run's tokens to the usage ledger
LEARN_SCOPE_ARG=""  # --learn-scope <scope>: the learnings scope (project:/agent:/workshop)
RUN_TOKENS=0      # accumulated real tokens (input+cache+output) across iterations

while [[ $# -gt 0 ]]; do
  case "$1" in
    --baseline-rev)  BASELINE="$2"; shift 2 ;;
    --dir)           DIR="$2"; shift 2 ;;
    --max-iter)      MAX_ITER="$2"; shift 2 ;;
    --max-wall)      MAX_WALL="$2"; shift 2 ;;
    --bill-project)  BILL_PROJECT="$2"; shift 2 ;;
    --learn-scope)   LEARN_SCOPE_ARG="$2"; shift 2 ;;
    *)               TASK="$1"; shift ;;
  esac
done

# Per-tree log + reviewer scoping. A project run (DIR != WORKSHOP) logs into the
# project's own .runtime and reviews the project's git repo via REVIEWER_ROOT.
LOG=$DIR/.runtime/ralph.log
mkdir -p "$(dirname "$LOG")"
log() { echo "[$(ts)] [ralph] $*" >> "$LOG"; echo "[ralph] $*"; }

if [[ -z "$TASK" ]]; then
  echo "usage: ralph.sh \"<task description>\" [--baseline-rev rev] [--max-iter N] [--max-wall sec]" >&2
  exit 4
fi

# Subscription auth + defense against accidental API-key auth.
if [[ -r "$AUTH_ENV" ]]; then
  set -a; . "$AUTH_ENV"; set +a
fi
unset ANTHROPIC_API_KEY

cd "$DIR" || { log "cannot cd to $DIR"; exit 4; }

# Per-tree lock: the workshop-root run keeps the shared key (so it and cron_agent
# never race); a per-project run gets its own key so it can run concurrently with
# the root loop but never twice against the same project tree.
if [[ "$DIR" == "$WORKSHOP" ]]; then
  LOCK=/tmp/workshop-ralph.lock
else
  LOCK=/tmp/workshop-ralph-$(echo "$DIR" | md5sum | cut -c1-12).lock
fi
exec 9>"$LOCK"
if ! flock -n 9; then
  log "another ralph is running against $DIR; aborting"
  exit 4
fi

# Baseline rev: caller-supplied, else current HEAD.
if [[ -z "$BASELINE" ]]; then
  BASELINE=$(git rev-parse HEAD 2>/dev/null || echo "")
fi

START_EPOCH=$(date +%s)
SENTINEL='<promise>COMPLETE</promise>'

log "starting task: $(echo "$TASK" | head -c 200)"
log "baseline=$BASELINE max_iter=$MAX_ITER max_wall=${MAX_WALL}s"

# Build the prompt template. Iteration 1 = full task. Iteration N>1 = "continue,
# reviewer said incomplete because <reasons>".
RALPH_INSTRUCTIONS=$(cat <<'PROMPT'
You are running inside Ralph — a completion-driven re-fire loop. Your job is
to DRIVE THIS TASK TO DONE.

Rules of the loop:

1. Read the task carefully and execute it FULLY. Not a partial step. Full.
2. Before considering yourself done, run `~/workshop/scripts/reviewer.sh` via
   Bash. Parse its JSON. If `complete: false`, READ the `reasons` field —
   that is precisely what you must fix. Then iterate.
3. STUBS ARE FORBIDDEN. Do NOT write `# TODO: implement this`,
   `pass  # stub`, `raise NotImplementedError`, `...`, "Here you would do X",
   `throw new Error('not implemented')`, or any equivalent. If a step is too
   big for one iteration, split it: mark the task `[~] BLOCKED:
   too-complex-for-single-tick` in TODO.md and propose smaller sub-tasks
   under `## Proposed`. Then exit (this is a legitimate completion).
4. VERIFY before claiming done. If you added an endpoint, curl it. If you
   added a route, grep the wiring. If you added a script, dry-run it.
5. When (and ONLY when) reviewer.sh returns `complete: true`, emit the
   literal sentinel on its own line as the LAST line of your text output:

       <promise>COMPLETE</promise>

   The Ralph loop greps for this exact string to decide whether to re-fire.
   No sentinel = re-fire.
6. If a step is genuinely impossible (missing operator-side credential,
   classifier block, etc.), mark the task `[~] BLOCKED: <specific reason>`
   in TODO.md and emit the sentinel — that's a valid completion.
7. Be terse in narrative output. The Ralph log is for the operator to skim.

The task follows below.
PROMPT
)

# Scope the reviewer command the model should run to the tree it is driving.
if [[ "$DIR" == "$WORKSHOP" ]]; then
  REVIEWER_CMD="~/workshop/scripts/reviewer.sh"
else
  REVIEWER_CMD="REVIEWER_ROOT=$DIR ~/workshop/scripts/reviewer.sh"
  RALPH_INSTRUCTIONS="$RALPH_INSTRUCTIONS

## Environment for THIS run
You are driving a single project: $DIR
- Your working directory IS the project root. Read PROJECT.md / AGENTS.md / TODO.md
  there for the brief and constraints. Do the work inside this tree only.
- Run the completion gate with the project scope:  $REVIEWER_CMD
  (it inspects THIS project's git repo, not the workshop root)."
fi

# Inject learned guardrails (Stage 8 wiring): the same global + scope memory the
# chat path injects, so autonomous sweeps benefit from accumulated lessons and
# stop repeating rejected patterns. LEARN_SCOPE is the billing scope (a project
# slug) or empty for a root run. Best-effort; a missing store never blocks a run.
# The learnings/knowledge store requires a PREFIXED scope (project:/agent:/app:/
# workshop); a bare billing slug like "demo-x" is rejected, which silently made
# per-project learning empty and dumped every reviewer rejection into GLOBAL,
# polluting every chat prompt. Prefer an explicit --learn-scope; else derive from
# the billing scope, mapping a bare slug to project:<slug>. (review F3)
LEARN_SCOPE="${LEARN_SCOPE_ARG:-${BILL_PROJECT:-}}"
if [[ -n "$LEARN_SCOPE" && "$LEARN_SCOPE" != *:* && "$LEARN_SCOPE" != "workshop" ]]; then
  LEARN_SCOPE="project:$LEARN_SCOPE"
fi
learn_block() {
  (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" \
     python3 -m services.learnings block --scope "$1" 2>/dev/null) || true
}
GLOBAL_LEARN=$(learn_block global)
SCOPE_LEARN=""
if [[ -n "$LEARN_SCOPE" && "$LEARN_SCOPE" != "global" ]]; then
  SCOPE_LEARN=$(learn_block "$LEARN_SCOPE")
fi
if [[ -n "$GLOBAL_LEARN" || -n "$SCOPE_LEARN" ]]; then
  RALPH_INSTRUCTIONS="$RALPH_INSTRUCTIONS

## Learned guardrails (accumulated from past runs — do not repeat these)"
  [[ -n "$GLOBAL_LEARN" ]] && RALPH_INSTRUCTIONS="$RALPH_INSTRUCTIONS
$GLOBAL_LEARN"
  [[ -n "$SCOPE_LEARN" ]] && RALPH_INSTRUCTIONS="$RALPH_INSTRUCTIONS

Scope-specific ($LEARN_SCOPE):
$SCOPE_LEARN"
  log "injected learnings (global=$([[ -n $GLOBAL_LEARN ]] && echo y || echo n) scope=$([[ -n $SCOPE_LEARN ]] && echo y || echo n))"
fi

iter=0
exit_code=1
last_reviewer_json='{"reasons":["initial-iteration-not-yet-run"]}'

while (( iter < MAX_ITER )); do
  iter=$((iter + 1))

  # Wall-time check.
  ELAPSED=$(( $(date +%s) - START_EPOCH ))
  if (( ELAPSED >= MAX_WALL )); then
    log "wall-time cap (${MAX_WALL}s) hit at iter=$iter"
    exit_code=2
    break
  fi
  REMAINING=$(( MAX_WALL - ELAPSED ))
  PER_ITER_TIMEOUT=$(( REMAINING < 1500 ? REMAINING : 1500 ))

  log "=== iter $iter/$MAX_ITER (elapsed=${ELAPSED}s, per-iter-timeout=${PER_ITER_TIMEOUT}s) ==="

  # Build the iteration prompt. First iteration: full task. Subsequent: "continue,
  # reviewer said X". The reviewer JSON gives concrete failure reasons the model
  # can address.
  if (( iter == 1 )); then
    ITER_PROMPT="$RALPH_INSTRUCTIONS

## Task

$TASK"
    CONTINUE_FLAG=""
  else
    REASONS=$(echo "$last_reviewer_json" | python3 -c "import json,sys; print(', '.join(json.load(sys.stdin).get('reasons',[])))" 2>/dev/null || echo "unknown")
    STUBS=$(echo "$last_reviewer_json" | python3 -c "
import json,sys
hits = json.load(sys.stdin).get('stub_hits',[])
print('\n'.join(f'  - {h[\"file\"]}:{h[\"line\"]} ({h[\"marker\"]})' for h in hits[:20]))
" 2>/dev/null || echo "")
    ITER_PROMPT="The reviewer rejected the previous iteration. Address each failure reason BELOW before re-running the reviewer.

Reasons: $REASONS

Stub hits (must be removed or replaced with real impl):
$STUBS

Re-read ~/workshop/scripts/reviewer.sh's output if you need the full structured detail. Then DO THE WORK. When done, emit the sentinel.

(Original task: $(echo "$TASK" | head -c 400))"
    CONTINUE_FLAG="--continue"
  fi

  # Capture stdout for sentinel + classifier-block detection.
  # Per-tree AND per-pid: the dispatcher runs sweeps concurrently, so a shared
  # $WORKSHOP path let runs read each other's output (foreign sentinel = false
  # completion, cross-fired classifier block, cross-billed tokens). $DIR/.runtime
  # exists (LOG lives there). Two sequential tasks in one sweep share a pid but
  # never overlap in time, so pid+iter is unique per live run. (review F1)
  ITER_OUT=$DIR/.runtime/ralph-iter-$$-${iter}.out
  # --output-format json wraps the final text in `.result` (so the sentinel and
  # classifier grep -F still match verbatim) AND carries `.usage`, which is the
  # only way to meter real token spend for the usage ledger. --max-turns bounds
  # within-iter cost.
  set +e
  # </dev/null so claude doesn't wait on (and warn about) stdin — that warning
  # would otherwise be merged into ITER_OUT by 2>&1 and corrupt the JSON.
  ITER_START=$(date +%s)
  timeout "$PER_ITER_TIMEOUT" "$CLAUDE" $CONTINUE_FLAG -p "$ITER_PROMPT" \
    --output-format json \
    --max-turns "$MAX_TURNS_PER_ITER" \
    </dev/null >"$ITER_OUT" 2>&1
  CLAUDE_EXIT=$?
  ITER_DUR=$(( $(date +%s) - ITER_START ))
  set -e

  # Sum this iteration's real context+output tokens (usage.input_tokens +
  # cache_read + cache_creation + output_tokens) into the run total. Robust to a
  # stray non-JSON line (e.g. a warning) landing in the file via 2>&1: try whole,
  # then the line carrying "usage", then from the first result object.
  ITER_TOKENS=$(python3 -c "
import json
tok = 0
try:
    txt = open('$ITER_OUT').read()
    obj = None
    try:
        obj = json.loads(txt)
    except Exception:
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith('{') and '\"usage\"' in line:
                try:
                    obj = json.loads(line); break
                except Exception:
                    pass
        if obj is None:
            i = txt.find('{\"type\"')
            if i >= 0:
                try:
                    obj = json.loads(txt[i:])
                except Exception:
                    obj = None
    u = (obj or {}).get('usage') or {}
    tok = (int(u.get('input_tokens', 0)) + int(u.get('cache_read_input_tokens', 0))
           + int(u.get('cache_creation_input_tokens', 0)) + int(u.get('output_tokens', 0)))
except Exception:
    tok = 0
print(tok)
" 2>/dev/null || echo 0)
  RUN_TOKENS=$((RUN_TOKENS + ITER_TOKENS))

  log "iter $iter: claude exit=$CLAUDE_EXIT, output bytes=$(stat -c %s "$ITER_OUT" 2>/dev/null || echo 0), tokens=$ITER_TOKENS, dur=${ITER_DUR}s"

  # Environment failure (API outage / auth expiry), NOT task failure: on the
  # FIRST iteration claude exited nonzero, produced no usage AND no result
  # envelope, and it was not a genuine wall-timeout kill (exit 124). The task did
  # not fail, the environment did (design 2d), so bail with a distinct code so the
  # caller releases the task WITHOUT burning an attempt or tripping a task-failure
  # breaker on good work. (review F4)
  if (( iter == 1 && CLAUDE_EXIT != 0 && CLAUDE_EXIT != 124 && ITER_TOKENS == 0 )) \
     && ! grep -q '"result"' "$ITER_OUT" 2>/dev/null; then
    log "ENV FAILURE on iter 1 (claude exit=$CLAUDE_EXIT, no result envelope, ${ITER_DUR}s) — releasing without burning an attempt"
    exit_code=5
    break
  fi

  # Detect classifier block in output — the operator's hard rule says these
  # are real blocks, not workarounds. Treat as terminal for this Ralph run.
  # Match only the exact multi-word block phrases, never the bare words
  # "classifier"/"safety-check": this workspace has a BirdNET classifier
  # subsystem, so legitimate task output routinely mentions "classifier" and must
  # NOT be read as a safety block. (review F2)
  if grep -qE '3 consecutive actions were blocked|Safety-Check Bypass|blocked by the (auto-mode )?classifier' "$ITER_OUT"; then
    log "classifier block detected in iter $iter output; stopping"
    exit_code=3
    break
  fi

  # Check sentinel.
  SAW_SENTINEL=0
  if grep -qF "$SENTINEL" "$ITER_OUT"; then
    SAW_SENTINEL=1
    log "sentinel observed in iter $iter output"
  fi

  # Run reviewer regardless of sentinel — sentinel is necessary but not sufficient.
  set +e
  REVIEWER_JSON=$(REVIEWER_ROOT="$DIR" "$REVIEWER" "$BASELINE" 2>>"$LOG")
  REVIEWER_EXIT=$?
  set -e
  last_reviewer_json="$REVIEWER_JSON"
  if (( REVIEWER_EXIT != 0 )); then
    log "reviewer.sh failed with exit $REVIEWER_EXIT — treating as incomplete"
    continue
  fi
  COMPLETE=$(echo "$REVIEWER_JSON" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('complete', False)))" 2>/dev/null || echo 0)
  log "iter $iter reviewer.complete=$COMPLETE"

  if (( SAW_SENTINEL == 1 && COMPLETE == 1 )); then
    log "PASS — sentinel + reviewer approval"
    exit_code=0
    break
  fi

  # If reviewer said complete but no sentinel, the worker forgot the contract — log learning.
  if (( COMPLETE == 1 && SAW_SENTINEL == 0 )); then
    "$WORKSHOP/scripts/log_learning.sh" \
      "Ralph: worker finished cleanly but forgot to emit <promise>COMPLETE</promise> sentinel — always end the final iteration with that exact string on its own line" \
      "ralph" 2>/dev/null || true
    log "PASS (reviewer ok, sentinel missed — treating as PASS)"
    exit_code=0
    break
  fi

  # Reviewer rejected. Record each reason as a learning so future runs avoid it.
  # Route into the billing scope (the project) when set, else global; record()
  # dedups by canonical hash + Jaccard so a repeated rejection does not pile up.
  if [[ -n "$REVIEWER_JSON" ]]; then
    echo "$REVIEWER_JSON" | (cd "$WORKSHOP" && \
      PYTHONPATH="$WORKSHOP/backend" LEARN_SCOPE="${LEARN_SCOPE:-}" python3 -c "
import json, sys, os
from services import learnings
scope = os.environ.get('LEARN_SCOPE') or learnings.GLOBAL
try:
    scope = learnings._valid_scope(scope)
except Exception:
    scope = learnings.GLOBAL
data = json.load(sys.stdin)
for reason in data.get('reasons', []):
    try:
        learnings.record(f'Ralph rejected: {reason}', source='ralph-reviewer',
                         scope=scope, tags=['ralph', 'rejection'])
    except Exception:
        pass
") 2>/dev/null || true
  fi
done

if (( iter >= MAX_ITER && exit_code == 1 )); then
  log "iteration cap ($MAX_ITER) hit; final reviewer JSON: $last_reviewer_json"
fi

# Bill this run's real token spend to the usage ledger so the dispatcher's
# admission gate can enforce the rolling-window budget. Best-effort; never blocks
# the exit. Only autonomous (billed) runs record — a bare manual ralph does not.
if [[ -n "$BILL_PROJECT" && "$RUN_TOKENS" -gt 0 ]]; then
  (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" python3 -m services.usage_ledger record \
     --project "$BILL_PROJECT" --tokens "$RUN_TOKENS" >/dev/null 2>&1) || true
  log "billed $RUN_TOKENS tokens to usage ledger (scope=$BILL_PROJECT)"
fi

log "=== done iter=$iter exit_code=$exit_code (tokens=$RUN_TOKENS) ==="
exit "$exit_code"
