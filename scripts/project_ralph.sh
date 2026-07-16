#!/bin/bash
# project_ralph.sh — per-project autonomous loop (generalized Ralph).
#
# The Layer-3 "conductor" scoped to ONE project instead of the workshop root.
# Closes the long-standing gap where a project's PROJECT.md brief + its own
# TODO.md never reached the autonomous loop: here the loop runs with its cwd
# INSIDE the project tree, so the brief, AGENTS.md, and the project's task queue
# are exactly what the worker sees.
#
# For each open task in the project's structured store it fires scripts/ralph.sh
# --dir <project> (which reviews the project's OWN git repo via REVIEWER_ROOT).
# Task state is driven through scripts/project_tasks.py so .tasks.json / TODO.md
# never drift.
#
# Usage:
#   project_ralph.sh <slug> [--max-wall SEC] [--max-tasks N] [--task-id ID]
#
# --task-id runs exactly ONE task (skips next-ready selection, implies
# --max-tasks 1): a chat/UI "run now" launcher. Everything else (flock, budget
# admission, lease/fence, receipt) is identical to a normal sweep.
#
# Exit codes:
#   0  — ran (queue drained, budget hit, or nothing to do)
#   2  — project not found / autonomy off / global off-switch
#   4  — startup failure
#   6  run-now (--task-id): requested task not found in the scope
#   7  run-now (--task-id): requested task not claimable (not todo, or claimed)

set -u

WORKSHOP=/home/clawd-a/workshop
AUTH_ENV=/home/clawd-a/.config/clawdbot/auth.env
AUTONOMY_FLAG=$WORKSHOP/.runtime/AUTONOMY_DISABLED
RALPH=$WORKSHOP/scripts/ralph.sh
PTASKS=$WORKSHOP/scripts/project_tasks.py

SLUG=""
DIR_ARG=""           # --dir: sweep an explicit scope dir (agent scope / any tree)
ASSIGNEE=""          # --assignee agent:<slug>: claim only this delegate's (or unassigned) tasks
MAX_WALL=3000        # 50 min total for a project sweep
MAX_TASKS=20         # cap tasks per sweep
RALPH_MAX_WALL=1200  # per-task wall budget handed to ralph.sh
RALPH_INNER_MAX_ITER=10  # FIXED inner ralph loop cap (iterations per attempt),
                         # distinct from the per-task ATTEMPT cap below (review F13)
# Per-task EFFORT -> inner-loop cap + model tier (O6/O7-config). A task carries an
# `effort` hint (quick|standard|deep, s|m|l accepted as aliases). standard / null /
# absent keeps EXACTLY today's default (RALPH_INNER_MAX_ITER, no model override);
# quick lowers the cap + picks a fast model; deep raises the cap + picks a strong
# model. Model tiers are env-overridable and passed to ralph.sh via ANTHROPIC_MODEL
# (the claude CLI's default-model override) since ralph.sh takes no --model flag.
RALPH_ITER_QUICK="${RALPH_ITER_QUICK:-3}"
RALPH_ITER_DEEP="${RALPH_ITER_DEEP:-12}"
RALPH_MODEL_QUICK="${RALPH_MODEL_QUICK:-claude-haiku-4-5}"
RALPH_MODEL_DEEP="${RALPH_MODEL_DEEP:-claude-opus-4-8}"
DEFAULT_MAX_ATTEMPTS=3   # per-task attempt cap (sweep runs) when the task sets no max_attempts
CONCURRENT_WORKTREES_MAX=${CONCURRENT_WORKTREES_MAX:-2}  # bound on task-worktrees in flight
                         # (worktree mode only). Sequential today (see the G3 block);
                         # reserved for the deferred parallel-execution variant.
FORCE=0              # manual operator-triggered run bypasses the global off-switch
JOB_ID=""            # adopt an already-open Job (manual run) instead of creating one
TASK_ID_ARG=""       # --task-id: run exactly this one task (run-now), skip next-ready

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)       DIR_ARG="$2"; shift 2 ;;
    --assignee)  ASSIGNEE="$2"; shift 2 ;;
    --max-wall)  MAX_WALL="$2"; shift 2 ;;
    --max-tasks) MAX_TASKS="$2"; shift 2 ;;
    --job-id)    JOB_ID="$2"; shift 2 ;;
    --task-id)   TASK_ID_ARG="$2"; shift 2 ;;
    --force)     FORCE=1; shift ;;
    *)           SLUG="$1"; shift ;;
  esac
done
ASSIGNEE_FLAG=(); [[ -n "$ASSIGNEE" ]] && ASSIGNEE_FLAG=(--assignee "$ASSIGNEE")
# A run-now targets exactly one task; never let it drain the queue.
[[ -n "$TASK_ID_ARG" ]] && MAX_TASKS=1

# Job store shim (services.jobs). Run from the repo root so `backend.services`
# is importable regardless of this script's caller cwd. The run-state of a sweep
# is a Job in `running` status; we finish() it on exit. Replaces the old
# per-project .runtime/ralph-run.json.
# --pid $$ records the sweep pid so the liveness watchdog can tell a crashed
# sweep (dead pid, Job still `running`) from one genuinely in flight (2d).
job_start() {  # $1 = trigger kind ; echoes the new job id (coordinator sweep Job)
  (cd "$WORKSHOP" && python3 -m backend.services.jobs start \
    --project-slug "$SLUG" --agent-id "$ASSIGNEE" --trigger "$1" --status running --pid $$ 2>/dev/null) || true
}
job_start_child() {  # $1=task_id $2=parent_job ; echoes the child Job id
  (cd "$WORKSHOP" && python3 -m backend.services.jobs start \
    --project-slug "$SLUG" --agent-id "$ASSIGNEE" --trigger loop --status running \
    --task-id "$1" --parent-id "$2" --pid $$ 2>/dev/null) || true
}
job_finish() {  # $1=job_id $2=status $3=tasks_done $4=tasks_failed [$5=evidence-json]
  [[ -z "$1" ]] && return 0
  local ev=(); [[ -n "${5:-}" ]] && ev=(--evidence "$5")
  (cd "$WORKSHOP" && python3 -m backend.services.jobs finish \
    --id "$1" --status "$2" --tasks-done "${3:-0}" --tasks-failed "${4:-0}" "${ev[@]}" \
    >/dev/null 2>&1) || true
}
job_finish_child() {  # $1=child_job $2=status $3=exit_code [$4=evidence-json]
  [[ -z "$1" ]] && return 0
  local ev=(); [[ -n "${4:-}" ]] && ev=(--evidence "$4")
  (cd "$WORKSHOP" && python3 -m backend.services.jobs finish \
    --id "$1" --status "$2" --exit-code "${3:-0}" "${ev[@]}" >/dev/null 2>&1) || true
}

# Build the structured receipt (D3 shape) for a child Job's evidence by merging
# the ralph-written receipt file (reviewer/summary/iterations/tokens) with the
# git-derived commit/baseline/head facts collected around the run. Echoes a JSON
# object on stdout. Best-effort: a missing/corrupt receipt still yields a
# well-formed object carrying the git facts. Uses jq, which is available.
build_receipt_evidence() {  # $1=receipt_file $2=baseline $3=head $4=commits(newline-sep)
  local rf="$1" baseline="$2" head="$3" commits_blob="$4"
  # Commit shas -> JSON array (empty when no commits landed).
  local commits_json
  commits_json=$(printf '%s\n' "$commits_blob" | sed '/^$/d' | jq -R . | jq -s . 2>/dev/null)
  [[ -z "$commits_json" ]] && commits_json='[]'
  # The ralph-written receipt, if it parses as JSON; else an empty object so the
  # git-derived facts still produce a well-formed evidence record.
  local base='{}'
  if [[ -f "$rf" ]] && jq empty "$rf" >/dev/null 2>&1; then
    base=$(cat "$rf")
  fi
  # The "files changed" the operator should see is the COMMITTED diff, not the
  # reviewer's working-tree status (which is full of runtime churn: iter outputs,
  # the task store, its lock, the regenerated TODO.md). Prefer baseline..head when
  # commits landed; fall back to the reviewer list when nothing was committed (a
  # failed run still shows what it touched). Filter bookkeeping paths in both.
  local ignore='^(\.runtime/|\.tasks\.json|TODO\.md$)'
  local files_json
  if [[ -n "$(printf '%s' "$commits_blob" | tr -d '[:space:]')" ]]; then
    files_json=$(cd "$WORK_DIR" && git diff --name-only "$baseline".."$head" 2>/dev/null \
      | grep -vE "$ignore" | jq -R . | jq -s . 2>/dev/null)
  else
    files_json=$(printf '%s' "$base" | jq -c "(.reviewer.files // []) | map(select(test(\"$ignore\") | not))" 2>/dev/null)
  fi
  [[ -z "$files_json" ]] && files_json='[]'
  # Merge: files/diff_files from the committed diff (above); typecheck/design from
  # the reviewer sub-object; iterations/tokens/summary from the receipt top level;
  # commits/baseline/head from git.
  jq -n \
    --argjson receipt "$base" \
    --arg baseline "$baseline" \
    --arg head "$head" \
    --argjson commits "$commits_json" \
    --argjson files "$files_json" \
    '($receipt.reviewer // {}) as $r
     | {
         kind: "receipt",
         files: $files,
         commits: $commits,
         baseline: $baseline,
         head: $head,
         diff_files: ($files | length),
         typecheck: ($r.typecheck // {}),
         design: ($r.design // ""),
         iterations: ($receipt.iterations // 0),
         tokens: ($receipt.tokens // 0),
         summary: ($receipt.summary // "")
       }' 2>/dev/null
}

# --- GitHub worktree orchestration (Phase G2/G3) shells --------------------
# These only ever run on the opt-in worktree path (bound project + the record's
# `concurrent_worktrees` flag). They mirror how the sweep already shells into
# backend services: run from $WORKSHOP with the backend on PYTHONPATH so
# `from services import worktrees` resolves the same module the API uses.

# Allocate an isolated, locked worktree for one task. Echoes "<dir>\t<branch_slug>"
# on success, nothing on failure (caller falls back to the project dir).
alloc_task_worktree() {  # $1=task_id
  (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" python3 - "$SLUG" "$1" <<'PYEOF' 2>>"$LOG"
import sys
from services import worktrees
try:
    r = worktrees.allocate_task_worktree(sys.argv[1], sys.argv[2])
    print(f"{r['dir']}\t{r['branch_slug']}")
except Exception as e:
    print(f"[alloc_task_worktree] {e}", file=sys.stderr)
PYEOF
)
}

# Push a task worktree's branch and open its PR against the canonical base.
# Echoes exactly one of:
#   conflict                 -> rebase-onto-base hit a conflict; caller parks
#   ok\t<pr_url>\t<pr_number> -> published
#   error: <msg>             -> transient/other failure; caller logs, still done
publish_task_worktree() {  # $1=branch_slug $2=title $3=body
  (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" python3 - "$SLUG" "$1" "$2" "$3" <<'PYEOF' 2>>"$LOG"
import sys
from services import worktrees
slug, branch_slug, title, body = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    r = worktrees.publish_branch(slug, branch_slug, title=title, body=body)
except Exception as e:
    print(f"error: {e}")
    raise SystemExit(0)
if not r.get("ok"):
    if r.get("conflict"):
        print("conflict")
    else:
        print("error: " + str(r.get("detail") or r.get("reason") or "publish failed"))
    raise SystemExit(0)
pr = r.get("pr") or {}
print(f"ok\t{pr.get('url') or ''}\t{pr.get('number') or ''}")
PYEOF
)
}

# Reap worktrees whose branch already merged into the canonical base. Best-effort.
cleanup_merged_worktrees() {
  (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" python3 - "$SLUG" <<'PYEOF' >/dev/null 2>>"$LOG"
import sys
from services import worktrees
try:
    worktrees.cleanup_merged(sys.argv[1])
except Exception as e:
    print(f"[cleanup_merged] {e}", file=sys.stderr)
PYEOF
) || true
}

# Called on a task's SUCCESS leg in worktree mode: push + open PR. On a rebase
# conflict it PARKS the task (dead-letter, does not loop) and returns 1 so the
# caller SKIPS its normal done/in_review transition. Otherwise augments $EVIDENCE
# with the branch + pr and returns 0 so the caller proceeds. On the default path
# (WT_MODE!=1) it is a no-op returning 0, so behavior there is unchanged.
publish_or_park() {  # $1=branch_slug $2=title $3=body
  [[ "$WT_MODE" != "1" ]] && return 0
  local out; out=$(publish_task_worktree "$1" "$2" "$3")
  if [[ "$out" == "conflict" ]]; then
    python3 "$PTASKS" note "$DIR" "$TASK_ID" "publish conflict: branch does not rebase cleanly onto the canonical base; parked (not looping)" --kind acceptance >/dev/null 2>>"$LOG"
    python3 "$PTASKS" set-status "$DIR" "$TASK_ID" parked --expect-claim-token "${CLAIM_TOKEN:-}" >/dev/null 2>>"$LOG"
    job_finish_child "$CHILD_JOB" failed "$RC" "$EVIDENCE"
    PARKED=$((PARKED+1))
    log "task=$TASK_ID PARKED (publish conflict)"
    return 1
  fi
  if [[ "$out" == ok* ]]; then
    local url num; url=$(printf '%s' "$out" | cut -f2); num=$(printf '%s' "$out" | cut -f3)
    local aug; aug=$(printf '%s' "$EVIDENCE" | jq -c \
      --arg branch "wt/$SLUG/$1" --arg url "$url" --arg num "$num" \
      '. + {branch: $branch, pr: {url: $url, number: $num}}' 2>/dev/null)
    [[ -n "$aug" ]] && EVIDENCE="$aug"
    log "task=$TASK_ID published branch wt/$SLUG/$1 pr=$url"
  else
    log "task=$TASK_ID publish did not open a PR: ${out#error: }"
  fi
  return 0
}
# ---------------------------------------------------------------------------

if [[ -z "$SLUG" && -z "$DIR_ARG" ]]; then
  echo "usage: project_ralph.sh <slug> | --dir <path> [--assignee agent:<slug>] [--max-wall SEC] [--max-tasks N]" >&2
  exit 4
fi

# Pull a single field out of a task JSON blob (from project_tasks.py). Nested
# fields use dotted paths (e.g. acceptance.kind); missing -> empty string.
jf() {  # $1=json $2=dotted-key
  echo "$1" | python3 -c "
import json, sys
try: d = json.load(sys.stdin)
except Exception: d = {}
cur = d
for k in sys.argv[1].split('.'):
    cur = cur.get(k) if isinstance(cur, dict) else None
    if cur is None: break
print('' if cur is None else cur)
" "$2" 2>/dev/null
}

# Run a task's command-acceptance spec inside the scope dir. Echoes PASS/FAIL and
# the tail of output; return code mirrors the command's.
run_acceptance_cmd() {  # $1=spec
  local out rc
  out=$( (cd "$WORK_DIR" && bash -lc "$1") 2>&1 )
  rc=$?
  echo "$out" | tail -c 800
  return $rc
}

# Subscription auth; defend against accidental API-key auth.
if [[ -r "$AUTH_ENV" ]]; then
  set -a; . "$AUTH_ENV"; set +a
fi
unset ANTHROPIC_API_KEY

# A --dir sweep targets an explicit scope tree (agent scope, or any tree) and
# trusts the caller's decision to run it (mode gate does not apply). Otherwise
# resolve the project dir + autonomy mode from the registry.
if [[ -n "$DIR_ARG" ]]; then
  DIR="$DIR_ARG"; MODE="on"
else
read -r DIR MODE < <(python3 - "$SLUG" <<'PYEOF'
import json, sys
from pathlib import Path
slug = sys.argv[1]
import os
reg = Path(os.environ.get("WORKSHOP_PROJECTS_REGISTRY",
                          "/home/clawd-a/workshop/.runtime/projects.json"))
try:
    data = json.loads(reg.read_text())
except Exception:
    print(" ")
    raise SystemExit(0)
p = (data.get("projects") or {}).get(slug)
if not p:
    print(" ")
    raise SystemExit(0)
mode = ((p.get("autonomy") or {}).get("mode")) or "off"
print(f"{p.get('dir','')} {mode}")
PYEOF
)
fi

if [[ -z "$DIR" || ! -d "$DIR" ]]; then
  echo "[project_ralph] project not found or no dir: ${SLUG:-$DIR_ARG}" >&2
  exit 2
fi

# GitHub binding (Phase G2/G3). A project bound to a remote carries a `repo`
# field (with a tracked default_branch) and an opt-in `concurrent_worktrees`
# flag. REPO_BOUND drives fetch-before-work; WT_ENABLED (bound AND flag true,
# and only for a slug-resolved project) drives the worktree-per-task path. An
# unbound project, a --dir sweep, or the flag left false keeps the single-DIR
# behavior below unchanged. WORK_DIR is the tree the current task actually runs
# in: the project DIR by default, an isolated worktree only in WT mode.
REPO_BOUND=0
DEFAULT_BRANCH=""
CONCURRENT_WT=0
if [[ -n "$SLUG" ]]; then
  read -r REPO_BOUND DEFAULT_BRANCH CONCURRENT_WT < <(python3 - "$SLUG" <<'PYEOF'
import json, os, sys
from pathlib import Path
slug = sys.argv[1]
reg = Path(os.environ.get("WORKSHOP_PROJECTS_REGISTRY",
                          "/home/clawd-a/workshop/.runtime/projects.json"))
try:
    data = json.loads(reg.read_text())
except Exception:
    print("0 - 0"); raise SystemExit(0)
p = (data.get("projects") or {}).get(slug) or {}
repo = p.get("repo") or {}
bound = 1 if repo.get("remote_url") else 0
db = repo.get("default_branch") or "-"
conc = 1 if p.get("concurrent_worktrees") else 0
print(f"{bound} {db} {conc}")
PYEOF
)
fi
[[ "$DEFAULT_BRANCH" == "-" ]] && DEFAULT_BRANCH=""
WT_ENABLED=0
[[ "$REPO_BOUND" == "1" && "$CONCURRENT_WT" == "1" && -n "$SLUG" ]] && WT_ENABLED=1
WORK_DIR="$DIR"

RUNTIME="$DIR/.runtime"
mkdir -p "$RUNTIME"
LOG="$RUNTIME/ralph.log"

ts() { date -Iseconds; }
log() { echo "[$(ts)] [project_ralph] $*" >> "$LOG"; echo "[project_ralph] $*"; }

# Global off-switch (same flag the workshop cron honors). A scheduled/continuous
# sweep obeys it; a manual operator-triggered run (--force) is a deliberate
# foreground action and bypasses it, along with the per-project mode gate below.
if [[ -f "$AUTONOMY_FLAG" && "$FORCE" -ne 1 ]]; then
  log "global autonomy off-switch present; not running"
  job_finish "$JOB_ID" cancelled 0 0
  exit 2
fi

# Per-project mode gate. A background (non-force) sweep only runs for a project
# whose mode is not "off". A manual --force run is always allowed (an operator
# "run now" is a deliberate action; "off = manual only").
if [[ "$MODE" == "off" && "$FORCE" -ne 1 ]]; then
  log "project autonomy is off for $SLUG; not running"
  job_finish "$JOB_ID" cancelled 0 0
  exit 2
fi

# One sweep per TREE at a time. Key the lock on the resolved DIR, not the slug:
# an agent sweep (empty slug) and the project sweep it is linked to resolve to
# the SAME tree but different slugs, so a slug lock let both run and collide on
# the inner per-tree ralph lock (the loser exits 4 and burns a task attempt).
# A DIR-keyed lock makes any two sweeps on one tree serialize at the sweep level
# (clean skip, no claim, no attempt burned). (review F17)
LOCK=/tmp/workshop-project-ralph-$(echo "$DIR" | md5sum | cut -c1-12).lock
exec 8>"$LOCK"
if ! flock -n 8; then
  log "another sweep already running against $DIR; aborting"
  job_finish "$JOB_ID" cancelled 0 0
  exit 4
fi

# Adopt the caller's Job (manual run created one already) or open a loop Job for
# this self-launched sweep. This is the run-state the backend reports.
if [[ -z "$JOB_ID" ]]; then
  JOB_ID=$(job_start loop)
fi

# The scope tokens are billed to in the usage ledger: the project slug for a
# project sweep, else the agent assignee, else the tree name. The dispatcher and
# this loop both admit against this scope's rolling-window budget.
BILL_SCOPE="${SLUG:-$ASSIGNEE}"; [[ -z "$BILL_SCOPE" ]] && BILL_SCOPE="$(basename "$DIR")"

START_EPOCH=$(date +%s)
log "=== sweep start slug=$SLUG dir=$DIR mode=$MODE job=$JOB_ID scope=$BILL_SCOPE max_wall=${MAX_WALL}s max_tasks=$MAX_TASKS bound=$REPO_BOUND worktrees=$WT_ENABLED ==="

# The canonical branch for a bound project: the tracked default_branch, else the
# same main/master/HEAD detection worktrees.py falls back to. Used by
# fetch-before-work and worktree allocation.
resolve_default_branch() {
  if [[ -n "$DEFAULT_BRANCH" ]]; then echo "$DEFAULT_BRANCH"; return; fi
  local c
  for c in main master; do
    if git -C "$DIR" rev-parse --verify "$c" >/dev/null 2>&1; then echo "$c"; return; fi
  done
  git -C "$DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main
}

# Fetch-before-work (Phase G2): a bound project starts its sweep from the
# remote's latest canonical branch. fetch + ff-only never rewrites local work,
# so this is safe on the normal single-DIR path too; it is skipped silently for
# unbound projects and on any failure (e.g. the local branch has diverged).
if [[ "$REPO_BOUND" == "1" ]]; then
  DB=$(resolve_default_branch)
  if git -C "$DIR" fetch origin "$DB" >/dev/null 2>>"$LOG"; then
    if git -C "$DIR" merge --ff-only "origin/$DB" >/dev/null 2>>"$LOG"; then
      log "fetch-before-work: fast-forwarded to origin/$DB"
    else
      log "fetch-before-work: origin/$DB fetched; ff-only skipped (local diverged)"
    fi
  else
    log "fetch-before-work: fetch origin/$DB failed; continuing from local state"
  fi
fi

DONE=0; FAILED=0; BLOCKED=0; TIMEOUT=0; IN_REVIEW=0; PARKED=0
tick_remaining() { echo $(( MAX_WALL - ( $(date +%s) - START_EPOCH ) )); }

# After a failed / incomplete attempt, decide retry (release to todo) vs give-up
# (block), based on the per-task attempt cap. $ATTEMPTS is the count already spent
# (claim bumps it before the run). Echoes "blocked" or "retry".
release_or_block() {  # $1=task_id $2=attempts $3=cap $4=reason-verb
  # $CLAIM_TOKEN (loop-scoped) fences this finalize: if a liveness reclaim already
  # released + reassigned the task to a newer sweep, the token mismatches and the
  # transition is a no-op instead of clobbering live work.
  if (( $2 >= $3 )); then
    python3 "$PTASKS" block "$DIR" "$1" "$4 after $2 attempt(s) (cap $3)" \
      --expect-claim-token "${CLAIM_TOKEN:-}" >/dev/null 2>>"$LOG"
    log "task=$1 BLOCKED: $4 after $2/$3 attempts"
    echo blocked
  else
    python3 "$PTASKS" set-status "$DIR" "$1" todo \
      --expect-claim-token "${CLAIM_TOKEN:-}" >/dev/null 2>>"$LOG"
    log "task=$1 released to todo ($4; attempt $2/$3)"
    echo retry
  fi
}

n=0
while (( n < MAX_TASKS )); do
  REMAIN=$(tick_remaining)
  if (( REMAIN < RALPH_MAX_WALL + 30 )); then
    log "budget remaining ${REMAIN}s < per-task ${RALPH_MAX_WALL}s — stopping"
    break
  fi

  # Task selection. A normal sweep asks for the dependency-aware next ready task.
  # A run-now (--task-id) targets exactly that id: read its record, require it to
  # be `todo` (anything else means another sweep owns it or it is not runnable),
  # and let the atomic claim below be the final authority. Both reconcile first.
  if [[ -n "$TASK_ID_ARG" ]]; then
    NEXT=$(python3 "$PTASKS" list-json "$DIR" --include-done 2>>"$LOG" \
             | jq -c --arg id "$TASK_ID_ARG" 'map(select(.id==$id)) | .[0] // empty' 2>>"$LOG")
    if [[ -z "$NEXT" ]]; then
      log "run-now: task $TASK_ID_ARG not found in $DIR"
      job_finish "$JOB_ID" cancelled "$DONE" "$FAILED"
      exit 6
    fi
    RUN_NOW_STATUS=$(jf "$NEXT" status)
    if [[ "$RUN_NOW_STATUS" != "todo" ]]; then
      log "run-now: task $TASK_ID_ARG not claimable (status=$RUN_NOW_STATUS)"
      job_finish "$JOB_ID" cancelled "$DONE" "$FAILED"
      exit 7
    fi
  else
    # Dependency-aware, assignee-scoped next task (reconciles the store first).
    NEXT=$(python3 "$PTASKS" next-ready "$DIR" "${ASSIGNEE_FLAG[@]}" 2>>"$LOG")
  fi
  TASK_ID=$(jf "$NEXT" id)
  TASK_TEXT=$(jf "$NEXT" text)
  if [[ -z "$TASK_ID" ]]; then
    log "no ready tasks"
    break
  fi

  # Usage-budget admission (Stage 5). Stop the sweep the moment this scope's
  # rolling-window budget is exhausted, so autonomous work can never run away
  # with the operator's Claude usage. Chats are unmetered and never gated.
  if ! (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" python3 -m services.usage_ledger \
          admit --project "$BILL_SCOPE" >/dev/null 2>&1); then
    log "usage budget exhausted for scope=$BILL_SCOPE — stopping sweep (task=$TASK_ID left ready)"
    break
  fi
  n=$((n+1))

  # Acceptance gate + per-task cap (O4). acceptance.kind: reviewer (default,
  # enforced inside ralph.sh) | command (run the spec after ralph passes) |
  # operator (worker finishing lands the task in_review, operator approves).
  ACC_KIND=$(jf "$NEXT" acceptance.kind)
  ACC_SPEC=$(jf "$NEXT" acceptance.spec)
  # Per-task ATTEMPT cap (how many sweep runs this task gets before block/park).
  # Distinct from ralph's inner iteration cap (RALPH_INNER_MAX_ITER), which is a
  # fixed per-attempt loop bound passed to ralph.sh below. (review F13)
  MAX_ATTEMPTS=$(jf "$NEXT" max_attempts)
  [[ -z "$MAX_ATTEMPTS" || "$MAX_ATTEMPTS" == "None" ]] && MAX_ATTEMPTS=$DEFAULT_MAX_ATTEMPTS

  # Per-task child Job under the coordinator sweep Job (task_id + parent_id link
  # the Jobs feed and the task's history into one graph).
  CHILD_JOB=$(job_start_child "$TASK_ID" "$JOB_ID")
  # Atomic claim: todo->doing recording the child job id + bumping attempts, and
  # setting the lease/pid/fencing-token (self-healing 2d). --pid $$ lets the
  # liveness reclaim tell a crashed sweep from a live one. A concurrent sweep or
  # CLI that already claimed it returns {}; skip.
  CLAIMED=$(python3 "$PTASKS" claim "$DIR" "$TASK_ID" "$CHILD_JOB" --pid $$ "${ASSIGNEE_FLAG[@]}" 2>>"$LOG")
  CLAIMED_ID=$(jf "$CLAIMED" id)
  if [[ -z "$CLAIMED_ID" ]]; then
    log "task=$TASK_ID already claimed elsewhere, skipping"
    job_finish_child "$CHILD_JOB" cancelled 0
    # A run-now can only mean this exact task; if it was claimed out from under us
    # between the precheck and here, report not-claimable rather than looping.
    if [[ -n "$TASK_ID_ARG" ]]; then
      job_finish "$JOB_ID" cancelled "$DONE" "$FAILED"
      exit 7
    fi
    continue
  fi
  ATTEMPTS=$(jf "$CLAIMED" attempts); [[ -z "$ATTEMPTS" ]] && ATTEMPTS=1
  # Fencing token from this claim; passed to every finalize so a zombie can't
  # clobber a task that a liveness reclaim reassigned to a newer sweep.
  CLAIM_TOKEN=$(jf "$CLAIMED" claim_token)

  # Worktree-per-task (Phase G3): opt-in, bound projects only. Run this task in
  # its own isolated worktree off origin/<default_branch> instead of the shared
  # project DIR, so tasks never contend on the index and each carries its own
  # branch + PR. WORK_DIR is the tree the worker (and the receipt/diff/acceptance
  # below) operates in. If allocation fails we degrade to the project DIR for
  # this one task (still correct, just no isolation/PR) rather than looping.
  # Execution is SEQUENTIAL (one worktree at a time); parallel launch is deferred.
  WORK_DIR="$DIR"; WT_MODE=0; WT_BRANCH_SLUG=""
  PR_TITLE=""; PR_BODY=""
  if [[ "$WT_ENABLED" == "1" ]]; then
    ALLOC=$(alloc_task_worktree "$TASK_ID")
    WT_DIR=$(printf '%s' "$ALLOC" | cut -f1)
    WT_BRANCH_SLUG=$(printf '%s' "$ALLOC" | cut -f2)
    if [[ -n "$WT_DIR" && -d "$WT_DIR" ]]; then
      WORK_DIR="$WT_DIR"; WT_MODE=1
      PR_TITLE="$(echo "$TASK_TEXT" | head -c 72 | tr '\n' ' ')"
      PR_BODY="Automated change for task $TASK_ID in project $SLUG."
      log "task=$TASK_ID allocated worktree $WT_DIR (branch wt/$SLUG/$WT_BRANCH_SLUG)"
    else
      log "task=$TASK_ID worktree allocation failed; running in project dir (no isolation, no PR)"
    fi
  fi

  BASELINE=$(cd "$WORK_DIR" && git rev-parse HEAD 2>/dev/null || echo "")
  log "task=$TASK_ID job=$CHILD_JOB attempt=$ATTEMPTS/$MAX_ATTEMPTS acceptance=${ACC_KIND:-reviewer} wt=$WT_MODE dir=$WORK_DIR \"$(echo "$TASK_TEXT" | head -c 120 | tr '\n' ' ')\" baseline=$BASELINE"

  # A command/operator-acceptance task's real gate is the acceptance command or
  # operator review, so ralph's reviewer must not fail it on an empty tracked-file
  # diff (a "restart the analyzer" / "investigate and write findings" task produces
  # none). Forward REVIEWER_ALLOW_NO_DIFF to ralph -> reviewer for those kinds only;
  # a reviewer-acceptance code task never gets it, so its no_diff gate is unchanged.
  # (review F18)
  ALLOW_NO_DIFF=0
  [[ "$ACC_KIND" == "command" || "$ACC_KIND" == "operator" ]] && ALLOW_NO_DIFF=1

  # Per-task effort -> inner-loop cap + model tier. Absent/standard = today's
  # default (RALPH_INNER_MAX_ITER, no model override); quick/deep adjust both.
  EFFORT=$(jf "$NEXT" effort)
  EFF_MAX_ITER="$RALPH_INNER_MAX_ITER"
  EFF_MODEL=""
  case "$EFFORT" in
    quick|q|s) EFF_MAX_ITER="$RALPH_ITER_QUICK"; EFF_MODEL="$RALPH_MODEL_QUICK" ;;
    deep|d|l)  EFF_MAX_ITER="$RALPH_ITER_DEEP";  EFF_MODEL="$RALPH_MODEL_DEEP" ;;
    *)         : ;;  # standard | m | null | absent -> defaults, unchanged
  esac
  [[ -n "$EFF_MODEL" ]] && log "task=$TASK_ID effort=$EFFORT -> max-iter=$EFF_MAX_ITER model=$EFF_MODEL"

  # ralph writes its structured receipt (reviewer/summary/iterations/tokens) here;
  # we merge it with the git facts below and attach it to the child Job's evidence.
  # pid+job-unique so concurrent sweeps never collide on the transport file.
  RECEIPT_FILE="$RUNTIME/receipt-$CHILD_JOB-$$.json"

  # A deep/quick task overrides the model tier via ANTHROPIC_MODEL (ralph.sh runs
  # `claude -p`, which honors it as the default-model override). Set only for the
  # non-default efforts so a standard/null task's environment is untouched.
  [[ -n "$EFF_MODEL" ]] && export ANTHROPIC_MODEL="$EFF_MODEL"
  set +e
  REVIEWER_ALLOW_NO_DIFF="$ALLOW_NO_DIFF" "$RALPH" "$TASK_TEXT" --dir "$WORK_DIR" --baseline-rev "$BASELINE" --max-iter "$EFF_MAX_ITER" --max-wall "$RALPH_MAX_WALL" --bill-project "$BILL_SCOPE" --receipt "$RECEIPT_FILE" >> "$LOG" 2>&1
  RC=$?
  set -e
  [[ -n "$EFF_MODEL" ]] && unset ANTHROPIC_MODEL

  # Structured receipt for the child Job's evidence: git-derived commits/head vs
  # the pre-run BASELINE, merged with ralph's receipt file. Attached on ALL
  # terminal child paths below (a failed run's receipt explains the failure).
  HEAD=$(cd "$WORK_DIR" && git rev-parse HEAD 2>/dev/null || echo "")
  COMMITS=$(cd "$WORK_DIR" && git rev-list --reverse "$BASELINE..$HEAD" 2>/dev/null || echo "")
  EVIDENCE=$(build_receipt_evidence "$RECEIPT_FILE" "$BASELINE" "$HEAD" "$COMMITS")
  # One task-side evidence entry pointing at the child Job (the ticket timeline
  # links to the run rather than duplicating the receipt payload).
  note_receipt_ref() { python3 "$PTASKS" note "$DIR" "$TASK_ID" "$CHILD_JOB" --kind receipt --ref "$CHILD_JOB" >/dev/null 2>>"$LOG"; }

  case "$RC" in
    0)
      # ralph's reviewer gate passed. Apply the task's own acceptance policy.
      # In worktree mode each SUCCESS leg first publishes the task's branch
      # (push + PR). publish_or_park is a no-op returning 0 on the default path,
      # so the transitions below are unchanged there; in worktree mode a publish
      # conflict parks the task and returns non-zero, skipping the done/in_review
      # transition (park, do not loop). On publish success it augments $EVIDENCE
      # with the branch + pr so the child Job receipt carries them.
      if [[ "$ACC_KIND" == "command" && -n "$ACC_SPEC" ]]; then
        set +e; ACC_OUT=$(run_acceptance_cmd "$ACC_SPEC"); ACC_RC=$?; set -e
        if (( ACC_RC == 0 )); then
          if publish_or_park "$WT_BRANCH_SLUG" "$PR_TITLE" "$PR_BODY"; then
            python3 "$PTASKS" note "$DIR" "$TASK_ID" "command acceptance passed: $ACC_SPEC" --kind acceptance >/dev/null 2>>"$LOG"
            python3 "$PTASKS" set-status "$DIR" "$TASK_ID" done --job-id "$CHILD_JOB" --expect-claim-token "${CLAIM_TOKEN:-}" >/dev/null 2>>"$LOG"
            note_receipt_ref
            DONE=$((DONE+1)); job_finish_child "$CHILD_JOB" done "$RC" "$EVIDENCE"; log "task=$TASK_ID DONE (command acceptance ok)"
          fi
        else
          python3 "$PTASKS" note "$DIR" "$TASK_ID" "command acceptance FAILED (exit $ACC_RC): $ACC_SPEC :: $(echo "$ACC_OUT" | tr '\n' ' ' | head -c 300)" --kind acceptance >/dev/null 2>>"$LOG"
          if [[ "$(release_or_block "$TASK_ID" "$ATTEMPTS" "$MAX_ATTEMPTS" "command acceptance failing")" == "blocked" ]]; then BLOCKED=$((BLOCKED+1)); else FAILED=$((FAILED+1)); fi
          job_finish_child "$CHILD_JOB" failed "$ACC_RC" "$EVIDENCE"
        fi
      elif [[ "$ACC_KIND" == "operator" ]]; then
        if publish_or_park "$WT_BRANCH_SLUG" "$PR_TITLE" "$PR_BODY"; then
          python3 "$PTASKS" note "$DIR" "$TASK_ID" "worker completed; awaiting operator approval" --kind acceptance >/dev/null 2>>"$LOG"
          python3 "$PTASKS" set-status "$DIR" "$TASK_ID" in_review --job-id "$CHILD_JOB" --expect-claim-token "${CLAIM_TOKEN:-}" >/dev/null 2>>"$LOG"
          note_receipt_ref
          IN_REVIEW=$((IN_REVIEW+1)); job_finish_child "$CHILD_JOB" done "$RC" "$EVIDENCE"; log "task=$TASK_ID -> in_review (operator gate)"
        fi
      else
        if publish_or_park "$WT_BRANCH_SLUG" "$PR_TITLE" "$PR_BODY"; then
          python3 "$PTASKS" set-status "$DIR" "$TASK_ID" done --job-id "$CHILD_JOB" --expect-claim-token "${CLAIM_TOKEN:-}" >/dev/null 2>>"$LOG"
          note_receipt_ref
          DONE=$((DONE+1)); job_finish_child "$CHILD_JOB" done "$RC" "$EVIDENCE"; log "task=$TASK_ID DONE"
        fi
      fi
      ;;
    3) python3 "$PTASKS" block "$DIR" "$TASK_ID" "classifier block" --expect-claim-token "${CLAIM_TOKEN:-}" >/dev/null 2>>"$LOG"; BLOCKED=$((BLOCKED+1)); job_finish_child "$CHILD_JOB" failed "$RC" "$EVIDENCE"; log "task=$TASK_ID BLOCKED (classifier)" ;;
    5)
      # Environment failure (API outage / auth), NOT task failure: give the
      # attempt back and STOP the sweep so we do not hammer a down API. The sweep
      # still tallies a failure so the circuit breaker cools this scope off for a
      # window (it keys on tasks_failed). (review F4)
      python3 "$PTASKS" note "$DIR" "$TASK_ID" "environment failure (API/auth); attempt restored, sweep backing off" --kind acceptance >/dev/null 2>>"$LOG"
      python3 "$PTASKS" set-status "$DIR" "$TASK_ID" todo --restore-attempt --expect-claim-token "${CLAIM_TOKEN:-}" >/dev/null 2>>"$LOG"
      job_finish_child "$CHILD_JOB" failed "$RC" "$EVIDENCE"; FAILED=$((FAILED+1))
      log "task=$TASK_ID ENV FAILURE: released (attempt restored); stopping sweep to back off"
      rm -f "$RECEIPT_FILE" 2>/dev/null || true
      break ;;
    1|2)
      # rc=1 (iteration cap) / rc=2 (wall cap): the run did NOT converge. Finish
      # the child FAILED and count it in tasks_failed so a pure-timeout thrash loop
      # is visible to the circuit breaker, which keys on tasks_failed. (review F14)
      if [[ "$(release_or_block "$TASK_ID" "$ATTEMPTS" "$MAX_ATTEMPTS" "incomplete (rc=$RC)")" == "blocked" ]]; then BLOCKED=$((BLOCKED+1)); else FAILED=$((FAILED+1)); fi
      job_finish_child "$CHILD_JOB" failed "$RC" "$EVIDENCE" ;;
    *)
      if [[ "$(release_or_block "$TASK_ID" "$ATTEMPTS" "$MAX_ATTEMPTS" "failed (rc=$RC)")" == "blocked" ]]; then BLOCKED=$((BLOCKED+1)); else FAILED=$((FAILED+1)); fi
      job_finish_child "$CHILD_JOB" failed "$RC" "$EVIDENCE" ;;
  esac
  # Receipt file is transport, not storage: attached to the Job above, drop it.
  rm -f "$RECEIPT_FILE" 2>/dev/null || true
  sleep 1
done

# Worktree mode (Phase G3): reap worktrees whose branch already merged into the
# canonical base so branches/<slug>/ does not accumulate stale trees. Best-effort.
if [[ "$WT_ENABLED" == "1" ]]; then
  cleanup_merged_worktrees
  log "cleanup: reaped merged task worktrees for $SLUG"
fi

DURATION=$(( $(date +%s) - START_EPOCH ))
# Terminal status of the coordinator sweep. If open (non-terminal) tasks remain
# and NONE are ready (every one is blocked / in_review), the sweep is waiting on
# the operator: finish awaiting_input with evidence. Home's AttentionStrip + Job
# detail surface it; the dispatcher skips an unchanged awaiting scope next tick.
OUT=$(cd "$WORKSHOP" && python3 - "$DIR" "$ASSIGNEE" "${SLUG:-$DIR}" <<'PYEOF'
import json, sys
from pathlib import Path
sys.path.insert(0, "/home/clawd-a/workshop/backend")
from services import tasks
root = Path(sys.argv[1]); assignee = sys.argv[2] or None; scope = sys.argv[3]
ready = tasks.next_ready(root, assignee=assignee)
open_tasks = tasks.list_tasks(root, include_done=False)
waiting = [t for t in open_tasks if t["status"] in ("blocked", "in_review")]

def question_for(t: dict) -> str:
    if t["status"] == "in_review":
        return "Worker finished this task. Review the change and approve it, or send it back with a note."
    reason = (t.get("blocked_reason") or "").strip()
    return f"Blocked: {reason}" if reason else "Blocked; needs your input to proceed."

# Write the per-task awaiting payload so the task carries its own question (the
# store is the source of truth; JobDetail + TaskPanel read it from there).
detail = []
for t in waiting:
    q = question_for(t)
    tasks.update(root, t["id"], awaiting={"question": q})
    detail.append({"id": t["id"], "status": t["status"], "text": (t.get("text") or "").split("\n")[0][:140],
                   "blocked_reason": t.get("blocked_reason"), "question": q})
print(json.dumps({"ready": bool(ready), "open": len(open_tasks), "waiting": len(waiting),
                  "scope": scope, "detail": detail[:20]}))
PYEOF
)
READY=$(echo "$OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('ready'))" 2>/dev/null)
OPEN=$(echo "$OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('open',0))" 2>/dev/null)
WAITING=$(echo "$OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('waiting',0))" 2>/dev/null)
log "=== sweep end scope=${SLUG:-$DIR} done=$DONE blocked=$BLOCKED failed=$FAILED parked=$PARKED in_review=$IN_REVIEW open=${OPEN:-0} waiting=${WAITING:-0} (${DURATION}s) ==="
# Only park as awaiting_input when the sweep is BOUND to a resolvable scope
# (a project slug or an assignee agent). An unbound sweep (--dir with no slug/
# assignee) has no scope the Jobs API can act on, so an awaiting_input job for it
# is a permanent, unresolvable operator alert. Finish it `done` instead; the
# always-available Dismiss stays as the escape hatch for any that slip through.
if [[ "$READY" == "False" && "${OPEN:-0}" -gt 0 && "${OPEN:-0}" == "${WAITING:-0}" && ( -n "$SLUG" || -n "$ASSIGNEE" ) ]]; then
  EV=$(echo "$OUT" | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin).get('detail',[])))" 2>/dev/null)
  job_finish "$JOB_ID" awaiting_input "$DONE" "$FAILED" "$EV"
  log "sweep awaiting_input: ${WAITING} task(s) blocked/in_review, nothing ready"
else
  if [[ "$READY" == "False" && "${OPEN:-0}" -gt 0 && "${OPEN:-0}" == "${WAITING:-0}" ]]; then
    log "sweep waiting but scope is unbound (no slug/assignee); finishing done, not awaiting_input"
  fi
  job_finish "$JOB_ID" done "$DONE" "$FAILED"
fi
exit 0
