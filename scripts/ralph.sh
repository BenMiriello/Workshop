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
# preserved with `claude --continue` after the first turn. Cost is bounded by
# wall clock (--max-wall, PER_ITER_TIMEOUT) and by the usage cap — deliberately
# NOT by a per-iteration step count. See MAX_TURNS_PER_ITER below.
#
# Usage:
#   ralph.sh "<task description>" [--baseline-rev <rev>] [--max-iter N] [--max-wall S]
#            [--latitude strict|standard|open]
#
# Exit codes:
#   0  — reviewer approved (COMPLETE)
#   1  — iteration cap hit without approval
#   2  — wall-time cap hit
#   3  — classifier block hit
#   4  — startup failure
#   5  — environment failure on iter 1 (API outage/auth): release, do NOT burn an attempt
#   6  : in-run no-progress / thrash park: the run was making no progress (same
#        reviewer rejection or a near-identical diff repeated, or the per-run token
#        budget was exceeded), so it was stopped MID-RUN. The caller release/blocks.
#   7  — reviewer returned `block-for-decision`: a strict-latitude run whose gate
#        could not establish completeness. The caller blocks the task with the
#        reviewer's reason and lets the operator decide. Re-firing cannot fix it.

set -u

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# Resolve the claude CLI to a real EXECUTABLE. A pinned nvm node-version path is
# fragile: a node upgrade moves it, and a reinstall while the disk is full can
# leave a truncated, non-executable stub (observed 2026-07-16: a 500-byte
# claude.exe -> exit 126). Prefer the system install, then PATH, then nvm; `-x`
# follows the symlink so a broken target is skipped. Override with CLAUDE_BIN.
CLAUDE=""
for _c in "${CLAUDE_BIN:-}" /usr/local/bin/claude "$(command -v claude 2>/dev/null || true)" \
          "$HOME"/.nvm/versions/node/*/bin/claude; do
  if [[ -n "$_c" && -x "$_c" ]]; then CLAUDE="$_c"; break; fi
done
[[ -z "$CLAUDE" ]] && CLAUDE=/usr/local/bin/claude
unset _c
AUTH_ENV="${WORKSHOP_AUTH_ENV:-$HOME/.config/workshop/auth.env}"
REVIEWER=$WORKSHOP/scripts/reviewer.sh
PTASKS=$WORKSHOP/scripts/project_tasks.py

ts() { date -Iseconds; }

# Parse args.
TASK=""
BASELINE=""
DIR=""                   # working tree to drive. NO DEFAULT: a target must be
                         # named (ADR 0014). This used to default to $WORKSHOP,
                         # which meant a bare `ralph.sh "some task"` drove the
                         # platform's own source tree.
MAX_ITER=10
MAX_WALL=1500   # 25 minutes
# NO step cap per iteration by default. An iteration has to cover the whole
# contract — do the work, run reviewer.sh, commit — and a step budget cannot know
# how many steps that takes. At 6 a real ticket died at `max_turns` with the
# commit never reached, and a finished 339-line design doc was orphaned
# uncommitted (3x on each of two tickets, 2026-07-18); raising it to 10 only
# moved the cliff. A requirement the turn budget cannot afford is not a
# requirement, it is a guaranteed failure — so the budget is gone. Cost is
# bounded where it actually belongs: PER_ITER_TIMEOUT, MAX_WALL, MAX_ITER, and
# the usage cap. Set MAX_TURNS_PER_ITER in the env to reinstate a cap for a
# one-off debugging run.
MAX_TURNS_PER_ITER="${MAX_TURNS_PER_ITER:-}"
BILL_PROJECT=""   # --bill-project <scope>: record this run's tokens to the usage ledger
LEARN_SCOPE_ARG=""  # --learn-scope <scope>: the learnings scope (project:/agent:/workshop)
RECEIPT_PATH=""   # --receipt <path>: best-effort JSON receipt of this run's work product
# Claim identity for the task-store heartbeat (Fix 2). Set by project_ralph.sh when
# this run drives a claimed task; empty for a standalone/manual ralph (no heartbeat).
TASK_ID=""        # --task-id <id>: the claimed task this run works
CLAIM_TOKEN=""    # --claim-token <n>: the claim generation, presented to fence the beat
STORE_DIR=""      # --store-dir <dir>: scope dir whose .tasks.json holds the claim
                  # (the project dir; differs from --dir in worktree mode)
RUN_ID=""         # --run <job_id>: the child Job (attempt) id, for usage-ledger
                  # attribution (role/task_id/run). Empty for a standalone ralph.
RUN_TOKENS=0      # accumulated real tokens (input+cache+output) across iterations
LATITUDE_ARG=""   # --latitude <strict|standard|open>: the INVOCATION level of the
                  # latitude cascade (invocation > task > scope > default). Empty
                  # = this run states no preference and inherits.

while [[ $# -gt 0 ]]; do
  case "$1" in
    --baseline-rev)  BASELINE="$2"; shift 2 ;;
    # ABSOLUTE, always. This script cds into the tree it drives and then keeps
    # using $DIR and $RUNTIME for the log, the per-iteration output file and the
    # store. A RELATIVE --dir resolves against the caller's cwd for the first
    # use and against the driven tree for every use after the cd, so the log
    # writes fail, the iteration output file cannot be created, and the run
    # exits 1 in about a second. Measured 2026-08-14 with
    # `--dir projects/autonomy-smoke`: three attempts, all "No such file or
    # directory", no worker ever started.
    --dir)           DIR="$(cd "$2" 2>/dev/null && pwd -P || printf '%s' "$2")"; shift 2 ;;
    --max-iter)      MAX_ITER="$2"; shift 2 ;;
    --max-wall)      MAX_WALL="$2"; shift 2 ;;
    --bill-project)  BILL_PROJECT="$2"; shift 2 ;;
    --learn-scope)   LEARN_SCOPE_ARG="$2"; shift 2 ;;
    --receipt)       RECEIPT_PATH="$2"; shift 2 ;;
    --task-id)       TASK_ID="$2"; shift 2 ;;
    --claim-token)   CLAIM_TOKEN="$2"; shift 2 ;;
    --store-dir)     STORE_DIR="$2"; shift 2 ;;
    --run)           RUN_ID="$2"; shift 2 ;;
    --latitude)      LATITUDE_ARG="$2"; shift 2 ;;
    *)               TASK="$1"; shift ;;
  esac
done

# Developer-mode gate (ADR 0014). The platform repo is a permitted run target
# only when WORKSHOP_DEV_MODE=1, and a target must be named either way. Checked
# here, before any work, so a refusal costs nothing. Exit 4 = startup failure.
source "$WORKSHOP/scripts/dev_mode_gate.sh"
assert_run_target "$DIR" "ralph.sh" || exit 4

# The heartbeat targets the task STORE, which lives in the project dir even when
# this run drives an isolated worktree (--dir), so default it to the tree we drive.
[[ -z "$STORE_DIR" ]] && STORE_DIR="$DIR"

# This run's presence row. Keyed on the child Job id when project_ralph.sh
# passed one, so the directory entry and the run-state record name the same
# attempt; a standalone ralph keys on the tree it drives instead.
#
# --profile pid opts the record into the liveness accelerator, which is what
# makes the long TTL honest: an iteration can take PER_ITER_TIMEOUT (up to
# 1500s) and only beats between iterations, so a crashed run would otherwise sit
# in the directory until the TTL ran out. The accelerator probes THIS shell's
# pid and can only ever shorten the row's life.
PRESENCE_ID="${RUN_ID:-ralph-$(printf '%s' "$DIR" | md5sum | cut -c1-12)}"
PRESENCE_TTL="${WORKSHOP_RUN_PRESENCE_TTL:-1800}"
beat_presence() {
  local subject=(--subject-kind none)
  [[ -n "$TASK_ID" ]] && subject=(--subject-kind ticket --subject-id "$TASK_ID")
  python3 "$WORKSHOP/scripts/presence.py" heartbeat "$PRESENCE_ID" \
      --kind run --activity "${PRESENCE_ACTIVITY:-working}" "${subject[@]}" \
      --scope "$STORE_DIR" --pid $$ --profile pid --ttl "$PRESENCE_TTL" \
      >/dev/null 2>&1 || true
}
# Say we are gone rather than making the directory wait out the TTL.
release_presence() {
  python3 "$WORKSHOP/scripts/presence.py" release "$PRESENCE_ID" >/dev/null 2>&1 || true
}
trap release_presence EXIT

# Renew the claimed task's lease so a slow-but-alive worker is never reclaimed
# mid-iteration (Fix 2). Best-effort and inert unless project_ralph passed the
# claim identity. Presents the claim token so it is fenced by Fix 1: once an
# operator edit or a liveness reclaim displaces this run, the heartbeat becomes a
# no-op (as will the final write), which we log but do not treat as fatal.
#
# IT ALSO WRITES PRESENCE (workspace-ownership-and-leases-2026-08-22 sec 6, step
# B4). Two different records, on purpose, and conflating them is the mistake to
# avoid: the task-store beat renews a CLAIM on a ticket (durable, fenced by a
# token), while presence is the expiring fact that this run is here and what it
# is doing (disposable, owned by nobody). They are renewed together because they
# are true together, and this run is a separate OS process, so the only way the
# platform can see it at all is a shared store it shells into.
heartbeat_task() {
  beat_presence
  [[ -z "$TASK_ID" || -z "$CLAIM_TOKEN" || -z "$STORE_DIR" ]] && return 0
  local out hb_id
  out=$(python3 "$PTASKS" heartbeat "$STORE_DIR" "$TASK_ID" "$CLAIM_TOKEN" 2>>"$LOG")
  hb_id=$(printf '%s' "$out" | python3 -c "import json,sys
try: print((json.load(sys.stdin) or {}).get('id') or '')
except Exception: print('')" 2>/dev/null || echo "")
  if [[ -z "$hb_id" ]]; then
    log "heartbeat no-op for task=$TASK_ID (claim displaced/reclaimed?); continuing best-effort"
  fi
}

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

# Pre-run working-tree snapshot, for the auto-commit net (maybe_autocommit). The
# sweep tree is routinely dirty BEFORE a run (the task store, the regenerated
# TODO.md, unrelated operator edits), so "what this run wrote" is the set of
# paths dirty now that were not dirty at start — never the whole dirty tree.
PRERUN_DIRTY=$(cd "$DIR" && git status --porcelain 2>/dev/null | sed 's/^...//' || true)

# Bookkeeping churn, excluded from the auto-commit exactly as the receipt builder
# in project_ralph.sh excludes it from the reported diff.
BOOKKEEPING_RE='^(\.runtime/|\.tasks\.json|TODO\.md$)'

# The completion gate in project_ralph.sh reads the GIT LOG: a run that leaves
# its work uncommitted scores as "acceptance passed but no work was committed",
# and the task is released back to the queue with the work discarded. Prompt rule
# 5 tells the worker to commit, but a forgotten commit must not cost the entire
# run, so this is the deterministic backstop: once the reviewer has APPROVED,
# stage exactly the paths this run newly dirtied and commit them on the worker's
# behalf. A worker that committed properly hits the early return and this is a
# no-op.
maybe_autocommit() {
  local landed now_dirty new_paths n touches
  landed=$(cd "$DIR" && git rev-list "$BASELINE..HEAD" 2>/dev/null | tr -d '[:space:]')
  [[ -n "$landed" ]] && return 0

  # "Newly dirty" is NOT sufficient evidence that this run wrote a file: the tree
  # is shared, so an operator or a concurrent session editing alongside the sweep
  # shows up as newly dirty too, and auto-committing it would attribute someone
  # else's work to this ticket under the worker's name. (Observed exactly once,
  # 2026-07-18, before this guard existed.) So the backstop only ever commits
  # inside a lane the TASK declared: `touches`. A task with no declared lane is
  # released by the gate as before — the prompt rule is the primary mechanism and
  # this is only a net, so it is correct for it to decline rather than guess.
  touches=""
  if [[ -n "$TASK_ID" ]]; then
    touches=$(python3 "$PTASKS" list-json "$STORE_DIR" 2>/dev/null \
      | python3 -c "
import json,sys
try:
    for t in json.load(sys.stdin):
        if t.get('id') == sys.argv[1]:
            print('\n'.join(t.get('touches') or []))
            break
except Exception:
    pass
" "$TASK_ID" 2>/dev/null || true)
  fi
  now_dirty=$(cd "$DIR" && git status --porcelain 2>/dev/null | sed 's/^...//')
  new_paths=$(comm -13 <(printf '%s\n' "$PRERUN_DIRTY" | sort -u) \
                       <(printf '%s\n' "$now_dirty" | sort -u) || true)

  # Bookkeeping churn is excluded ONLY when the task did not explicitly ask for
  # it. A ticket whose DELIVERABLE is data — e.g. "encode the prose dependencies
  # into depends_on", whose entire output is .tasks.json — declares that file in
  # `touches`, and stripping it here made such a ticket unwinnable: the worker
  # did the work, the reviewer approved, and the done-gate then reported "nothing
  # was committed" because the only changed file had been filtered out. Three
  # attempts, then blocked, with the worker never at fault (observed on C2
  # dbcd929e, 2026-07-18). An explicit lane is a deliberate statement of intent
  # and outranks the default filter; without one the filter still applies.
  if [[ -z "$(printf '%s' "$touches" | tr -d '[:space:]')" ]]; then
    new_paths=$(printf '%s\n' "$new_paths" | grep -vE "$BOOKKEEPING_RE" || true)
  fi

  # No declared lane (the common case for a design ticket, whose output is a doc
  # nobody listed in advance). Rather than decline and orphan finished work, fall
  # back to the narrowest defensible rule: files that DID NOT EXIST when the run
  # started, i.e. currently untracked. A brand-new file appearing mid-run is the
  # worker's output with high confidence, and committing it cannot clobber a
  # concurrent editor's changes to an existing file — which was the actual hazard
  # (hit once on 2026-07-18). Modified-but-pre-existing files are still left
  # alone without a declared lane.
  if [[ -z "$(printf '%s' "$touches" | tr -d '[:space:]')" ]]; then
    local untracked
    untracked=$(cd "$DIR" && git ls-files --others --exclude-standard 2>/dev/null | sort -u)
    new_paths=$(comm -12 <(printf '%s\n' "$new_paths" | sort -u) \
                         <(printf '%s\n' "$untracked") || true)
    if [[ -z "$(printf '%s' "$new_paths" | tr -d '[:space:]')" ]]; then
      log "worker left changes uncommitted, task declares no touches lane, and nothing NEW was created — declining to auto-commit (cannot tell this run's edits to existing files from a concurrent editor's)"
      return 0
    fi
    log "no touches lane: rescuing only newly-created files"
  fi

  # Keep only paths inside the declared lane.
  if [[ -n "$(printf '%s' "$touches" | tr -d '[:space:]')" ]] \
     && [[ -n "$(printf '%s' "$new_paths" | tr -d '[:space:]')" ]]; then
    new_paths=$(printf '%s\n' "$new_paths" | python3 -c "
import fnmatch, sys
pats = [p for p in sys.argv[1].splitlines() if p.strip()]
for line in sys.stdin:
    p = line.strip()
    if p and any(fnmatch.fnmatch(p, pat) for pat in pats):
        print(p)
" "$touches" 2>/dev/null || true)
  fi
  [[ -z "$(printf '%s' "$new_paths" | tr -d '[:space:]')" ]] && return 0
  (cd "$DIR" && printf '%s\n' "$new_paths" | while IFS= read -r p; do
     [[ -n "$p" ]] && git add -- "$p"
   done) || return 0
  # `--only -- <paths>`: commit EXACTLY the paths we selected, ignoring whatever
  # else sits in the index. A bare `git commit` takes the whole index, so a file
  # a previous task's worker ran `git add` on (and never committed) rides along
  # into THIS task's commit — which happened: 465 lines of one ticket's docs
  # landed inside the next ticket's commit, and the touches gate then correctly
  # blocked that ticket for writing outside its lane.
  (cd "$DIR" && git -c user.name='ralph' -c user.email='ralph@workshop.local' \
     commit -q --only -m "ralph: $(echo "$TASK" | head -c 72 | tr '\n' ' ')" \
     -- $(printf '%s ' $new_paths) >>"$LOG" 2>&1) || return 0
  n=$(printf '%s\n' "$new_paths" | grep -c . || true)
  log "auto-committed $n file(s) the worker left uncommitted"
  "$WORKSHOP/scripts/log_learning.sh" \
    "Ralph: worker completed the task but left its changes UNCOMMITTED — the loop auto-committed them. Always commit before emitting the sentinel (prompt rule 5); the completion gate reads the git log, not the working tree." \
    "ralph" 2>/dev/null || true
  return 0
}

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
5. COMMIT AS SOON AS THE WORK EXISTS — BEFORE you run the reviewer. Do not save
   the commit for the end. The completion gate reads the GIT LOG, not your
   working tree: an uncommitted change is invisible to it, so the task is
   released back to the queue as "acceptance passed but no work was committed"
   and everything you did is thrown away. If you run out of turns while
   reviewing, an already-committed change survives and an uncommitted one does
   not. So: write the file, `git add` it, commit it with a short message, THEN
   review and iterate (amending or adding follow-up commits as you fix things).
   Stage only files this task owns — never `git add -A` over an unrelated dirty
   tree. If the task legitimately produces no file change (a pure external side
   effect), say so explicitly rather than committing nothing silently.
6. When (and ONLY when) reviewer.sh returns `complete: true` AND your work is
   committed, emit the literal sentinel on its own line as the LAST line of
   your text output:

       <promise>COMPLETE</promise>

   The Ralph loop greps for this exact string to decide whether to re-fire.
   No sentinel = re-fire.
7. If a step is genuinely impossible (missing operator-side credential,
   classifier block, etc.), mark the task `[~] BLOCKED: <specific reason>`
   in TODO.md and emit the sentinel — that's a valid completion.
8. Be terse in narrative output. The Ralph log is for the operator to skim.

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

# --- Latitude: what this run may do where the ticket does not say ------------
# Resolved HERE, at claim time, through the one cascade every reader shares
# (services/latitude.py): invocation > task > scope > "standard". First non-null
# wins; a null at any level means INHERIT, never "standard". The scope level is
# the scope that OWNS THE TREE this task lives in (LEARN_SCOPE is that key, in
# its prefixed form after P0's F3 fix) — an agent with `latitude: open` in its
# own frontmatter does not import that permissiveness into a project the operator
# marked strict.
#
# Exported as LATITUDE so both the `claude -p` env and reviewer.sh see the same
# resolved value; reviewer.sh blocks instead of guessing when it is `strict`.
LATITUDE_JSON=$( (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" python3 -m services.latitude \
  resolve --scope "$LEARN_SCOPE" \
  ${TASK_ID:+--store-dir "$STORE_DIR" --task-id "$TASK_ID"} \
  ${LATITUDE_ARG:+--invocation "$LATITUDE_ARG"} 2>>"$LOG") || true)
LATITUDE=$(printf '%s' "$LATITUDE_JSON" | python3 -c "import json,sys
try: print((json.load(sys.stdin) or {}).get('latitude') or 'standard')
except Exception: print('standard')" 2>/dev/null || echo "standard")
# A FAILED lookup must not report itself as a successful one. `|| true` above
# keeps an advisory resolution from failing the run, which is right -- but the
# source then said `default`, which is the same thing it says when the cascade
# ran fine and found nothing set. So a crash in the resolver was
# indistinguishable from "nothing is configured". That is how the OwnerUnset in
# services.latitude went unnoticed: every line of the log read normal (measured
# 2026-08-22). The VALUE still falls back to standard; only the SOURCE tells the
# truth.
LATITUDE_SOURCE=$(printf '%s' "$LATITUDE_JSON" | python3 -c "import json,sys
try: print((json.load(sys.stdin) or {}).get('source') or 'default')
except Exception: print('unresolved: latitude lookup failed, see log')" 2>/dev/null \
  || echo "unresolved: latitude lookup failed, see log")
export LATITUDE
log "latitude=$LATITUDE (source=$LATITUDE_SOURCE scope=${LEARN_SCOPE:-none} task=${TASK_ID:-none})"

# --- the reversibility gate, for a run nobody is watching --------------------
#
# THIS is the run agent_authority.IRREVERSIBLE_PATTERNS was written for. The list
# existed for weeks with one caller — the chat's @mention path — where the
# operator has just typed the mention, so `disallowed_for` returned the empty
# list every single time it was asked. The genuinely unattended sweep, this one,
# passed no tool flags at all and inherited ~/.claude/settings.json, which
# allow-lists the push and release commands at defaultMode auto.
#
# Unconditional, not conditional on a mode flag: `claude -p` is headless. There
# is no approval prompt in this process and no one to answer it, so "ask before
# something irreversible" can only be spelled "no". A ralph run started by hand
# from a terminal is the same headless subprocess as one started by cron.
#
# This denies the AGENT's tool calls. It does not touch the commands this script
# runs itself, which are ordinary shell and never pass through the CLI's tool
# gate — none of ralph.sh, project_ralph.sh or reviewer.sh publishes anything on
# its own behalf, so the loop's own machinery is unaffected by the deny list.
# test_agent_authority.py holds all three to that.
#
# Read from Python so the patterns have exactly one definition. If the call
# fails, the array is empty and the run proceeds without the gate: a broken
# import must not stop the autonomy loop, and the log line below says which of
# the two happened.
DENY_TOOLS=()
mapfile -t DENY_TOOLS < <( (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" \
  python3 -m services.agent_authority denied --unattended 2>>"$LOG") || true)
DENY_FLAGS=()
if (( ${#DENY_TOOLS[@]} > 0 )); then
  DENY_FLAGS=(--disallowedTools "${DENY_TOOLS[@]}")
  log "irreversible-action gate: ${#DENY_TOOLS[@]} patterns denied (${DENY_TOOLS[*]})"
else
  log "irreversible-action gate: NOT APPLIED (agent_authority produced no patterns)"
fi

# --- unattended write authorization (execution boundary, decided 2026-08-21) --
#
# `claude -p` is headless: there is no approval prompt in this process and nobody
# to answer one. A mode that can "stop and ask" (default/auto/acceptEdits) has no
# channel here, so the CLI accepts the mode and then silently auto-denies every
# gated call and reports success. That is the failure that left this loop unable
# to write a single file (measured 2026-08-14: the worker tried Write three times
# and a Bash redirect, was denied every time, and exited claiming completion; the
# reviewer correctly rejected it). The ONLY headless mode that actually writes is
# `bypassPermissions`.
#
# This is NOT a new posture. services/model_caps records that bypass is excluded
# from the CHAT PICKER per the operator's 2026-08-09 instruction, but that "the
# unattended paths still pass it explicitly (job runners, sweeps, the voice
# worker) ... those have no operator to ask". Ralph was the one unattended worker
# left without it, pending the SEC-A1 execution-boundary decision. The operator
# made that call on 2026-08-21 (see
# docs/platform-redesign/work-loop-core-design-2026-08-21.md, step S2).
#
# The boundary has TWO layers now, not one. (1) Kernel confinement: the spawn
# below runs through `scoped_exec exec`, the same bwrap seam the chat path uses,
# so an authorized-to-write worker still only sees its own scope root (mounted at
# /workspace) plus the named grants — the host tree, other scopes and other
# owners' data are structurally absent, not merely denied. This corrects a stale
# claim that lived here: bwrap confinement WAS non-functional on this host because
# scoped_exec.functional() under-bound its probe, which was fixed 2026-08-21
# (commit 0daa352); userns works here and every turn is confined. (2) The in-run
# irreversible-action deny gate built just above (push/publish/merge/delete/sudo/
# reboot via --disallowedTools) plus git-revertability — the same net every bypass
# turn relies on. Confinement bounds WHERE a write can land; the deny gate bounds
# WHAT cannot be done; together they are why bypassPermissions is safe here.
#
# EXPLICIT AND REVERSIBLE. Off unless WORKSHOP_UNATTENDED_WRITES is truthy, so
# merging this changes no existing run until it is deliberately enabled. Set
# WORKSHOP_UNATTENDED_WRITES=1 to authorize unattended writes for a run; unset or
# =0 keeps the pre-2026-08-21 (cannot-write) behavior. The S3 resident driver is
# where this becomes the default, after an attended verification run.
PERM_FLAGS=()
case "${WORKSHOP_UNATTENDED_WRITES:-0}" in
  1|true|yes|on|TRUE|YES|ON)
    PERM_FLAGS=(--permission-mode bypassPermissions)
    log "unattended writes: AUTHORIZED (bypassPermissions; irreversible-action deny gate active)" ;;
  *)
    log "unattended writes: NOT authorized (WORKSHOP_UNATTENDED_WRITES unset/0) — worker cannot write this run" ;;
esac


# --- the search tool, for a worker that cannot ask anybody --------------------
#
# The worker gets `mcp__workshop__search`: semantic search over the workshop's
# own notes, docs and past chats. It is the one tool that answers "have we
# decided this before", and the unattended sweep is the run that most needs it,
# because it has nobody to ask. Every chat surface had it and this one did not,
# purely because ralph spawns `claude -p` itself and passed no MCP flags at all.
#
# The launch spec is emitted by Python (services.llm_router mcp-config) rather
# than written out here. It is not a constant: it carries the venv path, the
# server path, PYTHONPATH, and — the part that matters — WORKSHOP_QUERY_OWNER.
# The server is OWNER-SCOPED, so an unpinned worker searches an empty index and
# every run still succeeds. A copy of that spec in bash is a copy that goes stale
# silently, and silently is the only way this failure ever presents.
#
# NO `--strict-mcp-config`. This is an ADDITIVE config: the worker is
# full-agentic by design, and strict mode would drop every other server the tree
# legitimately configures. Best-effort, like the deny gate above: if the emitter
# fails the run proceeds without search rather than not proceeding, and the log
# line below says which happened.
# INLINE JSON, not a temp file (--stdout, not the path-emitting default). Under
# confinement the worker runs in a bwrap view with a fresh `--tmpfs /tmp`, so a
# `--mcp-config /tmp/...json` path written on the host is simply ABSENT inside the
# sandbox and the search tool silently vanishes. The chat path (build_streaming)
# already passes the document inline for exactly this reason; ralph now matches it.
# `wrap()` remaps the server's absolute paths under the scope root to /workspace,
# so an inline spec resolves inside the sandbox where a /tmp path could not.
MCP_JSON=$( (cd "$WORKSHOP/backend" && .venv/bin/python -m services.llm_router \
  mcp-config --server workshop --stdout 2>>"$LOG") || true)
MCP_FLAGS=()
if [[ -n "$MCP_JSON" && "${MCP_JSON:0:1}" == "{" ]]; then
  # Placed so the next token in the spawn is always a flag (--max-turns or the
  # redirect): `--mcp-config` is variadic and would otherwise eat a following
  # non-flag token. Additive, never --strict-mcp-config (see comment above).
  MCP_FLAGS=(--mcp-config "$MCP_JSON")
  log "workshop search tool: enabled (inline mcp-config)"
  # Told to the model only when the tool is actually there. A prompt that
  # describes a tool the CLI was never given is worse than silence: the model
  # spends a turn calling it and reads the failure as its own mistake.
  RALPH_INSTRUCTIONS="$RALPH_INSTRUCTIONS

## The workshop's own memory
You have \`mcp__workshop__search\`: semantic search over this workshop's own
writing: knowledge notes, everything under docs/, and the full text of past
chats. You never saw the conversation this ticket came out of, and this is the
only way to read it.

Use it BEFORE you design anything, and whenever the ticket assumes context you
do not have: 'have we decided this before', 'why is this built this way', 'what
did we say about X'. An empty result is a real answer, not a malfunction: it
means there is no prior art here and you should proceed on the ticket alone.
Do not re-run the same query hoping for a different result."
else
  log "workshop search tool: NOT APPLIED (llm_router emitted no inline mcp-config)"
fi

# The persona paragraph. Latitude is an INSTRUCTION about the residue the ticket
# left unspecified. It is prompt text: it changes what the model is told to do,
# and the only thing downstream that reads the value is the strict-and-ambiguous
# block (rc=7). It is not a permission, and it does not sit "above" a floor that
# stops anything mid-run — `touches` is audited after the commit, not before the
# write. Do not describe either one to the model as a thing that binds it: a
# claim the model can falsify by simply doing the thing teaches it to discount
# every other line in this prompt.
LATITUDE_RULE_strict="STRICT: do what the ticket says and nothing more. Where the
ticket does not say, ASK — do not decide. Raise the question with
\`project_tasks.py set-awaiting\` (or block the task with a specific reason) and
stop; a block is a correct outcome here, not a failure."
LATITUDE_RULE_standard="STANDARD: decide REVERSIBLE things yourself and record the
choice as task evidence (\`project_tasks.py note\`). BLOCK on anything
irreversible, on anything outside the task's declared \`touches\`, and on any hard
gate."
LATITUDE_RULE_open="OPEN: pursue the goal. Decide freely and report the decisions
you made at the end, in the task evidence. Stay inside the ticket's declared
\`touches\`: nothing stops you writing outside them, but the sweep checks the
committed diff against them afterwards and BLOCKS the ticket if you strayed, so
work that wanders out of its lane is work the operator has to come back to."
LATITUDE_RULE_VAR="LATITUDE_RULE_$LATITUDE"
RALPH_INSTRUCTIONS="$RALPH_INSTRUCTIONS

## Latitude for THIS run: $LATITUDE
Latitude is what you are authorized to do WHERE THE TICKET DOES NOT SAY. Every
ticket underspecifies something; this is the standing policy for that residue.
${!LATITUDE_RULE_VAR}

REVERSIBLE means: a later \`git revert\` or a one-line edit fully undoes it, there
is no external side effect, and no third party has observed it. Adding a file is
reversible. Adding a dependency others will install, pushing a branch, sending a
message, deleting a store: NOT reversible.

What \`touches\` actually is, stated plainly because you can check it: it is an
AUDIT, not a gate. Nothing in this environment refuses a write outside the
ticket's globs. After the run, the sweep compares the committed diff against them
and blocks the ticket if anything strayed, with an evidence line naming the file.
So the cost of writing out of lane is not a refusal, it is a blocked ticket and
the operator's time. Stay in lane because that is what the ticket is about."
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
LAST_ITER_OUT=""   # path of the most recent iteration's --output-format json envelope
LAST_RESULT=""     # last NON-EMPTY worker .result text seen, so the receipt summary
                   # is never blank when the task is accepted even if the FINAL
                   # iteration exited nonzero with a partial/absent envelope (Fix 2)
# Session id captured from iteration 1's result envelope, so iterations >=2 resume
# THIS run's own session (--resume) instead of the cwd's most-recent session
# (--continue), which could grab a chat's session started in $DIR between iters
# (review F12). Empty until captured; we fall back to --continue if never set.
SESSION_ID=""
# Per-run reviewer-reason counts. We record a learning only when the SAME reason
# is rejected 2+ times in this run (a real repeated pattern), not on every raw
# per-run diagnostic (review F15).
declare -A REASON_COUNT=()
# Nominal task cost (from the usage ledger config, 150k default). Used to bill a
# timeout-killed iteration that produced no usage JSON so it is not invisible to
# admission (review F8b). Read once; fall back to the hardcoded default.
NOMINAL_TOKENS=$( (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" python3 -c "
from services import usage_ledger
print(int(usage_ledger.load_config().get('nominal_task_tokens') or 150000))
" 2>/dev/null) || echo 150000)
[[ -z "$NOMINAL_TOKENS" ]] && NOMINAL_TOKENS=150000

# In-run no-progress / thrash guards (Fix 3). The sweep-level circuit breaker only
# sees a run's terminal tally; it cannot catch thrash WITHIN one run. These stop a
# run mid-flight (exit 6 -> caller release/blocks) when it is churning without
# progress. Thresholds are deliberately conservative so a healthy multi-iteration
# task (which makes a DIFFERENT change and/or clears a reason each tick) never
# trips; all are env-overridable.
#   - RALPH_THRASH_SAME_REJECT: consecutive rejections with the SAME normalized
#     reason set that trip the guard (default 2).
#   - RALPH_THRASH_SAME_DIFF: consecutive iterations whose cumulative diff is
#     byte-identical that trip the guard (default 2).
# Tool-call-signature repetition is intentionally NOT tracked: with
# --output-format json (not stream-json) ralph sees only the final envelope, not
# individual tool calls, so a tool-call counter here could not work. We rely on the
# diff and reject-reason signals, which do not need per-tool visibility.
RALPH_THRASH_SAME_REJECT="${RALPH_THRASH_SAME_REJECT:-2}"
RALPH_THRASH_SAME_DIFF="${RALPH_THRASH_SAME_DIFF:-2}"
# Per-run cumulative-token allowance (admission-only budget cannot stop a runaway
# already in flight). Derived from the effort-scaled iteration cap so a deep task
# gets a larger allowance than a quick one; a generous factor keeps it clear of a
# normal run. Env RALPH_MAX_RUN_TOKENS overrides the derived value; 0 disables.
RALPH_RUN_TOKEN_FACTOR="${RALPH_RUN_TOKEN_FACTOR:-3}"
MAX_RUN_TOKENS="${RALPH_MAX_RUN_TOKENS:-$(( MAX_ITER * NOMINAL_TOKENS * RALPH_RUN_TOKEN_FACTOR ))}"
PREV_REJECT_SIG=""; SAME_REJECT_COUNT=0
PREV_DIFF_SIG="";   SAME_DIFF_COUNT=0

while (( iter < MAX_ITER )); do
  iter=$((iter + 1))

  # Renew the task-store lease at the TOP of each iteration, before the (possibly
  # multi-minute) claude turn below, so the reclaimer sees a fresh heartbeat well
  # inside the TTL for the whole iteration. Inert for a standalone ralph (Fix 2).
  heartbeat_task

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
    RESUME_FLAG=()
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
    # Resume THIS run's own session by id when we captured it in iter 1; only fall
    # back to --continue (cwd's most-recent session) if we never got one. (F12)
    if [[ -n "$SESSION_ID" ]]; then
      RESUME_FLAG=(--resume "$SESSION_ID")
    else
      RESUME_FLAG=(--continue)
    fi
  fi

  # Capture stdout for sentinel + classifier-block detection.
  # Per-tree AND per-pid: the dispatcher runs sweeps concurrently, so a shared
  # $WORKSHOP path let runs read each other's output (foreign sentinel = false
  # completion, cross-fired classifier block, cross-billed tokens). $DIR/.runtime
  # exists (LOG lives there). Two sequential tasks in one sweep share a pid but
  # never overlap in time, so pid+iter is unique per live run. (review F1)
  ITER_OUT=$DIR/.runtime/ralph-iter-$$-${iter}.out
  LAST_ITER_OUT="$ITER_OUT"   # remember for the receipt summary (final iter's .result)
  # --output-format json wraps the final text in `.result` (so the sentinel and
  # classifier grep -F still match verbatim) AND carries `.usage`, which is the
  # only way to meter real token spend for the usage ledger. Within-iter cost is
  # bounded by PER_ITER_TIMEOUT, not by a step count.
  TURN_CAP=()
  [ -n "$MAX_TURNS_PER_ITER" ] && TURN_CAP=(--max-turns "$MAX_TURNS_PER_ITER")
  set +e
  # </dev/null so claude doesn't wait on (and warn about) stdin — that warning
  # would otherwise be merged into ITER_OUT by 2>&1 and corrupt the JSON.
  ITER_START=$(date +%s)
  # CONFINED SPAWN. The worker's `claude -p` runs through `scoped_exec exec`, the
  # same fail-closed bwrap seam the chat path uses: it confines the process to
  # $DIR (the ticket's scope root) mounted at /workspace, or -- if the sandbox
  # cannot be built -- REFUSES (exit 3) rather than running with host filesystem
  # access. An authorized-to-write worker must be sandboxed; this is the seam that
  # guarantees it, in one place, fail-closed. `--die-with-parent` (set by the
  # exec entrypoint) ties the sandbox to this `timeout`, so a wall-budget kill
  # takes claude down with it instead of orphaning it. The inherited fds
  # (stdout -> $ITER_OUT, stdin < /dev/null) cross the namespace as already-open
  # descriptors, so the JSON capture is unaffected. PYTHONPATH lets the launcher
  # (which runs OUTSIDE the sandbox, building the argv) import services.scoped_exec.
  SCOPED_EXEC=("$WORKSHOP/backend/.venv/bin/python" -m services.scoped_exec exec --workdir "$DIR" --)
  PYTHONPATH="$WORKSHOP/backend" timeout "$PER_ITER_TIMEOUT" \
    "${SCOPED_EXEC[@]}" \
    "$CLAUDE" "${RESUME_FLAG[@]}" -p "$ITER_PROMPT" \
    --output-format json \
    "${PERM_FLAGS[@]}" \
    "${DENY_FLAGS[@]}" \
    "${MCP_FLAGS[@]}" \
    "${TURN_CAP[@]}" \
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
    # Weight cache-read tokens at 0.1: the subscription rate limits count a cache
    # read far below a fresh input token, so metering them at parity trips the
    # budget long before the real quota is spent (review L3). Fresh input, cache
    # creation, and output stay at parity.
    tok = (int(u.get('input_tokens', 0))
           + int(0.1 * int(u.get('cache_read_input_tokens', 0)))
           + int(u.get('cache_creation_input_tokens', 0)) + int(u.get('output_tokens', 0)))
except Exception:
    tok = 0
print(tok)
" 2>/dev/null || echo 0)
  RUN_TOKENS=$((RUN_TOKENS + ITER_TOKENS))

  # Capture this iteration's final assistant text (.result) and remember the last
  # NON-EMPTY one, so the receipt summary is never blank when the task is accepted
  # even if the FINAL iteration exited nonzero with a partial envelope (Fix 2).
  # Same tolerant parse ladder as the token/session parses above.
  ITER_RESULT=$(python3 -c "
import json
res = ''
try:
    txt = open('$ITER_OUT').read()
    obj = None
    try:
        obj = json.loads(txt)
    except Exception:
        i = txt.find('{\"type\"')
        if i >= 0:
            try:
                obj = json.loads(txt[i:])
            except Exception:
                obj = None
    if isinstance(obj, dict):
        res = obj.get('result') or ''
except Exception:
    res = ''
print(res[:1500])
" 2>/dev/null || echo "")
  [[ -n "$ITER_RESULT" ]] && LAST_RESULT="$ITER_RESULT"

  # Capture the session id from the result envelope once (iter 1), so iter >=2 can
  # --resume THIS run's session rather than --continue the cwd's newest one. (F12)
  # Robust to a stray non-JSON line landing in the file via 2>&1 (same ladder as
  # the token parse: whole file, then a line carrying session_id, then first obj).
  if [[ -z "$SESSION_ID" ]]; then
    SESSION_ID=$(python3 -c "
import json
sid = ''
try:
    txt = open('$ITER_OUT').read()
    obj = None
    try:
        obj = json.loads(txt)
    except Exception:
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith('{') and '\"session_id\"' in line:
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
    sid = (obj or {}).get('session_id') or ''
except Exception:
    sid = ''
print(sid)
" 2>/dev/null || echo "")
    [[ -n "$SESSION_ID" ]] && log "captured session_id=$SESSION_ID for --resume on later iters"
  fi

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

  # Bill nominal for a timeout-KILLED iteration that ran real wall time but yielded
  # no usage JSON. The most expensive failure mode (a run that burns its whole wall
  # budget then gets killed) otherwise bills 0 and stays invisible to the ledger's
  # admission gate. We are past the env-failure bail, so exit 124 + no metered
  # tokens + >60s of real runtime is a genuine long run, not an outage. (review F8b)
  if (( CLAUDE_EXIT == 124 && ITER_TOKENS == 0 && ITER_DUR > 60 )); then
    RUN_TOKENS=$((RUN_TOKENS + NOMINAL_TOKENS))
    log "iter $iter: timeout kill, no usage JSON after ${ITER_DUR}s, billing nominal $NOMINAL_TOKENS tokens (F8b)"
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
  # The reviewer is handed ONLY the tree + baseline: it judges the committed DIFF and
  # (via REVIEWER_CRITERIA_FILE, set by project_ralph.sh) the executed acceptance
  # criteria. The worker's own $ITER_RESULT/.result summary is deliberately NOT passed
  # in, so the worker cannot bias its own completion verdict (self-preference).
  # Pass the bill scope + attribution so reviewer.sh meters its OWN auxiliary
  # claude spend (if it performs an LLM review) against the worker's scope, tagged
  # role=reviewer. Inert for a standalone ralph (BILL_PROJECT empty) and for the
  # deterministic gate (no reviewer claude envelope to meter). (research rec 5)
  set +e
  REVIEWER_JSON=$(REVIEWER_ROOT="$DIR" \
    REVIEWER_BILL_SCOPE="${BILL_PROJECT:-}" \
    REVIEWER_BILL_TASK_ID="${TASK_ID:-}" \
    REVIEWER_BILL_RUN="${RUN_ID:-}" \
    "$REVIEWER" "$BASELINE" 2>>"$LOG")
  REVIEWER_EXIT=$?
  set -e
  last_reviewer_json="$REVIEWER_JSON"
  if (( REVIEWER_EXIT != 0 )); then
    log "reviewer.sh failed with exit $REVIEWER_EXIT — treating as incomplete"
    continue
  fi
  COMPLETE=$(echo "$REVIEWER_JSON" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('complete', False)))" 2>/dev/null || echo 0)
  VERDICT=$(echo "$REVIEWER_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('verdict') or 'reject')" 2>/dev/null || echo reject)
  log "iter $iter reviewer.complete=$COMPLETE verdict=$VERDICT"

  # block-for-decision: the gate would have approved but could not verify what it
  # needed to, and this run is `strict`, so the answer is "ask", not "assume".
  # Terminal for the run — another iteration re-runs the same unavailable check.
  # The caller (project_ralph.sh) reads the reason out of the receipt and blocks
  # the task with it.
  if [[ "$VERDICT" == "block-for-decision" ]]; then
    BLOCK_REASON=$(echo "$REVIEWER_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('blocked_reason') or 'latitude:strict-and-ambiguous')" 2>/dev/null || echo "latitude:strict-and-ambiguous")
    log "BLOCK-FOR-DECISION at iter $iter: $BLOCK_REASON"
    exit_code=7
    break
  fi

  # Reviewer approved: make sure the work is actually LANDED as a commit before
  # the PASS branches below hand the task to project_ralph.sh's committed-work
  # done-gate, which would otherwise release a perfectly good run back to todo.
  (( COMPLETE == 1 )) && maybe_autocommit

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

  # Reviewer rejected. A raw reviewer reason (no_diff, new_stubs:3,
  # py_typecheck_fail) is a per-run diagnostic, not a durable lesson: recording
  # every one buries the scope memory in transient near-dup facts that defeat
  # dedup and bloat the injected block (review F15). Record a learning ONLY when
  # the SAME reason is rejected 2+ times in THIS run (a genuine stuck pattern),
  # ONCE (on the 2nd occurrence), with the task text for context.
  if [[ -n "$REVIEWER_JSON" ]]; then
    RUN_REASONS=$(echo "$REVIEWER_JSON" | python3 -c "import json,sys; print('\n'.join(json.load(sys.stdin).get('reasons',[])))" 2>/dev/null || echo "")
    while IFS= read -r reason; do
      [[ -z "$reason" ]] && continue
      REASON_COUNT["$reason"]=$(( ${REASON_COUNT["$reason"]:-0} + 1 ))
      if (( ${REASON_COUNT["$reason"]} == 2 )); then
        (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" \
           LEARN_SCOPE="${LEARN_SCOPE:-}" LEARN_REASON="$reason" \
           LEARN_TASK="$(echo "$TASK" | head -c 160)" python3 -c "
import os
from services import learnings
scope = os.environ.get('LEARN_SCOPE') or learnings.GLOBAL
try:
    scope = learnings._valid_scope(scope)
except Exception:
    scope = learnings.GLOBAL
reason = os.environ.get('LEARN_REASON', '')
task = os.environ.get('LEARN_TASK', '')
try:
    learnings.record(
        f'Ralph keeps hitting \"{reason}\" on this task; the direct approach is not '
        f'converging, try a different tactic: {task}',
        source='ralph-reviewer', scope=scope, tags=['ralph', 'rejection', 'repeated'])
except Exception:
    pass
") 2>/dev/null || true
        log "recorded repeated-rejection learning (reason=$reason x${REASON_COUNT["$reason"]})"
      fi
    done <<< "$RUN_REASONS"
  fi

  # --- In-run no-progress / thrash guards (Fix 3) -------------------------------
  # Reached only on a NON-passing iteration (a passing iter already broke out with
  # exit 0 above), so a healthy run never touches this. Each guard stops the run
  # mid-flight with exit 6 so the caller release/blocks the task with a reason.

  # (a) Cumulative per-run token budget. Admission gating cannot stop a run already
  #     burning tokens; this can. RUN_TOKENS is the weighted spend accumulated above.
  if (( MAX_RUN_TOKENS > 0 && RUN_TOKENS > MAX_RUN_TOKENS )); then
    log "no-progress PARK: run token budget exceeded (RUN_TOKENS=$RUN_TOKENS > $MAX_RUN_TOKENS) at iter=$iter"
    exit_code=6
    break
  fi

  # (b) Same reviewer rejection twice (or more) in a row. Normalize reasons: drop
  #     trailing :N counts (so new_stubs:3 == new_stubs:2) and sort, so only a
  #     genuinely repeated reason set trips, not a re-ordered or re-counted one.
  REJECT_SIG=$(echo "$REVIEWER_JSON" | python3 -c "
import json, re, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
rs = sorted({re.sub(r':\d+$', '', str(r)) for r in d.get('reasons', []) if str(r).strip()})
print('|'.join(rs))
" 2>/dev/null || echo "")
  if [[ -n "$REJECT_SIG" && "$REJECT_SIG" == "$PREV_REJECT_SIG" ]]; then
    SAME_REJECT_COUNT=$((SAME_REJECT_COUNT + 1))
  else
    SAME_REJECT_COUNT=1
  fi
  PREV_REJECT_SIG="$REJECT_SIG"
  if [[ -n "$REJECT_SIG" ]] && (( SAME_REJECT_COUNT >= RALPH_THRASH_SAME_REJECT )); then
    log "no-progress PARK: same reviewer rejection x$SAME_REJECT_COUNT (reasons=$REJECT_SIG) at iter=$iter"
    exit_code=6
    break
  fi

  # (c) Near-identical diff twice (or more) in a row: the worker keeps producing the
  #     same change (byte-identical cumulative diff vs baseline) that the reviewer
  #     keeps rejecting, i.e. it is stuck. Signature = tracked+staged diff plus the
  #     untracked-file name+size set, hashed. A run making real progress changes the
  #     diff each tick, so this never trips on healthy work.
  DIFF_SIG=$( { git diff "$BASELINE" 2>/dev/null; git diff --cached "$BASELINE" 2>/dev/null; \
                git ls-files --others --exclude-standard 2>/dev/null | while IFS= read -r f; do \
                  [[ -f "$f" ]] && printf '%s %s\n' "$f" "$(wc -c < "$f" 2>/dev/null)"; done; } \
              | sha1sum | cut -d' ' -f1)
  if [[ -n "$DIFF_SIG" && "$DIFF_SIG" == "$PREV_DIFF_SIG" ]]; then
    SAME_DIFF_COUNT=$((SAME_DIFF_COUNT + 1))
  else
    SAME_DIFF_COUNT=1
  fi
  PREV_DIFF_SIG="$DIFF_SIG"
  if (( SAME_DIFF_COUNT >= RALPH_THRASH_SAME_DIFF )); then
    log "no-progress PARK: near-identical diff x$SAME_DIFF_COUNT at iter=$iter"
    exit_code=6
    break
  fi
done

if (( iter >= MAX_ITER && exit_code == 1 )); then
  log "iteration cap ($MAX_ITER) hit; final reviewer JSON: $last_reviewer_json"
fi

# Bill this run's real token spend to the usage ledger so the dispatcher's
# admission gate can enforce the rolling-window budget. Best-effort; never blocks
# the exit. Only autonomous (billed) runs record — a bare manual ralph does not.
if [[ -n "$BILL_PROJECT" && "$RUN_TOKENS" -gt 0 ]]; then
  # Attribution (research rec 3): role=worker plus the task/run ids this run knows,
  # so worker spend is distinguishable from the reviewer's auxiliary spend in the
  # same scope. task-id / run are added only when set (a standalone sweep may lack
  # a job id), keeping the record backward-compatible.
  BILL_ATTR=(--role worker)
  [[ -n "$TASK_ID" ]] && BILL_ATTR+=(--task-id "$TASK_ID")
  [[ -n "$RUN_ID" ]] && BILL_ATTR+=(--run "$RUN_ID")
  (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" python3 -m services.usage_ledger record \
     --project "$BILL_PROJECT" --tokens "$RUN_TOKENS" "${BILL_ATTR[@]}" >/dev/null 2>&1) || true
  log "billed $RUN_TOKENS tokens to usage ledger (scope=$BILL_PROJECT role=worker task=${TASK_ID:-none} run=${RUN_ID:-none})"
fi

log "=== done iter=$iter exit_code=$exit_code (tokens=$RUN_TOKENS) ==="

# Best-effort receipt: the structured work product of this run, for the parent
# (project_ralph.sh) to attach to the child Job's evidence. NEVER blocks or
# changes the exit code. `summary` is the .result text of the final iteration's
# envelope, which on a PASS is the worker's own completion account (a worklog,
# not raw chain-of-thought). Skipped entirely when --receipt was not passed.
if [[ -n "$RECEIPT_PATH" ]]; then
  ( RECEIPT_PATH="$RECEIPT_PATH" EXIT_CODE="$exit_code" ITERATIONS="$iter" \
    TOKENS="$RUN_TOKENS" SESSION_ID="$SESSION_ID" \
    REVIEWER_JSON="$last_reviewer_json" LAST_ITER_OUT="$LAST_ITER_OUT" \
    LAST_RESULT="$LAST_RESULT" \
    python3 - <<'PYEOF'
import json, os
path = os.environ["RECEIPT_PATH"]
# The final iteration's .result is the worker's own worklog summary, clipped.
summary = ""
last_out = os.environ.get("LAST_ITER_OUT", "")
if last_out and os.path.isfile(last_out):
    try:
        txt = open(last_out, encoding="utf-8", errors="replace").read()
        obj = None
        try:
            obj = json.loads(txt)
        except Exception:
            i = txt.find('{"type"')
            if i >= 0:
                try:
                    obj = json.loads(txt[i:])
                except Exception:
                    obj = None
        if isinstance(obj, dict):
            summary = (obj.get("result") or "")[:1200]
    except Exception:
        summary = ""
# Fix 2: never leave the summary blank when the task was accepted. On a
# nonzero-but-accepted final iteration the envelope may be partial (empty
# .result), so fall back to the last NON-EMPTY .result captured mid-run, then to a
# trimmed raw tail of the final output. The default PASS path (final .result
# present) keeps the exact summary it had before.
exit_code = int(os.environ.get("EXIT_CODE") or 0)
if not summary:
    summary = (os.environ.get("LAST_RESULT") or "")[:1200]
if not summary and exit_code == 0 and last_out and os.path.isfile(last_out):
    try:
        raw = open(last_out, encoding="utf-8", errors="replace").read().strip()
        if raw:
            summary = raw[-800:]
    except Exception:
        pass
try:
    reviewer = json.loads(os.environ.get("REVIEWER_JSON") or "null")
except Exception:
    reviewer = None
receipt = {
    "exit_code": int(os.environ.get("EXIT_CODE") or 0),
    "iterations": int(os.environ.get("ITERATIONS") or 0),
    "tokens": int(os.environ.get("TOKENS") or 0),
    "session_id": os.environ.get("SESSION_ID") or "",
    "reviewer": reviewer,
    "summary": summary,
}
try:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(receipt, f)
except Exception:
    pass
PYEOF
  ) 2>>"$LOG" || true
fi

exit "$exit_code"
