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

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export WORKSHOP
AUTH_ENV="${WORKSHOP_AUTH_ENV:-$HOME/.config/workshop/auth.env}"
# One switch, read through services/autonomy_override. Fails closed.
source "$WORKSHOP/scripts/autonomy_gate.sh"
AUTONOMY_OVERRIDE=$WORKSHOP/.runtime/autonomy-override.json
RALPH=$WORKSHOP/scripts/ralph.sh
REVIEWER=$WORKSHOP/scripts/reviewer.sh
PTASKS=$WORKSHOP/scripts/project_tasks.py

SLUG=""
DIR_ARG=""           # --dir: sweep an explicit scope dir (agent scope / any tree)
ASSIGNEE=""          # --assignee agent:<slug>: claim only this delegate's (or unassigned) tasks
MAX_WALL=3000        # 50 min total for a project sweep
MAX_TASKS=20         # cap tasks per sweep
# Per-task wall budget (SECONDS) handed to ralph.sh. Env-overridable so a caller
# that also sets --max-wall can keep the two consistent: the loop below refuses to
# START a task it cannot finish inside the remaining sweep budget, so a sweep
# budget smaller than MAX_TASKS * this value silently caps tasks-per-sweep at one.
# See scripts/queue_runner.sh, which sets both from one number.
RALPH_MAX_WALL="${RALPH_MAX_WALL:-1200}"
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
GRANT_TOKEN=""       # --grant-token: the in-scope resource grant the dispatcher
                     # minted for THIS job (services/dispatch_grants.py). A sweep
                     # is an `agent` identity, not the operator, so its reach is
                     # handed to it rather than ambient. Empty for a manual run:
                     # the operator's own Run-now is the operator, never gated.
# --supervised: land a passing task in `in_review` instead of `done`.
#
# This is the autonomy enum's ONLY machine-visible consequence, and it is what
# makes unattended execution safe to try at all: the reviewer gate and the
# committed-work gate are unchanged and still have to pass, but the last step is
# the operator's rather than the sweep's. Env-settable so the resident runner can
# default to it without every call site growing a flag.
SUPERVISED="${WORKSHOP_SWEEP_SUPERVISED:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)         DIR_ARG="$2"; shift 2 ;;
    --assignee)    ASSIGNEE="$2"; shift 2 ;;
    --max-wall)    MAX_WALL="$2"; shift 2 ;;
    --max-tasks)   MAX_TASKS="$2"; shift 2 ;;
    --job-id)      JOB_ID="$2"; shift 2 ;;
    --task-id)     TASK_ID_ARG="$2"; shift 2 ;;
    --grant-token) GRANT_TOKEN="$2"; shift 2 ;;
    --force)       FORCE=1; shift ;;
    --supervised)  SUPERVISED=1; shift ;;
    *)             SLUG="$1"; shift ;;
  esac
done
ASSIGNEE_FLAG=(); [[ -n "$ASSIGNEE" ]] && ASSIGNEE_FLAG=(--assignee "$ASSIGNEE")

# Unattended selection (phase 4). A background sweep additionally requires
# `execution == "code"` and a passing readiness check; a --force sweep is the
# operator saying "work the queue now", which is attended, so it selects exactly
# as it always did. That split is what keeps BOTH ratified rules true: readiness
# never vetoes a human, and only tickets marked ready run unattended.
#
# Fail-closed has a real consequence worth knowing: a store whose tickets carry
# no `execution` yet has NOTHING unattended-ready, so a background sweep of it
# does nothing until they are classified (scripts/classify_open_tickets.py).
# That is the intended behaviour — the alternative is guessing — but it is the
# reason a sweep can go quiet after this change rather than because it broke.
UNATTENDED_FLAG=(); UNATTENDED_SELECT=0
[[ "$FORCE" -ne 1 ]] && { UNATTENDED_FLAG=(--unattended); UNATTENDED_SELECT=1; }

# The grant dies with the job. Its expiry caveat already bounds it, but a token
# that outlives its sweep is reach nobody is using, so revoke on EVERY exit path
# (including the early aborts above the main loop). Idempotent + best-effort.
revoke_grant() {
  [[ -z "$GRANT_TOKEN" ]] && return 0
  (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" \
     python3 -m services.dispatch_grants revoke "$GRANT_TOKEN" >/dev/null 2>&1) || true
  GRANT_TOKEN=""
}
trap revoke_grant EXIT
# A run-now targets exactly one task; never let it drain the queue.
[[ -n "$TASK_ID_ARG" ]] && MAX_TASKS=1

# Job store shim (services.jobs). The run-state of a sweep is a Job in `running`
# status; we finish() it on exit. Replaces the old per-project
# .runtime/ralph-run.json.
# --pid $$ records the sweep pid so the liveness watchdog can tell a crashed
# sweep (dead pid, Job still `running`) from one genuinely in flight (2d).
#
# IMPORT PATH: `PYTHONPATH=$WORKSHOP/backend` + `-m services.jobs`, the same way
# this file already calls services.usage_ledger and services.latitude. It used to
# be `-m backend.services.jobs`, which stopped working the moment jobs.py grew a
# top-level `from services import paths` — that import only resolves with
# backend/ ON the path, not with the repo root as the package parent. Every call
# below then died on ModuleNotFoundError.
#
# And nothing said so, because all four call sites end in `2>/dev/null || true`.
# So a sweep ran, worked tasks, and never recorded a Job: the Jobs feed and the
# chat card had nothing to show, which reads as "the run vanished". The silencing
# is deliberate — a bookkeeping write must never fail a sweep — but it must not
# be silent to the LOG too, so the failure now lands in $LOG where the next
# person looking at a missing run will actually find it.
_job_py() {  # "$@" = args after `python3 -m services.jobs`
  (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" python3 -m services.jobs "$@") \
    2>>"${LOG:-/dev/stderr}"
}
job_start() {  # $1 = trigger kind ; echoes the new job id (coordinator sweep Job)
  _job_py start --project-slug "$SLUG" --agent-id "$ASSIGNEE" \
    --trigger "$1" --status running --pid $$ || true
}
job_start_child() {  # $1=task_id $2=parent_job ; echoes the child Job id
  _job_py start --project-slug "$SLUG" --agent-id "$ASSIGNEE" \
    --trigger loop --status running --task-id "$1" --parent-id "$2" --pid $$ || true
}
job_finish() {  # $1=job_id $2=status $3=tasks_done $4=tasks_failed [$5=evidence-json]
  [[ -z "$1" ]] && return 0
  local ev=(); [[ -n "${5:-}" ]] && ev=(--evidence "$5")
  _job_py finish --id "$1" --status "$2" --tasks-done "${3:-0}" \
    --tasks-failed "${4:-0}" "${ev[@]}" >/dev/null || true
}
job_finish_child() {  # $1=child_job $2=status $3=exit_code [$4=evidence-json]
  [[ -z "$1" ]] && return 0
  local ev=(); [[ -n "${4:-}" ]] && ev=(--evidence "$4")
  _job_py finish --id "$1" --status "$2" --exit-code "${3:-0}" "${ev[@]}" \
    >/dev/null || true
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

# Merge a task worktree's branch into the canonical base branch locally (no push).
# Echoes exactly one of:
#   conflict                 -> merge conflict; caller parks
#   ok\t<commit>             -> merged successfully
#   error: <msg>             -> other failure; caller logs, still done
merge_task_worktree_local() {  # $1=branch_slug
  (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" python3 - "$SLUG" "$1" <<'PYEOF' 2>>"$LOG"
import sys
from services import worktrees
slug, branch_slug = sys.argv[1], sys.argv[2]
try:
    r = worktrees.merge_branch(slug, branch_slug)
except Exception as e:
    print(f"error: {e}")
    raise SystemExit(0)
if not r.get("ok"):
    reason = str(r.get("reason") or "merge failed")
    if "conflict" in reason.lower():
        print("conflict")
    else:
        print("error: " + reason[:300])
    raise SystemExit(0)
print(f"ok\t{r.get('commit') or ''}")
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

# Called on a task's SUCCESS leg in worktree mode. For a bound project: push +
# open PR, park on rebase conflict. For an unbound project: merge the branch
# locally into the canonical base, park on merge conflict. In both cases augments
# $EVIDENCE and returns 0 so the caller proceeds, or parks the task and returns 1
# so the caller skips the done/in_review transition. On the default path
# (WT_MODE!=1) it is a no-op returning 0.
publish_or_park() {  # $1=branch_slug $2=title $3=body
  [[ "$WT_MODE" != "1" ]] && return 0
  local out
  if [[ "$REPO_BOUND" != "1" ]]; then
    # Local-only path: merge into base, no remote push or PR.
    out=$(merge_task_worktree_local "$1")
    if [[ "$out" == "conflict" ]]; then
      python3 "$PTASKS" note "$DIR" "$TASK_ID" "local merge conflict: branch does not merge cleanly into the canonical base; parked (not looping)" --kind acceptance >/dev/null 2>>"$LOG"
      ptasks_tx set-status "$DIR" "$TASK_ID" parked --expect-claim-token "${CLAIM_TOKEN:-}" || true
      job_finish_child "$CHILD_JOB" failed "$RC" "$EVIDENCE"
      PARKED=$((PARKED+1))
      log "task=$TASK_ID PARKED (local merge conflict)"
      return 1
    fi
    if [[ "$out" == ok* ]]; then
      local commit; commit=$(printf '%s' "$out" | cut -f2)
      local aug; aug=$(printf '%s' "$EVIDENCE" | jq -c \
        --arg branch "wt/$SLUG/$1" --arg commit "$commit" \
        '. + {branch: $branch, commit: $commit}' 2>/dev/null)
      [[ -n "$aug" ]] && EVIDENCE="$aug"
      log "task=$TASK_ID merged branch wt/$SLUG/$1 locally commit=$commit"
    else
      log "task=$TASK_ID local merge did not complete: ${out#error: }"
    fi
    return 0
  fi
  # Remote path (bound project): push + open PR.
  out=$(publish_task_worktree "$1" "$2" "$3")
  if [[ "$out" == "conflict" ]]; then
    python3 "$PTASKS" note "$DIR" "$TASK_ID" "publish conflict: branch does not rebase cleanly onto the canonical base; parked (not looping)" --kind acceptance >/dev/null 2>>"$LOG"
    ptasks_tx set-status "$DIR" "$TASK_ID" parked --expect-claim-token "${CLAIM_TOKEN:-}" || true
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
  echo "usage: project_ralph.sh <slug> | --dir <path> [--assignee agent:<slug>] [--max-wall SEC] [--max-tasks N] [--supervised]" >&2
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

# Run a token-fenced task transition (set-status/block that carries
# --expect-claim-token) and detect a FENCE rejection: project_tasks.py prints a
# task object with an id when the write applied, or {} when it was a no-op because
# the claim was displaced (an operator edit bumped the generation, or a liveness
# reclaim reassigned the task) or the task vanished. On a fence we log clearly and
# return 1 so the caller can treat it as a lost claim WITHOUT clobbering the state
# the operator/reclaimer set; a normal transition returns 0 (Fix 1). Logs only to
# the file (never stdout), so it is safe inside command-substituted callers like
# release_or_block whose stdout is captured.
ptasks_tx() {  # $@ = project_tasks.py verb + args (must include --expect-claim-token)
  local out id
  out=$(python3 "$PTASKS" "$@" 2>>"$LOG")
  id=$(jf "$out" id)
  if [[ -z "$id" ]]; then
    echo "[$(ts)] [project_ralph] claim fenced/no-op (operator edit or reclaim displaced this run): $*" >> "$LOG"
    return 1
  fi
  return 0
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

# Execute a task's machine-checkable acceptance criteria (grounded completion
# gate) in the work tree and enforce that ALL pass. Reads the pre-extracted spec
# ($CRIT_SPEC_FILE / $CRIT_COUNT), runs each cmd in WORK_DIR, and decides pass per
# criterion: exit 0 when expect is "exit0" (default), else the expected substring
# must appear in the command's stdout. Captures per-criterion {criterion, cmd,
# expect, pass, evidence(exit code + stdout/stderr tail)}, records ONE task-evidence
# note (kind=criteria) so the receipt/Work UI shows exactly what was checked, writes
# the executed results to $CRIT_RESULTS_FILE, then SURFACES those results to
# reviewer.sh (REVIEWER_CRITERIA_FILE) so the completion gate reflects executed
# evidence, not a self-report. Sets globals CRIT_PRESENT (0/1), CRIT_ALL_PASS (0/1),
# CRIT_FAIL_DESC (first failing criterion). A task with no criteria is a no-op
# (CRIT_PRESENT=0), leaving the existing gate byte-identical.
eval_criteria() {
  CRIT_PRESENT=0; CRIT_ALL_PASS=1; CRIT_FAIL_DESC=""
  (( CRIT_COUNT > 0 )) || return 0
  CRIT_PRESENT=1
  CRIT_RESULTS_FILE="$RUNTIME/criteria-results-$CHILD_JOB-$$.json"
  local status
  status=$(cd "$WORK_DIR" && CRIT_SPEC_FILE="$CRIT_SPEC_FILE" \
             CRIT_RESULTS_FILE="$CRIT_RESULTS_FILE" python3 - <<'PYEOF' 2>>"$LOG"
import json, os, subprocess
try:
    crit = json.load(open(os.environ["CRIT_SPEC_FILE"]))
    if not isinstance(crit, list):
        crit = []
except Exception:
    crit = []
results = []
all_pass = True
first_fail = ""
for c in crit:
    if not isinstance(c, dict):
        continue
    cmd = str(c.get("cmd") or "").strip()
    if not cmd:
        continue
    desc = str(c.get("desc") or cmd).strip()
    expect = str(c.get("expect") or "exit0").strip()
    try:
        p = subprocess.run(["bash", "-lc", cmd], capture_output=True,
                           text=True, timeout=120)
        rc, so, se = p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        rc, so, se = 124, "", "criterion timed out after 120s"
    except Exception as e:
        rc, so, se = 1, "", f"criterion execution error: {e}"
    if expect == "exit0":
        passed = (rc == 0)
    else:
        passed = (expect in so)
    tail = (so + (("\n" + se) if se else "")).strip()
    results.append({"criterion": desc, "cmd": cmd, "expect": expect,
                    "pass": bool(passed), "evidence": f"exit={rc}; " + tail[-300:]})
    if not passed and not first_fail:
        first_fail = desc
    all_pass = all_pass and passed
try:
    json.dump(results, open(os.environ["CRIT_RESULTS_FILE"], "w"))
except Exception:
    pass
print("PASS" if (all_pass and results) else "FAIL\t" + (first_fail or "no criteria executed"))
PYEOF
)
  case "$status" in
    PASS)    CRIT_ALL_PASS=1 ;;
    FAIL*)   CRIT_ALL_PASS=0; CRIT_FAIL_DESC="${status#FAIL}"; CRIT_FAIL_DESC="${CRIT_FAIL_DESC#	}" ;;
    *)       CRIT_ALL_PASS=0; CRIT_FAIL_DESC="criteria evaluation error" ;;
  esac
  # Record what was actually checked as durable task evidence (kind=criteria).
  local summary
  summary=$(CRIT_RESULTS_FILE="$CRIT_RESULTS_FILE" python3 -c "
import json, os
try: r = json.load(open(os.environ['CRIT_RESULTS_FILE']))
except Exception: r = []
n_ok = len([c for c in r if c.get('pass')])
parts = [('ok' if c.get('pass') else 'FAIL') + ': ' + str(c.get('criterion'))[:80] for c in r]
print(f'{n_ok}/{len(r)} acceptance criteria passed | ' + ' ; '.join(parts))
" 2>>"$LOG")
  [[ -n "$summary" ]] && python3 "$PTASKS" note "$DIR" "$TASK_ID" "$summary" --kind criteria >/dev/null 2>>"$LOG"
  # Surface the EXECUTED results to reviewer.sh so the gate reflects the diff PLUS
  # what actually ran (never the worker's self-report). Best-effort, informational:
  # the hard enforcement below keys on CRIT_ALL_PASS, so a reviewer hiccup here can
  # never wrongly PASS a task; this call records the criteria-aware verdict for the
  # Work UI and proves the reviewer judged executed evidence.
  # Pass the bill scope + attribution so this criteria-aware reviewer pass meters
  # its own auxiliary claude spend (if any) against the worker's scope, role=reviewer.
  # Inert for the deterministic gate (no reviewer claude envelope). (research rec 5)
  local rjson rcomplete rreasons
  rjson=$(REVIEWER_CRITERIA_FILE="$CRIT_RESULTS_FILE" REVIEWER_ROOT="$WORK_DIR" \
          REVIEWER_ALLOW_NO_DIFF="$ALLOW_NO_DIFF" \
          REVIEWER_BILL_SCOPE="${BILL_SCOPE:-}" \
          REVIEWER_BILL_TASK_ID="${TASK_ID:-}" \
          REVIEWER_BILL_RUN="${CHILD_JOB:-}" \
          "$REVIEWER" "$BASELINE" 2>>"$LOG")
  rcomplete=$(jf "$rjson" complete)
  rreasons=$(printf '%s' "$rjson" | python3 -c "import json,sys
try: print(', '.join(json.load(sys.stdin).get('reasons',[])))
except Exception: print('')" 2>/dev/null)
  log "task=$TASK_ID criteria-aware reviewer: complete=$rcomplete reasons=${rreasons:-none}"
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
                          str(Path(os.environ["WORKSHOP"]) / ".runtime" / "projects.json")))
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

# A project registered as `supervised` supervises every sweep of it, however the
# sweep was started. The flag and the env var can turn supervision ON for a --dir
# sweep (which has no registry entry and no slug), but nothing here turns it OFF:
# a scope the operator marked supervised must not become unsupervised because a
# caller forgot a flag.
[[ "$MODE" == "supervised" ]] && SUPERVISED=1

# The terminal status of a task whose gates all passed. `in_review` under
# supervision, `done` otherwise. Both writes are otherwise identical: the
# reviewer gate, the committed-work gate and the acceptance criteria have all
# already passed by the time either is reached.
terminal_status() { if [[ "$SUPERVISED" == "1" ]]; then echo "in_review"; else echo "done"; fi; }
terminal_label()  { if [[ "$SUPERVISED" == "1" ]]; then echo "IN_REVIEW (supervised)"; else echo "DONE"; fi; }

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
                          str(Path(os.environ["WORKSHOP"]) / ".runtime" / "projects.json")))
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
# Developer-mode gate (ADR 0014), applied once DIR is resolved from either
# route. This is the door the comment above deliberately leaves open: a --dir
# sweep "trusts the caller's decision", and the dispatcher's project loop treats
# the `self: true` registry row as an ordinary loop candidate. Neither of those
# should be able to sweep the platform's own tree with dev mode off.
source "$WORKSHOP/scripts/dev_mode_gate.sh"
assert_run_target "$DIR" "project_ralph.sh" || exit 2

WT_ENABLED=0
[[ "$CONCURRENT_WT" == "1" && -n "$SLUG" ]] && WT_ENABLED=1
WORK_DIR="$DIR"

RUNTIME="$DIR/.runtime"
mkdir -p "$RUNTIME"
LOG="$RUNTIME/ralph.log"

ts() { date -Iseconds; }
log() { echo "[$(ts)] [project_ralph] $*" >> "$LOG"; echo "[project_ralph] $*"; }

# Global off-switch (same flag + JSON overlay the workshop cron honors). A
# scheduled/continuous sweep obeys them; a manual operator-triggered run
# (--force) is a deliberate foreground action and bypasses both, along with
# the per-project mode gate below.
_is_autonomy_off() {
  autonomy_engaged && return 0
  [[ -f "$AUTONOMY_OVERRIDE" ]] || return 1
  if python3 -c "import json,sys; sys.exit(0 if json.load(open('$AUTONOMY_OVERRIDE')).get('engaged') else 1)" 2>/dev/null; then
    return 0
  fi
  # Corrupt JSON = fail-closed.
  if ! python3 -c "import json; json.load(open('$AUTONOMY_OVERRIDE'))" 2>/dev/null; then
    return 0
  fi
  return 1
}
if _is_autonomy_off && [[ "$FORCE" -ne 1 ]]; then
  log "global autonomy off-switch present (flag or override); not running"
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
# this loop both admit against this scope's rolling-window budget, so this key
# must stay EXACTLY what dispatch_triggers.sh admits against (a bare slug for a
# project, `agent:<slug>` for an agent) — prefixing it here would split one
# scope's budget across two ledger keys and silently double it.
BILL_SCOPE="${SLUG:-$ASSIGNEE}"; [[ -z "$BILL_SCOPE" ]] && BILL_SCOPE="$(basename "$DIR")"

# The LEARNINGS scope is a different namespace from the billing key, and this is
# where F3 bit: learnings/knowledge require a PREFIXED scope (`project:<slug>`,
# `agent:<slug>`, `app:<name>`, `workshop`). A bare billing slug like `demo-x` is
# rejected by learnings._valid_scope, so every per-project lesson and every
# reviewer rejection fell back to `global` — and chat_context injects `global`
# into EVERY scope's prompt, so one project's mistakes leaked into every agent
# everywhere. Emit the prefixed form HERE, at the source, instead of leaving
# ralph.sh to guess that a bare token means a project (it cannot know; an
# unbound --dir sweep's basename is not a project).
learn_scope_for() {
  if [[ -n "$SLUG" ]]; then echo "project:$SLUG"
  elif [[ -n "$ASSIGNEE" ]]; then echo "$ASSIGNEE"   # already carries `agent:`
  else echo "project:$(basename "$DIR")"
  fi
}
LEARN_SCOPE="$(learn_scope_for)"


START_EPOCH=$(date +%s)
log "=== sweep start slug=$SLUG dir=$DIR mode=$MODE job=$JOB_ID scope=$BILL_SCOPE learn_scope=$LEARN_SCOPE grant=${GRANT_TOKEN:0:8} max_wall=${MAX_WALL}s max_tasks=$MAX_TASKS bound=$REPO_BOUND worktrees=$WT_ENABLED ==="

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
    ptasks_tx block "$DIR" "$1" "$4 after $2 attempt(s) (cap $3)" \
      --expect-claim-token "${CLAIM_TOKEN:-}" || true
    log "task=$1 BLOCKED: $4 after $2/$3 attempts"
    echo blocked
  else
    ptasks_tx set-status "$DIR" "$1" todo \
      --expect-claim-token "${CLAIM_TOKEN:-}" || true
    log "task=$1 released to todo ($4; attempt $2/$3)"
    echo retry
  fi
}

# Why the sweep stopped early, when it stopped for a reason that is NOT "there
# was nothing to do". A sweep refused admission never ran, and closing that as
# `done` with 0 tasks told the operator the opposite of what happened. Set here,
# read at job_finish below.
HALT_REASON=""

n=0
while (( n < MAX_TASKS )); do
  REMAIN=$(tick_remaining)
  if (( REMAIN < RALPH_MAX_WALL + 30 )); then
    log "budget remaining ${REMAIN}s < per-task ${RALPH_MAX_WALL}s — stopping"
    HALT_REASON="out of wall-clock budget: ${REMAIN}s left, a task needs ${RALPH_MAX_WALL}s"
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
    NEXT=$(python3 "$PTASKS" next-ready "$DIR" "${ASSIGNEE_FLAG[@]}" "${UNATTENDED_FLAG[@]}" 2>>"$LOG")
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
  # --exclude-job: this sweep's own coordinator Job is already `running`, so
  # without excluding it the reserve counts the task about to start TWICE (once
  # as "the next task", once as an in-flight sweep) and deadlocks the queue in a
  # 150k-wide band below the ceiling.
  # Capture the refusal text rather than discarding it: it is the only thing
  # that can tell the operator why their run did nothing.
  if ! ADMIT_OUT=$(cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" python3 -m services.usage_ledger \
          admit --project "$BILL_SCOPE" --exclude-job "$JOB_ID" 2>/dev/null); then
    ADMIT_WHY=$(printf '%s' "$ADMIT_OUT" | jq -r '.reason // empty' 2>/dev/null)
    [[ -z "$ADMIT_WHY" ]] && ADMIT_WHY="usage budget exhausted for scope=$BILL_SCOPE"
    HALT_REASON="$ADMIT_WHY"
    log "usage budget refused scope=$BILL_SCOPE: $ADMIT_WHY (task=$TASK_ID left ready)"
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
  # EXPLICIT no-diff opt-in (Fix 1 escape hatch): a task that deliberately produces
  # no committed diff (external side effect only) sets acceptance.allow_no_diff true.
  # This is the ONLY signal that exempts a run from the committed-work done-gate
  # below (the acceptance KIND does not), so a `command`/`operator` task that was
  # expected to commit real work but did not can no longer slip through to done.
  EXPLICIT_NO_DIFF=0
  case "$(jf "$NEXT" acceptance.allow_no_diff | tr '[:upper:]' '[:lower:]')" in
    true|1|yes) EXPLICIT_NO_DIFF=1 ;;
  esac
  # REVIEWER_ALLOW_NO_DIFF exempts reviewer.sh's tracked-file no_diff check. Keep
  # today's behavior EXACTLY (command/operator acceptance auto-exempt, so a
  # legitimately-no-tracked-diff task does not fail the reviewer), and also honor an
  # explicit flag. This is the reviewer gate only; the commit gate is separate.
  ALLOW_NO_DIFF=0
  [[ "$ACC_KIND" == "command" || "$ACC_KIND" == "operator" ]] && ALLOW_NO_DIFF=1
  [[ "$EXPLICIT_NO_DIFF" == "1" ]] && ALLOW_NO_DIFF=1

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

  # Machine-checkable acceptance criteria (grounded gate). Extract the task's
  # criteria list to a spec file the success leg executes. CRIT_COUNT=0 (absent /
  # empty / malformed) makes the criteria gate a pure no-op, so a task without
  # assertions runs exactly as before. pid+job-unique like the receipt file.
  CRIT_SPEC_FILE="$RUNTIME/criteria-spec-$CHILD_JOB-$$.json"
  CRIT_COUNT=$(printf '%s' "$NEXT" | CRIT_SPEC_FILE="$CRIT_SPEC_FILE" python3 -c "
import json, os, sys
try: d = json.load(sys.stdin)
except Exception: d = {}
crit = ((d.get('acceptance') or {}).get('criteria')) or []
crit = [c for c in crit if isinstance(c, dict) and str(c.get('cmd') or '').strip()]
open(os.environ['CRIT_SPEC_FILE'], 'w').write(json.dumps(crit))
print(len(crit))
" 2>>"$LOG" || echo 0)
  [[ -z "$CRIT_COUNT" ]] && CRIT_COUNT=0

  # A deep/quick task overrides the model tier via ANTHROPIC_MODEL (ralph.sh runs
  # `claude -p`, which honors it as the default-model override). Set only for the
  # non-default efforts so a standard/null task's environment is untouched.
  [[ -n "$EFF_MODEL" ]] && export ANTHROPIC_MODEL="$EFF_MODEL"
  # Pass the claim identity so ralph.sh heartbeats this task's lease from inside its
  # loop (Fix 2). --store-dir is the project DIR (where .tasks.json lives) even in
  # worktree mode, where --dir is the isolated worktree. The heartbeat presents
  # CLAIM_TOKEN so it composes with the Fix-1 fence.
  set +e
  REVIEWER_ALLOW_NO_DIFF="$ALLOW_NO_DIFF" WORKSHOP_GRANT_TOKEN="$GRANT_TOKEN" "$RALPH" "$TASK_TEXT" --dir "$WORK_DIR" --baseline-rev "$BASELINE" --max-iter "$EFF_MAX_ITER" --max-wall "$RALPH_MAX_WALL" --bill-project "$BILL_SCOPE" --learn-scope "$LEARN_SCOPE" --receipt "$RECEIPT_FILE" --store-dir "$DIR" --task-id "$TASK_ID" --claim-token "${CLAIM_TOKEN:-}" --run "$CHILD_JOB" >> "$LOG" 2>&1
  RC=$?
  set -e
  [[ -n "$EFF_MODEL" ]] && unset ANTHROPIC_MODEL

  # Structured receipt for the child Job's evidence: git-derived commits/head vs
  # the pre-run BASELINE, merged with ralph's receipt file. Attached on ALL
  # terminal child paths below (a failed run's receipt explains the failure).
  HEAD=$(cd "$WORK_DIR" && git rev-parse HEAD 2>/dev/null || echo "")
  COMMITS=$(cd "$WORK_DIR" && git rev-list --reverse "$BASELINE..$HEAD" 2>/dev/null || echo "")
  EVIDENCE=$(build_receipt_evidence "$RECEIPT_FILE" "$BASELINE" "$HEAD" "$COMMITS")
  # Did the run land a REAL commit between the pre-run baseline and HEAD in the
  # scope dir? This (not the reviewer's working-tree status) is the source of truth
  # for the committed-work done-gate (Fix 1).
  COMMITTED=0
  [[ -n "$(printf '%s' "$COMMITS" | tr -d '[:space:]')" ]] && COMMITTED=1
  # One task-side evidence entry pointing at the child Job (the ticket timeline
  # links to the run rather than duplicating the receipt payload).
  note_receipt_ref() { python3 "$PTASKS" note "$DIR" "$TASK_ID" "$CHILD_JOB" --kind receipt --ref "$CHILD_JOB" >/dev/null 2>>"$LOG"; }

  # Touches gate: hold the run to what the TASK said it writes. $EVIDENCE.files is
  # the committed diff vs the pre-run baseline (bookkeeping paths already filtered
  # out by build_receipt_evidence), i.e. what this run actually landed. A task with
  # no `touches` is unrestricted, so this is a pure no-op for every task that does
  # not opt in — which is all of them today. Checked BEFORE the acceptance legs: a
  # run that wrote outside its lane does not get to be `done` on its way past.
  TOUCH_FILES=$(printf '%s' "$EVIDENCE" | jq -r '.files[]?' 2>/dev/null)
  if [[ -n "$TOUCH_FILES" ]]; then
    TOUCH_OUT=$(printf '%s\n' "$TOUCH_FILES" | python3 "$PTASKS" check-touches "$DIR" "$TASK_ID" \
                  --job-id "$CHILD_JOB" --expect-claim-token "${CLAIM_TOKEN:-}" 2>>"$LOG")
    if [[ "$TOUCH_OUT" == *'"blocked": true'* ]]; then
      # enforce_touches already recorded the evidence line and blocked the task.
      BLOCKED=$((BLOCKED+1))
      job_finish_child "$CHILD_JOB" failed "$RC" "$EVIDENCE"
      log "task=$TASK_ID BLOCKED: touches violation $(printf '%s' "$TOUCH_OUT" | jq -c '.violations' 2>/dev/null)"
      rm -f "$RECEIPT_FILE" "${CRIT_SPEC_FILE:-}" "${CRIT_RESULTS_FILE:-}" 2>/dev/null || true
      CRIT_SPEC_FILE=""; CRIT_RESULTS_FILE=""
      sleep 1
      continue
    fi
  fi

  case "$RC" in
    0)
      # ralph's reviewer gate passed. Apply the task's own acceptance policy.
      # In worktree mode each SUCCESS leg first publishes the task's branch
      # (push + PR). publish_or_park is a no-op returning 0 on the default path,
      # so the transitions below are unchanged there; in worktree mode a publish
      # conflict parks the task and returns non-zero, skipping the done/in_review
      # transition (park, do not loop). On publish success it augments $EVIDENCE
      # with the branch + pr so the child Job receipt carries them.
      #
      # Committed-work done-gate (Fix 1): the reviewer / acceptance command can pass
      # on an UNCOMMITTED working tree (e.g. `test -f X` against a file the worker
      # created but never committed), which would land the task `done` with an empty
      # receipt. A task may reach done/in_review only if the run produced a real
      # commit, UNLESS it is an explicitly-flagged no-diff task (acceptance
      # .allow_no_diff true, an external-side-effect task). Otherwise release/block
      # with a clear reason instead of marking it done. A normal task that commits
      # real work has COMMITTED=1 and falls straight through to the paths below,
      # byte-identical to today.
      if (( COMMITTED == 0 )) && [[ "$EXPLICIT_NO_DIFF" != "1" ]]; then
        python3 "$PTASKS" note "$DIR" "$TASK_ID" "acceptance passed but no work was committed; not marking done (set acceptance.allow_no_diff for a deliberate external-side-effect task)" --kind acceptance >/dev/null 2>>"$LOG"
        if [[ "$(release_or_block "$TASK_ID" "$ATTEMPTS" "$MAX_ATTEMPTS" "acceptance passed but no work was committed")" == "blocked" ]]; then BLOCKED=$((BLOCKED+1)); else FAILED=$((FAILED+1)); fi
        job_finish_child "$CHILD_JOB" failed "$RC" "$EVIDENCE"
        log "task=$TASK_ID NOT done: acceptance passed but nothing was committed"
      else
      # Grounded acceptance-criteria gate (O4+): EXECUTE the task's machine-checkable
      # assertions in the work tree, record what was checked, and require ALL to pass
      # before any done/in_review transition. eval_criteria also surfaces the executed
      # results to reviewer.sh. A task with NO criteria is a pure no-op (CRIT_PRESENT=0),
      # so the kind routing below is byte-identical to before for those tasks. Criteria
      # combine with the existing gate: reviewer-kind => criteria pass AND ralph's
      # reviewer approved (RC=0 here); command-kind WITH criteria => the criteria ARE
      # the check (the extra ACC_SPEC run is skipped, guarded by CRIT_PRESENT==0).
      eval_criteria
      if (( CRIT_PRESENT == 1 && CRIT_ALL_PASS == 0 )); then
        python3 "$PTASKS" note "$DIR" "$TASK_ID" "acceptance criterion failed: ${CRIT_FAIL_DESC}" --kind acceptance >/dev/null 2>>"$LOG"
        if [[ "$(release_or_block "$TASK_ID" "$ATTEMPTS" "$MAX_ATTEMPTS" "acceptance criterion failed: ${CRIT_FAIL_DESC}")" == "blocked" ]]; then BLOCKED=$((BLOCKED+1)); else FAILED=$((FAILED+1)); fi
        job_finish_child "$CHILD_JOB" failed "$RC" "$EVIDENCE"
        log "task=$TASK_ID NOT done: acceptance criterion failed (${CRIT_FAIL_DESC})"
      elif [[ "$ACC_KIND" == "command" && -n "$ACC_SPEC" ]] && (( CRIT_PRESENT == 0 )); then
        set +e; ACC_OUT=$(run_acceptance_cmd "$ACC_SPEC"); ACC_RC=$?; set -e
        if (( ACC_RC == 0 )); then
          if publish_or_park "$WT_BRANCH_SLUG" "$PR_TITLE" "$PR_BODY"; then
            python3 "$PTASKS" note "$DIR" "$TASK_ID" "command acceptance passed: $ACC_SPEC" --kind acceptance >/dev/null 2>>"$LOG"
            if ptasks_tx set-status "$DIR" "$TASK_ID" "$(terminal_status)" --job-id "$CHILD_JOB" --expect-claim-token "${CLAIM_TOKEN:-}"; then
              note_receipt_ref
              if [[ "$SUPERVISED" == "1" ]]; then IN_REVIEW=$((IN_REVIEW+1)); else DONE=$((DONE+1)); fi
              job_finish_child "$CHILD_JOB" done "$RC" "$EVIDENCE"; log "task=$TASK_ID $(terminal_label) (command acceptance ok)"
            else
              job_finish_child "$CHILD_JOB" cancelled "$RC" "$EVIDENCE"; log "task=$TASK_ID done write fenced (claim displaced); left as operator/reclaim set it"
            fi
          fi
        else
          python3 "$PTASKS" note "$DIR" "$TASK_ID" "command acceptance FAILED (exit $ACC_RC): $ACC_SPEC :: $(echo "$ACC_OUT" | tr '\n' ' ' | head -c 300)" --kind acceptance >/dev/null 2>>"$LOG"
          if [[ "$(release_or_block "$TASK_ID" "$ATTEMPTS" "$MAX_ATTEMPTS" "command acceptance failing")" == "blocked" ]]; then BLOCKED=$((BLOCKED+1)); else FAILED=$((FAILED+1)); fi
          job_finish_child "$CHILD_JOB" failed "$ACC_RC" "$EVIDENCE"
        fi
      elif [[ "$ACC_KIND" == "operator" ]]; then
        if publish_or_park "$WT_BRANCH_SLUG" "$PR_TITLE" "$PR_BODY"; then
          python3 "$PTASKS" note "$DIR" "$TASK_ID" "worker completed; awaiting operator approval" --kind acceptance >/dev/null 2>>"$LOG"
          if ptasks_tx set-status "$DIR" "$TASK_ID" in_review --job-id "$CHILD_JOB" --expect-claim-token "${CLAIM_TOKEN:-}"; then
            note_receipt_ref
            IN_REVIEW=$((IN_REVIEW+1)); job_finish_child "$CHILD_JOB" done "$RC" "$EVIDENCE"; log "task=$TASK_ID -> in_review (operator gate)"
          else
            job_finish_child "$CHILD_JOB" cancelled "$RC" "$EVIDENCE"; log "task=$TASK_ID in_review write fenced (claim displaced); left as operator/reclaim set it"
          fi
        fi
      else
        if publish_or_park "$WT_BRANCH_SLUG" "$PR_TITLE" "$PR_BODY"; then
          if ptasks_tx set-status "$DIR" "$TASK_ID" "$(terminal_status)" --job-id "$CHILD_JOB" --expect-claim-token "${CLAIM_TOKEN:-}"; then
            note_receipt_ref
            if [[ "$SUPERVISED" == "1" ]]; then IN_REVIEW=$((IN_REVIEW+1)); else DONE=$((DONE+1)); fi
            job_finish_child "$CHILD_JOB" done "$RC" "$EVIDENCE"; log "task=$TASK_ID $(terminal_label)"
          else
            job_finish_child "$CHILD_JOB" cancelled "$RC" "$EVIDENCE"; log "task=$TASK_ID done write fenced (claim displaced); left as operator/reclaim set it"
          fi
        fi
      fi
      fi
      ;;
    3) ptasks_tx block "$DIR" "$TASK_ID" "classifier block" --expect-claim-token "${CLAIM_TOKEN:-}" || true; BLOCKED=$((BLOCKED+1)); job_finish_child "$CHILD_JOB" failed "$RC" "$EVIDENCE"; log "task=$TASK_ID BLOCKED (classifier)" ;;
    5)
      # Environment failure (API outage / auth), NOT task failure: give the
      # attempt back and STOP the sweep so we do not hammer a down API. The sweep
      # still tallies a failure so the circuit breaker cools this scope off for a
      # window (it keys on tasks_failed). (review F4)
      python3 "$PTASKS" note "$DIR" "$TASK_ID" "environment failure (API/auth); attempt restored, sweep backing off" --kind acceptance >/dev/null 2>>"$LOG"
      ptasks_tx set-status "$DIR" "$TASK_ID" todo --restore-attempt --expect-claim-token "${CLAIM_TOKEN:-}" || true
      job_finish_child "$CHILD_JOB" failed "$RC" "$EVIDENCE"; FAILED=$((FAILED+1))
      log "task=$TASK_ID ENV FAILURE: released (attempt restored); stopping sweep to back off"
      rm -f "$RECEIPT_FILE" 2>/dev/null || true
      break ;;
    6)
      # rc=6 (in-run no-progress / thrash): ralph stopped the run MID-FLIGHT because
      # it was churning without progress (same reviewer rejection or a near-identical
      # diff repeated, or the per-run token budget was blown). Release/block with a
      # human reason so the Work UI shows WHY it stopped rather than a bare failure.
      # PARKED, not failed, and not released for another go. A thrashing run did
      # not produce wrong work; it produced no work, and the next attempt on an
      # unchanged ticket thrashes the same way, which is how a stuck run turns
      # into a burnt attempt cap and then a blocked ticket whose stated reason is
      # a lie ("failed 3 times" when it never got started). Parking says the true
      # thing: stopped, nothing committed, needs a look. It is already the
      # dead-letter for attempt-cap exhaustion, already maps to `backlog`, and is
      # already visible in the Work UI.
      #
      # The reason names the child job so the run is findable, which is the
      # "resumable id" half: un-parking the ticket is one click and the operator
      # can read what the run actually did first.
      python3 "$PTASKS" note "$DIR" "$TASK_ID" "no progress within the run (repeated rejection / near-identical diff, or run token budget exceeded); parked mid-run, nothing committed. Run: $CHILD_JOB" --kind acceptance >/dev/null 2>>"$LOG"
      ptasks_tx set-status "$DIR" "$TASK_ID" parked --expect-claim-token "${CLAIM_TOKEN:-}" || true
      PARKED=$((PARKED+1))
      # The child job is `failed` to match the other park sites (publish_or_park):
      # the RUN did not complete, whatever the ticket's disposition. Only the
      # TASK's status carries the "never failed" rule.
      job_finish_child "$CHILD_JOB" failed "$RC" "$EVIDENCE"
      log "task=$TASK_ID PARKED (no progress within the run); resume from job $CHILD_JOB" ;;
    7)
      # rc=7 (block-for-decision): a `strict` sweep whose gate could not establish
      # completeness (reviewer.sh). Strict means ask rather than assume, so this
      # BLOCKS for the operator instead of retrying: the ambiguity is in the
      # environment, and another attempt re-runs the same unavailable check. Not
      # release_or_block — the attempt cap is about a task that keeps failing, and
      # this task did not fail. The reviewer's own reason travels verbatim so the
      # block is legible without re-deriving what was ambiguous.
      BLOCK_REASON=$(jq -r '.reviewer.blocked_reason // empty' "$RECEIPT_FILE" 2>/dev/null)
      [[ -z "$BLOCK_REASON" ]] && BLOCK_REASON="latitude:strict-and-ambiguous"
      # The block also carries a STRUCTURED request so the Review tab can act on
      # it rather than just display a sentence. kind=decision: the gate could not
      # establish completeness, so what it needs is a call, not a permission. The
      # ambiguity list is the dedupe key, so a rule that stopped ten tasks the same
      # way is answered once.
      BLOCK_AMBIG=$(jq -r '(.reviewer.ambiguous // []) | join(",")' "$RECEIPT_FILE" 2>/dev/null)
      ptasks_tx block "$DIR" "$TASK_ID" "$BLOCK_REASON" \
        --kind decision --ask "$BLOCK_REASON" \
        --unblocks-if "the operator confirms completeness, or says what to check instead" \
        --dedupe-key "latitude-strict:${BLOCK_AMBIG:-unknown}" \
        --raised-by "reviewer" \
        --expect-claim-token "${CLAIM_TOKEN:-}" || true
      python3 "$PTASKS" note "$DIR" "$TASK_ID" "$BLOCK_REASON" --kind acceptance >/dev/null 2>>"$LOG"
      log "task=$TASK_ID BLOCKED for decision (strict latitude): $BLOCK_REASON"
      BLOCKED=$((BLOCKED+1))
      job_finish_child "$CHILD_JOB" failed "$RC" "$EVIDENCE" ;;
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
  # The criteria spec/results files are likewise transport (recorded as task
  # evidence + Job receipt already), so drop them too.
  rm -f "$RECEIPT_FILE" "${CRIT_SPEC_FILE:-}" "${CRIT_RESULTS_FILE:-}" 2>/dev/null || true
  CRIT_SPEC_FILE=""; CRIT_RESULTS_FILE=""
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
OUT=$(cd "$WORKSHOP" && python3 - "$DIR" "$ASSIGNEE" "${SLUG:-$DIR}" "$UNATTENDED_SELECT" <<'PYEOF'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["WORKSHOP"] + "/backend")
from services import tasks
root = Path(sys.argv[1]); assignee = sys.argv[2] or None; scope = sys.argv[3]
# Same predicate the sweep just selected with. If this said "ready" while the
# sweep could not pick anything up, the scope would never report awaiting_input
# and the operator would never be told the queue had stalled on unclassified or
# underspecified tickets — it would just look idle.
unattended = sys.argv[4] == "1"
ready = tasks.next_ready(root, assignee=assignee, unattended=unattended)
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
  # A sweep that was refused before it worked anything is not done. Close it
  # `cancelled` carrying the refusal, so the Job row, the run report, and the UI
  # all say the run did not happen instead of claiming a clean zero-task success.
  if [[ -n "$HALT_REASON" && "$DONE" -eq 0 && "$FAILED" -eq 0 ]]; then
    HALT_EV=$(jq -nc --arg r "$HALT_REASON" '{halted:$r}' 2>/dev/null)
    [[ -z "$HALT_EV" ]] && HALT_EV="{\"halted\":\"refused\"}"
    job_finish "$JOB_ID" cancelled "$DONE" "$FAILED" "$HALT_EV"
    log "sweep cancelled without working anything: $HALT_REASON"
  elif [[ -n "$HALT_REASON" ]]; then
    HALT_EV=$(jq -nc --arg r "$HALT_REASON" '{halted:$r}' 2>/dev/null)
    [[ -z "$HALT_EV" ]] && HALT_EV="{\"halted\":\"stopped early\"}"
    job_finish "$JOB_ID" done "$DONE" "$FAILED" "$HALT_EV"
  else
    job_finish "$JOB_ID" done "$DONE" "$FAILED"
  fi
fi
exit 0
