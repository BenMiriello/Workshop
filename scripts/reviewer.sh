#!/bin/bash
# reviewer.sh — deterministic completion gate.
#
# Called by Ralph's loop and by the cron worker before claiming `[x]`. Inspects
# the working tree against a baseline commit-ish (default: HEAD) and returns
# JSON on stdout:
#
#   {
#     "complete":  true|false,
#     "diff_files": N,
#     "stub_hits":  [{file, line, marker}, ...],   // NEW stubs only
#     "typecheck":  {"py": "ok|fail|skipped", "ts": "ok|fail|skipped"},
#     "verdict":    "approve|reject|block-for-decision",
#     "latitude":   "strict|standard|open",
#     "ambiguous":  [string, ...],                 // checks that COULD NOT run
#     "blocked_reason": string|null,
#     "criteria":   [{criterion, cmd, expect, pass, evidence}, ...],  // executed, if any
#     "reasons":    [string, ...]
#     "one_definition":    "ok|fail|skipped",   // R3: duplicate enum value-set touching this diff
#     "occupancy_notice":  string|null,         // R2: NEW zero-occupancy field since baseline (advisory)
#     "doc_status_notice": string|null,         // R1: a changed doc has no status / a misleading banner (advisory)
#     "module_layout":       "ok|fail|skipped", // ADR 0024: function-local imports / import cycles /
#                                                // upward layering / big-file count, each ratcheted
#                                                // against scripts/module_layout_baseline.json
#     "no_process_globals":  "ok|fail|skipped", // tenancy clause (target-architecture-2026-08-17.md
#                                                // §2.6 item 3): count of module-level mutable globals
#                                                // in backend/, ratcheted against
#                                                // scripts/no_process_globals_baseline.json
#     "no_raw_storage":      "ok|fail|skipped"  // ADR 0025 c4: localStorage/sessionStorage
#                                                // outside lib/storage.ts. ABSOLUTE, tree is at zero
#     "response_model":      "ok|fail|skipped"  // ADR 0023: routes/ lacking response_model=, ratcheted
#                                                // per-file against scripts/response_model_baseline.json
#     "memory_reachable":    "ok|fail|skipped"  // scripts/verify_memory.py: the memory acceptance
#                                                // harness, run when the diff touches the retrieval
#                                                // path (index / share policy / scope tree / query tool)
#   }
#
# This gate judges the DIFF and executed evidence ONLY. The worker's own
# self-report/summary is never passed in (it is not read here), so a worker cannot
# talk the reviewer into approving incomplete work (self-preference bias).
#
# `complete: true` requires ALL of:
#   - non-empty diff (something changed), UNLESS REVIEWER_ALLOW_NO_DIFF=1, set by
#     project_ralph.sh for command/operator-acceptance tasks, whose real gate is
#     the acceptance command / operator review, not a tracked-file diff (F18)
#   - zero NEW stub markers introduced (preexisting stubs in unchanged code are OK)
#   - python typecheck passes on changed .py files (uses backend venv mypy if present)
#   - the BACKEND TEST SUITE has no failure outside its pinned baseline, when
#     the diff touches Python (ADR-less; see check_backend_tests.py's own
#     header for why this arrived only on 2026-08-31 -- the suite could not
#     finish before then, so there was nothing to gate on)
#   - typescript tsc --noEmit passes on changed .ts/.tsx files
#   - no enum value-set touching this diff is declared in two places (R3, only
#     when the diff introduces or touches an existing same-language duplicate —
#     see check_one_definition.py's own header for why it is not unconditional)
#   - no module-layout metric (function-local imports, import-cycle SCC size,
#     upward layering violations, files over 800 lines) is worse than the
#     pinned baseline (ADR 0024; see check_module_layout.py's own header)
#   - no new process-local mutable global in backend/ (tenancy clause; see
#     check_no_process_globals.py's own header)
#   - the memory acceptance harness passes, when the diff touches the retrieval
#     path (scripts/verify_memory.py; see 4.6c-vi for why it is gated on that
#     path rather than run every time)
#   - no routes/ file touched by this diff has MORE routes lacking
#     response_model= than the pinned baseline (ADR 0023; see
#     check_response_model.py's own header)
#   - the frontend does not state MORE response shapes by hand than the pinned
#     baseline: a response body's type is named from
#     frontend/src/api/schema.gen.ts, never written out here. A hand-written one
#     typechecks green while reading `undefined`, which is how the Page[T]
#     rename left /tickets empty over 188 tickets for two weeks (see
#     check_response_types.py's own header)
#   - no response MODEL touched by this diff has more untyped fields
#     (`dict[str, Any]`, `list[dict]`, bare `Any`) than the pinned baseline. The
#     route-level response_model check above does not see these: a real model
#     whose fields are `dict` still generates `Record<string, never>` in the
#     client (ADR 0023 clause 6; see check_opaque_fields.py's own header)
#   - every GET route the running backend serves answers without a 5xx (ADR 0023
#     clause 6, third half; see check_routes_answer.py's own header). The two
#     checks above read the DECLARATION; only calling a route can tell whether the
#     declared type matches what the handler returns, and a mismatch is a 500 --
#     the shape that took the chat surface down on 2026-08-17
#   - zero raw localStorage/sessionStorage calls outside frontend/src/lib/storage.ts
#     (ADR 0025 clause 4). Absolute rather than ratcheted: the tree is at zero, and
#     a new raw call is a key a second account on the same machine can read
#   - every executed acceptance criterion passed, when REVIEWER_CRITERIA_FILE points
#     at a JSON results array (machine-checkable assertions run by project_ralph.sh)
#
# Conservative: if a check can't run (no tooling), it's "skipped" — counted as pass.
# Strict-mode (REVIEWER_STRICT=1) flips skipped to fail.
#
# LATITUDE (exported by ralph.sh, resolved through services/latitude.py) decides
# what happens when this gate WOULD APPROVE but a check that APPLIED could not
# actually run — the reviewer's honest "I don't know". Counting that as a pass is
# the default and is right for `standard`/`open`. At `strict` it is exactly the
# wrong answer: strict means "when it is not established, ask". So a strict sweep
# with an applicable-but-unrun check returns verdict `block-for-decision` and a
# `blocked_reason` naming the ambiguity; project_ralph.sh flips the task to
# `blocked` with that reason instead of banking an unverified done.
#
# "Applicable but unrun" is narrower than "skipped": a skip because no .py file
# changed is not an ambiguity, it is a non-question. Only a check with real input
# and no tooling to judge it counts.
#
# Usage:
#   reviewer.sh [baseline-rev]        # default baseline = HEAD
#
# Exits 0 always (output is JSON; consumers parse `complete` field).
#
# GATE RESULTS ON THE CHANGE (step C3). Set REVIEWER_CHANGE_ID, or
# REVIEWER_GATE_PROJECT + REVIEWER_GATE_BRANCH, and every per-check outcome
# below is also written to that change's `gates` column via
# services/change_gates.py — including the ones that SKIPPED, with the reason,
# because an absent gate and a skipped one read identically to a person. The
# write is best-effort and cannot change this script's verdict or exit code.

set -u

# ADDING A DELTA-CAPABLE CHECKER (2026-08-23). Seven checkers now follow the
# same pattern: they accept `--since <ref>`, scan the whole tree at that ref
# (via `git archive` through `scripts/_ratchet_delta.py`), and fail only when
# THIS RUN made a metric worse, so inherited debt never blocks unrelated work.
#
# To wire a new delta-capable Python checker:
#   1. Accept `--since REV` in the checker's argparse (see check_no_app_names.py
#      and check_module_layout.py for the complete pattern).
#   2. Call `_delta_check <VAR> <script.py> [<baseline.json>]` from reviewer.sh
#      inside any applicable change gate. The helper handles the file-exists
#      check, passes --since "$WS_BASELINE", and sets VAR to ok/fail.
#   3. If the check also runs against a different root (REVIEWER_ROOT projects),
#      make sure its --since ref is resolvable in the WORKSHOP repo
#      (WS_BASELINE handles that; don't pass BASELINE directly).
#   For a shell checker, call `bash "$WORKSHOP/scripts/<script>.sh" --since "$WS_BASELINE"`
#   directly (shell callers manage the AMBIGUOUS array themselves, as hardcoding does).
#
# `_delta_check` sets the named variable to ok/fail/unchanged (skipped stays
# as-is if the prerequisite files are missing). It DOES NOT handle the outer
# change gate — that remains per-check so different checks can gate on different
# file patterns.
_delta_check() {
  local varname="$1"
  local script="$2"
  local baseline="${3:-}"
  local script_path="$WORKSHOP/scripts/$script"
  local label="${script%.py}"
  label="${label%.sh}"

  if [[ ! -f "$script_path" ]]; then
    AMBIGUOUS+=("${label}_check_unavailable")
    return
  fi
  if [[ -n "$baseline" && ! -f "$WORKSHOP/scripts/$baseline" ]]; then
    AMBIGUOUS+=("${label}_check_unavailable")
    return
  fi

  local runner="python3"
  [[ "$script" == *.sh ]] && runner="bash"

  if $runner "$script_path" --since "$WS_BASELINE" >/dev/null 2>&1; then
    printf -v "$varname" "ok"
  else
    printf -v "$varname" "fail"
  fi
}

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export WORKSHOP
# ROOT is the tree to inspect (git repo + stub scan). Defaults to the workshop
# install, but a per-project Ralph run points it at projects/<slug> via
# REVIEWER_ROOT so the reviewer gates the PROJECT's own git repo. WORKSHOP stays
# the tooling home (venv mypy, tsc, check_design.sh).
ROOT=${REVIEWER_ROOT:-$WORKSHOP}
BASELINE=${1:-HEAD}
LOG=$WORKSHOP/.runtime/reviewer.log

# A BASELINE THE WORKSHOP REPO CAN RESOLVE. Most gates below inspect $ROOT, but
# the three whole-tree ones (no-hardcoding, module-layout, no-app-names) measure
# $WORKSHOP by construction, and their `--since` mode resolves the ref with
# `git archive` IN $WORKSHOP. On a per-project run (REVIEWER_ROOT=projects/<slug>,
# a separate git repo -- seven of them exist) $BASELINE is a sha from THAT repo,
# which the workshop repo has never heard of: all three would exit 2, read as
# `fail`, and reject a project run over workshop debt it could not have touched.
# That is the same "blamed for what it did not cause" defect the --since work of
# 2026-08-23 exists to remove, so it must not be reintroduced by the fix.
#
# The workshop's own HEAD is the honest answer for that case: a project run
# changed nothing in the workshop tree, so it made none of these metrics worse,
# and HEAD-vs-working-tree says exactly that. When ROOT is the workshop (the
# normal case) $BASELINE resolves and this is a no-op.
WS_BASELINE="$BASELINE"
if ! git -C "$WORKSHOP" rev-parse --verify --quiet "${BASELINE}^{commit}" >/dev/null 2>&1; then
  WS_BASELINE="HEAD"
fi
STRICT=${REVIEWER_STRICT:-0}

mkdir -p "$(dirname "$LOG")"
ts() { date -Iseconds; }

cd "$ROOT" 2>/dev/null || { echo '{"complete":false,"reasons":["root_unreachable"]}'; exit 0; }

# Checks that HAD input to judge but no tooling to judge it with — the reviewer's
# "I don't know" (see the LATITUDE note above). Empty on a normal run.
AMBIGUOUS=()
LATITUDE_VAL=${LATITUDE:-standard}
case "$LATITUDE_VAL" in
  strict|standard|open) ;;
  *) LATITUDE_VAL="standard" ;;
esac

# 1. Collect changed files vs baseline. Include staged + unstaged + untracked.
CHANGED_TRACKED=$(git diff --name-only "$BASELINE" 2>/dev/null || true)
CHANGED_STAGED=$(git diff --cached --name-only "$BASELINE" 2>/dev/null || true)
CHANGED_UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null || true)
CHANGED_ALL=$(printf '%s\n%s\n%s\n' "$CHANGED_TRACKED" "$CHANGED_STAGED" "$CHANGED_UNTRACKED" | sort -u | sed '/^$/d')
DIFF_FILES=$(printf '%s\n' "$CHANGED_ALL" | sed '/^$/d' | wc -l)

# 2. Stub markers in changed files only. We scan the WORKING-TREE state of each
#    changed file rather than git-diff text, so we catch stubs no matter how
#    they entered the file. Pre-existing stubs in unchanged files are ignored.
#    Tokens chosen from research brief — high signal, low false-positive.
STUB_HITS_JSON='[]'
STUB_COUNT=0
if [[ -n "$CHANGED_ALL" ]]; then
  STUB_HITS_JSON=$(python3 - <<'PYEOF' "$ROOT" "$CHANGED_ALL"
import json, os, re, sys

workshop, changed_blob = sys.argv[1], sys.argv[2]
files = [l for l in changed_blob.splitlines() if l.strip()]

# Tightened: TODO/FIXME/HACK/XXX must be in canonical-stub form to count.
# Canonical = followed by `:` (`TODO:`) OR appearing in a comment-leading position
# (`# TODO`, `// TODO`, `/* TODO`, ` * TODO`). This filters out incidental uses
# in docstrings ("TODO.md filename") and identifiers (TODO_PATH constant).
PATTERNS = [
    (re.compile(r"\bTODO:"),                                                     "TODO"),
    (re.compile(r"^\s*(?:#|//|/\*|\*)\s*TODO\b"),                                "TODO-comment"),
    (re.compile(r"\bFIXME:"),                                                    "FIXME"),
    (re.compile(r"^\s*(?:#|//|/\*|\*)\s*FIXME\b"),                               "FIXME-comment"),
    (re.compile(r"\bXXX:"),                                                      "XXX"),
    (re.compile(r"^\s*(?:#|//|/\*|\*)\s*XXX\b"),                                 "XXX-comment"),
    (re.compile(r"\bHACK:"),                                                     "HACK"),
    (re.compile(r"^\s*(?:#|//|/\*|\*)\s*HACK\b"),                                "HACK-comment"),
    (re.compile(r"NotImplementedError"),                                         "NotImplementedError"),
    (re.compile(r"raise\s+NotImplemented"),                                      "raise NotImplemented"),
    (re.compile(r"^\s*pass\s*#\s*(?:stub|todo|placeholder)", re.IGNORECASE),     "pass-stub"),
    (re.compile(r"^\s*\.\.\.\s*$"),                                              "ellipsis-stub"),
    (re.compile(r"throw new Error\(['\"](?:not implemented|TODO|unimplemented)", re.IGNORECASE), "js-not-impl"),
    (re.compile(r"panic!\(['\"](?:todo|unimplemented)", re.IGNORECASE),          "rust-todo"),
    (re.compile(r"# stub$|// stub$"),                                            "stub-comment"),
]
# Files to scan: source extensions only, exclude vendor.
ALLOWED_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go"}
EXCLUDED_DIR_PARTS = {"node_modules", ".venv", "__pycache__", "dist", "build", ".git", "coverage", ".next"}

hits = []
for rel in files:
    abs_p = os.path.join(workshop, rel)
    if not os.path.isfile(abs_p):
        continue
    parts = rel.split("/")
    if any(p in EXCLUDED_DIR_PARTS for p in parts):
        continue
    ext = os.path.splitext(rel)[1]
    if ext not in ALLOWED_EXT:
        continue
    try:
        with open(abs_p, "r", encoding="utf-8", errors="replace") as f:
            for ln, line in enumerate(f, 1):
                for pat, name in PATTERNS:
                    if pat.search(line):
                        hits.append({"file": rel, "line": ln, "marker": name})
                        break
    except OSError:
        continue
print(json.dumps(hits))
PYEOF
)
  STUB_COUNT=$(echo "$STUB_HITS_JSON" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
fi

# 3. Python typecheck on changed .py files.
# Filter to files that still exist on disk: a file deleted in the working tree
# (vs an older baseline) shows up in the diff but must not be handed to
# py_compile/mypy — the tool errors "No such file" and fails the whole check.
# The stub scan already guards this way (os.path.isfile); mirror it here.
PY_CHECK="skipped"
PY_FILES=$(printf '%s\n' "$CHANGED_ALL" | grep '\.py$' | grep -v '\.venv/' | grep -v '__pycache__' | while IFS= read -r f; do [[ -f "$f" ]] && printf '%s\n' "$f"; done || true)
if [[ -n "$PY_FILES" ]]; then
  # Prefer backend venv mypy. Fall back to py_compile.
  MYPY=$WORKSHOP/backend/.venv/bin/mypy
  if [[ -x "$MYPY" ]]; then
    if echo "$PY_FILES" | xargs -r "$MYPY" --no-error-summary --ignore-missing-imports >/dev/null 2>&1; then
      PY_CHECK="ok"
    else
      PY_CHECK="fail"
    fi
  elif [[ -x "$WORKSHOP/backend/.venv/bin/python" ]]; then
    if echo "$PY_FILES" | xargs -r "$WORKSHOP/backend/.venv/bin/python" -m py_compile >/dev/null 2>&1; then
      PY_CHECK="ok"
    else
      PY_CHECK="fail"
    fi
  else
    # Python changed and neither mypy nor the venv python exists: this gate has
    # an opinion it cannot form.
    AMBIGUOUS+=("py_typecheck_unavailable")
  fi
fi

# 4. TypeScript typecheck on changed .ts/.tsx files via frontend tsc.
TS_CHECK="skipped"
TS_FILES=$(printf '%s\n' "$CHANGED_ALL" | grep -E '\.(ts|tsx)$' | grep -v 'node_modules' || true)
if [[ -n "$TS_FILES" ]]; then
  # Resolve tsc from EITHER layout. A root `npm install` with workspaces hoists
  # typescript to the root node_modules, so the frontend-local .bin/tsc that
  # used to be here simply is not there after a clean install -- and this check
  # degrades to ts_typecheck_unavailable, which reads as "no typechecker on this
  # box" rather than "it moved one directory up".
  TSC=$WORKSHOP/frontend/node_modules/.bin/tsc
  [[ -x "$TSC" ]] || TSC=$WORKSHOP/node_modules/.bin/tsc
  if [[ -x "$TSC" ]]; then
    # Run tsc against the whole frontend project (incremental, fast). This catches
    # cross-file errors that per-file checks miss. If no changed file is in
    # frontend/ or packages/ui/, skip — the change was in a different ts project
    # (apps/, mobile/).
    #
    # packages/ui counts as frontend for this purpose: frontend/tsconfig.json
    # includes ../packages/ui/src, so one tsc run covers the kit AND every call
    # site in the app. A kit change that breaks a consumer must fail HERE, which
    # is the whole point of hoisting the kit out of the app.
    if echo "$TS_FILES" | grep -qE '^(frontend|packages/ui)/'; then
      if (cd "$WORKSHOP/frontend" && timeout 90 "$TSC" -b --noEmit) >/dev/null 2>&1; then
        TS_CHECK="ok"
      else
        TS_CHECK="fail"
      fi
    fi
  elif echo "$TS_FILES" | grep -qE '^(frontend|packages/ui)/'; then
    AMBIGUOUS+=("ts_typecheck_unavailable")
  fi
fi

# 4.5 Design-system lint on ADDED lines in changed frontend .tsx files.
#     Enforces frontend/CLAUDE.md: no new inline hex / emoji / not-allowed.
#     "added lines only" mirrors the stub check — pre-existing legacy styling in
#     unmigrated pages never blocks; you just can't introduce new violations.
DESIGN_CHECK="skipped"
if printf '%s\n' "$CHANGED_ALL" | grep -qE '^(frontend|packages/ui)/src/.*\.tsx$'; then
  if [[ -x "$WORKSHOP/scripts/check_design.sh" ]]; then
    if bash "$WORKSHOP/scripts/check_design.sh" --added "$BASELINE" >/dev/null 2>&1; then
      DESIGN_CHECK="ok"
    else
      DESIGN_CHECK="fail"
    fi
  else
    AMBIGUOUS+=("design_check_unavailable")
  fi
fi

# 4.6 Pluggable-app invariant — apps must not be welded back into platform-core
#     files, and per-instance data must stay untracked (docs/apps-are-pluggable.md).
#     Gated: only runs when the diff touches the specific files the script inspects,
#     eliminating the unconditional repo-wide scan cost on unrelated diffs.
APPS_CHECK="skipped"
_APPS_CORE="backend/main.py backend/services/llm_router.py backend/services/app_registry.py backend/services/chat_context.py frontend/src/pages/Apps.tsx"
_APPS_FORBIDDEN="projects.json build_agents.json backend/apps.json commute/schedule.json commute/legs.json commute/targets.json commute/sources/registry.json"
_APPS_RELEVANT=false
for _apf in $_APPS_CORE $_APPS_FORBIDDEN; do
  if printf '%s\n' "$CHANGED_ALL" | grep -qxF "$_apf"; then
    _APPS_RELEVANT=true; break
  fi
done
if ! $_APPS_RELEVANT && printf '%s\n' "$CHANGED_ALL" | grep -qE '^commute/notes/'; then
  _APPS_RELEVANT=true
fi
if $_APPS_RELEVANT; then
  if [[ -x "$WORKSHOP/scripts/check_pluggable_apps.sh" ]]; then
    if bash "$WORKSHOP/scripts/check_pluggable_apps.sh" >/dev/null 2>&1; then
      APPS_CHECK="ok"
    else
      APPS_CHECK="fail"
    fi
  else
    AMBIGUOUS+=("pluggable_apps_check_unavailable")
  fi
fi
unset _APPS_CORE _APPS_FORBIDDEN _APPS_RELEVANT _apf

# 4.5b The backend test suite, as a ratchet.
#
# THIS GATE RAN MYPY AND NEVER PYTEST until 2026-08-31, for one concrete reason:
# the suite hung at 32% and could not finish, so there was nothing to gate on.
# That hang is fixed (a test called the real `compact_thread`, which spawns a CLI
# turn that never returns under pytest), the suite completes in ~3.5 minutes, and
# the reason is gone.
#
# What the absence cost, measured the day it was wired: `GET /api/tasks` renamed
# its response keys on 2026-08-17, seven frontend call sites kept reading the old
# ones, and /tickets rendered "No tickets yet" over 188 open tickets for two
# weeks. Every typecheck and every design check passed throughout, because each
# call site hand-wrote the type it was reading.
#
# Known failures are pinned by id, not counted, so a fixed test and a newly
# broken one cannot cancel out. Only a failure absent from the baseline fails
# this check. See check_backend_tests.py for the full contract.
#
# ONLY WHEN PYTHON CHANGED. It is the slowest check here by an order of
# magnitude, and a frontend-only diff cannot move it.
BACKEND_TESTS_CHECK="skipped"
# NOT WHEN WE ARE ALREADY INSIDE A SUITE RUN. tests/test_change_gates.py executes
# a real reviewer.sh, so without this the suite runs the reviewer runs the suite.
# check_backend_tests.py sets this for its pytest subprocess; see its own note.
if [[ -n "$PY_FILES" && -z "${WORKSHOP_BACKEND_TESTS_RUNNING:-}" ]]; then
  if [[ -f "$WORKSHOP/scripts/check_backend_tests.py" && -f "$WORKSHOP/scripts/backend_tests_baseline.json" ]]; then
    if python3 "$WORKSHOP/scripts/check_backend_tests.py" --quiet >/dev/null 2>&1; then
      BACKEND_TESTS_CHECK="ok"
    else
      BACKEND_TESTS_CHECK="fail"
    fi
  else
    AMBIGUOUS+=("backend_tests_check_unavailable")
  fi
fi

# 4.6b No hardcoded machine or operator identity. STILL UNCONDITIONAL — unlike
# the apps check this has no relevance precondition, because a path or a
# hostname can land in any file and the whole point is that it looks fine from
# inside the install that has it. It still scans EVERY tracked file.
#
# `--since "$BASELINE"` (added 2026-08-23) is what makes it a REVIEW gate rather
# than a tree-health readout, and it is the same treatment the two ratchets
# above got. It counts identity hits on the whole tree at the run's base ref AND
# on the working tree, and fails only on a category THIS RUN made worse. Before
# that it was ABSOLUTE and whole-tree: any hit anywhere failed the review, so
# being unconditional made it fatal. On 2026-08-23 it was rejecting every run in
# this tree over 25 inherited references — 19 operator-name hits in two backend
# test files, 3 login-name hits in a frontend test, 3 more — in files no worker
# had touched, several older than the check itself. Each run collected a
# rejection it could not act on (fixing it means editing another agent's files,
# which the repo rule forbids), retried, hit ralph.sh's no-progress thrash guard
# and exited 6: 2,066 loop jobs since 2026-07-18 all reported tasks_done: 0.
#
# The check is NOT weakened and nothing is allowlisted. A bare run still reports
# the real absolute debt (25 today) and delta mode still prints it as FYI; the
# needles are still asked of the machine rather than written down; introducing
# ONE new reference anywhere in the tree still fails. A run is simply not blamed
# for identity references that existed at its own base commit.
HARDCODE_CHECK="skip"
if [[ -x "$WORKSHOP/scripts/check_no_hardcoding.sh" ]]; then
  if bash "$WORKSHOP/scripts/check_no_hardcoding.sh" --since "$WS_BASELINE" >/dev/null 2>&1; then
    HARDCODE_CHECK="ok"
  else
    HARDCODE_CHECK="fail"
  fi
else
  AMBIGUOUS+=("hardcoding_check_unavailable")
fi
export HARDCODE_CHECK

# 4.6b-ii THE SAME CHECK, OVER docs/ AND *.md. Added 2026-08-27.
#
# `check_no_hardcoding.sh` excludes `docs/**` and `*.md` unless `--all` is
# passed, and nothing passed it. So 124 tracked knowledge notes, five security
# audits of the operator's own machine and 38 KB of his raw voice dictation sat
# outside every gate this repo actually runs -- while the run above printed
# "no-hardcoding: clean" and meant only "clean in the half we looked at". A
# flag that exists and is never passed is not coverage; it is the appearance of
# coverage, which is worse.
#
# So the docs half runs too, as its own gate with its own verdict, delta-mode
# for the same reason the code half is delta-mode: measured 2026-08-27 the
# tracked docs tree carries 116 home paths, 95 identity references and 1 email
# address, none of them written by whatever run is being reviewed, and failing
# every run on inherited debt is precisely what produced 2,066 loop jobs with
# `tasks_done: 0`. Delta mode still fails the moment a diff ADDS one, which is
# the behaviour that was missing entirely.
#
# The ABSOLUTE docs number is not swept under this. `scripts/check_no_hardcoding.sh
# --all` reports it on demand, and `scripts/check_fresh_clone.sh` runs it in
# absolute mode against a real clone and FAILS on it -- that is the target that
# is meant to be run and meant to be able to fail.
HARDCODE_DOCS_CHECK="skip"
if [[ -x "$WORKSHOP/scripts/check_no_hardcoding.sh" ]]; then
  if bash "$WORKSHOP/scripts/check_no_hardcoding.sh" --all --since "$WS_BASELINE" >/dev/null 2>&1; then
    HARDCODE_DOCS_CHECK="ok"
  else
    HARDCODE_DOCS_CHECK="fail"
  fi
fi
export HARDCODE_DOCS_CHECK

# 4.6c One definition per enum (R3, coherence-overhaul-2026-08-16.md). Gated on
# touching a file the checker actually scans (backend .py, frontend/packages-ui
# .ts/.tsx) — cheap either way (~0.6s full-repo), but a docs-only or shell-only
# diff has nothing this check could find. `--only-changed` restricts FAILURE to
# duplicate groups where at least one declaring file is in THIS diff: touching
# either side of an existing duplicate, or introducing a new one, keeps you
# honest, but a duplicate sitting untouched elsewhere in the tree does not
# block unrelated work — see the script's own header for why an unconditional
# whole-tree gate would just get this check disabled.
ONE_DEF_CHECK="skipped"
ONE_DEF_COUNT=0
if printf '%s\n' "$CHANGED_ALL" | grep -qE '^(backend/.*\.py|frontend/src/.*\.tsx?|packages/ui/src/.*\.tsx?)$'; then
  if [[ -f "$WORKSHOP/scripts/check_one_definition.py" ]]; then
    ONE_DEF_JSON=$(python3 "$WORKSHOP/scripts/check_one_definition.py" --only-changed "$BASELINE" --json 2>/dev/null)
    if [[ -n "$ONE_DEF_JSON" ]]; then
      ONE_DEF_COUNT=$(printf '%s' "$ONE_DEF_JSON" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('gating_groups', [])))" 2>/dev/null || echo 0)
      if [[ "$ONE_DEF_COUNT" -gt 0 ]]; then
        ONE_DEF_CHECK="fail"
      else
        ONE_DEF_CHECK="ok"
      fi
    else
      AMBIGUOUS+=("one_definition_check_errored")
    fi
  else
    AMBIGUOUS+=("one_definition_check_unavailable")
  fi
fi

# 4.6c-ii Module-layout ratchet (ADR 0024, target-architecture-2026-08-17.md
# §2.2/§4 item 7). Gated on touching a backend .py file — same precondition
# shape as the one-definition check above, and for the same reason: a
# frontend-only or docs-only diff has nothing this check could move. Full
# whole-backend measurement either way (~1.5s per side), not scoped to the
# diff, because the metrics themselves (SCC size, upward-violation count, ...)
# are properties of the WHOLE graph, not of one file.
#
# `--since "$BASELINE"` (added 2026-08-23) is what makes it a REVIEW gate
# rather than a tree-health readout. It measures the same whole backend at the
# run's base ref AND on the working tree, and fails only on a metric THIS RUN
# made worse. Before that it compared the whole tree to the STATIC pinned
# baseline, so any debt already present when a worker started — another
# agent's concurrent commit, the operator's own edit, drift from weeks ago —
# failed that worker. On 2026-08-23 it was failing on three inherited metrics
# (function_local_imports 552 -> 569, largest_scc_size 110 -> 124,
# big_file_count 22 -> 24) that no single run had caused. Every run collected
# that rejection, retried, hit ralph.sh's no-progress thrash guard and exited
# 6: 2,066 loop jobs since 2026-07-18 all reported tasks_done: 0.
#
# The ratchet is NOT loosened. The pinned baseline still governs direction over
# time (a bare run still reports the absolute debt, and delta mode still prints
# it as FYI), --write-baseline still requires a deliberate re-pin, and
# check_ratchets_not_loosened.py below still blocks re-pinning inside a review.
# A run is simply not blamed for debt that existed at its own base commit.
MODULE_LAYOUT_CHECK="skipped"
if printf '%s\n' "$CHANGED_ALL" | grep -qE '^backend/.*\.py$'; then
  _delta_check MODULE_LAYOUT_CHECK "check_module_layout.py" "module_layout_baseline.json"
fi

# 4.6c-iii No new process-local mutable globals (tenancy clause, §2.6 item 3).
# Same gating precondition and same reasoning as module-layout above: this is
# a whole-backend property (a global declared in file A can be mutated from
# file B), not a per-file one, so it is not scoped to the diff — only gated
# on whether there is any backend .py change to re-measure against.
NO_PROCESS_GLOBALS_CHECK="skipped"
if printf '%s\n' "$CHANGED_ALL" | grep -qE '^backend/.*\.py$'; then
  _delta_check NO_PROCESS_GLOBALS_CHECK "check_no_process_globals.py" "no_process_globals_baseline.json"
fi

# 4.6c-iv A pinned ratchet baseline may not be LOOSENED by this diff.
# Every other ratchet reads its baseline out of the WORKING TREE, so a worker
# that rewrites the baseline instead of fixing the regression is, to the
# checker, indistinguishable from a clean tree. That is not hypothetical: on
# 2026-08-22 a worker failing the module-layout gate ran --write-baseline,
# committed the result (971f7f3), passed, and landed in_review. Gated on this
# diff touching a baseline file at all, which is the only way it can happen.
# This gate is what makes the others hold. See the script's own header.
RATCHETS_NOT_LOOSENED_CHECK="skipped"
if printf '%s\n' "$CHANGED_ALL" | grep -qE '^scripts/.*baseline.*\.json$'; then
  if [[ -f "$WORKSHOP/scripts/check_ratchets_not_loosened.py" ]]; then
    if python3 "$WORKSHOP/scripts/check_ratchets_not_loosened.py" --baseline "$BASELINE" >/dev/null 2>&1; then
      RATCHETS_NOT_LOOSENED_CHECK="ok"
    else
      RATCHETS_NOT_LOOSENED_CHECK="fail"
    fi
  else
    AMBIGUOUS+=("ratchets_not_loosened_check_unavailable")
  fi
fi

# 4.6c-v No new mutable STATE file committed under a source directory (Layer 0 of
# managed-file-references-and-path-scoping-2026-08-18.md §9). Gated on this diff
# adding/changing a json/jsonl/db under backend|frontend|packages — that is the
# only way an offender enters the tree, and a docs- or code-only diff has nothing
# to move. Pinned-baseline ratchet (currently allowlists backend/grants.json,
# which is relocated at startup and deleted post-verify); it fails only on a NEW
# state file. See check_no_state_in_source.py's own header.
NO_STATE_IN_SOURCE_CHECK="skipped"
if printf '%s\n' "$CHANGED_ALL" | grep -qE '^(backend|frontend|packages)/.*\.(json|jsonl|db|sqlite|sqlite3)$'; then
  _delta_check NO_STATE_IN_SOURCE_CHECK "check_no_state_in_source.py" "no_state_in_source_baseline.json"
fi

# 4.6c-vi No NEW raw OS path in agent-facing text (Layer 4 of
# managed-file-references-and-path-scoping-2026-08-18.md §9). Gated on this diff
# touching one of the scanned agent-facing surfaces (chat_context / build_streaming
# / refs, or chat UI copy) — a diff that touches none cannot add an offender.
# Pinned-baseline ratchet: fails only on a NEW raw path. Baseline shrinks to zero
# once Layer 1 removes the runtime-briefing cwd. See check_no_raw_paths.py.
NO_RAW_PATHS_CHECK="skipped"
if printf '%s\n' "$CHANGED_ALL" | grep -qE '^(backend/services/(chat_context|build_streaming|refs)\.py|frontend/src/components/chat/.*\.(ts|tsx))$'; then
  _delta_check NO_RAW_PATHS_CHECK "check_no_raw_paths.py" "no_raw_paths_baseline.json"
fi

# 4.6c-iiib No platform source file may name a specific app (CLAUDE.md; §6 of
# docs/platform-redesign/app-is-the-entity-2026-08-20.md). STILL UNCONDITIONAL,
# and deliberately so: check_pluggable_apps.sh below is gated to five files and
# reported "clean" for five weeks while 143 tracked files of one operator's
# apps sat in the platform tree. A rule this central cannot be enforced only on
# diffs that happen to touch the right path. It RUNS on every diff, docs-only
# ones included, and it still scans EVERY tracked file.
#
# What changed on 2026-08-23 is what it is COMPARED against, not what it runs
# on. `--since "$BASELINE"` scans the whole tree at the run's base ref as well,
# and fails only on a count THIS RUN made worse. Being unconditional is exactly
# what made the old absolute form fatal: it compared the whole tree to the
# STATIC pinned baseline on EVERY run, so while the tree carried inherited debt
# (literal_sites 309 -> 314 on 2026-08-23) it rejected every worker — including
# docs-only diffs that could not possibly have added a literal. Each run
# collected a rejection it could not act on, retried, hit ralph.sh's no-progress
# thrash guard and exited 6: 2,066 loop jobs since 2026-07-18 all reported
# tasks_done: 0, while reviewer-approved work sat stranded on locked branches.
#
# The ratchet is NOT loosened and the rule is NOT scoped. The counts may still
# only go down over time: a bare run reports the real absolute debt, delta mode
# prints inherited debt as FYI, --write-baseline still refuses an upward pin
# without --because, and check_ratchets_not_loosened.py still blocks re-pinning
# inside a review. See check_no_app_names.py, and §0.2 of
# app-separation-and-github-2026-08-20.md.
NO_APP_NAMES_CHECK="skipped"
_delta_check NO_APP_NAMES_CHECK "check_no_app_names.py" "no_app_names_baseline.json"

# 4.6c-iiic No installation-specific fact in PLATFORM prompt text. Same rule as
# no-app-names, applied to prompt strings instead of code: CLAUDE.md's "L0 to L2
# are PLATFORM text ... it has no gate yet, so it is on you". It had not been on
# anyone -- 11 violations on 2026-08-27, including "live for him until the bundle
# is rebuilt", `~/workshop/scripts/project_tasks.py` in two personas, and a
# nine-name egress allowlist with a tailnet assumption.
#
# UNCONDITIONAL, for the reason spelled out above check_no_app_names.py: a rule
# this central cannot be enforced only on diffs that happen to touch the right
# path, and a new persona is by definition written in a file nobody thought to
# add to a gate list.
#
# ABSOLUTE, not a ratchet, and it can be: the eleven were fixed in the commit
# that added the checker, so the tree is at zero and there is nothing inherited
# to forgive. `allowed` in scripts/prompt_layer_rules.json is the escape hatch
# and is empty; check_ratchets_not_loosened.py blocks re-pinning inside a review.
PROMPT_LAYERS_CHECK="skipped"
if [[ -f "$WORKSHOP/scripts/check_prompt_layers.py" ]]; then
  if python3 "$WORKSHOP/scripts/check_prompt_layers.py" >/dev/null 2>&1; then
    PROMPT_LAYERS_CHECK="ok"
  else
    PROMPT_LAYERS_CHECK="fail"
  fi
else
  AMBIGUOUS+=("prompt_layers_check_unavailable")
fi

# THE APPLICATION IS NOT A PLACE TO LEAVE NOTES FOR THE OPERATOR. No screen
# explains its own mechanism in prose: no paragraph under a control describing
# how a feature ranks, no parenthetical teaching a file format, no help line
# restating the label above it.
#
# A RATCHET, not an absolute, and that is the difference from the prompt-layer
# gate above. That one could start at zero because its eleven violations were
# all fixed in one commit; this one inherited 40 across the app, and pretending
# otherwise would mean either a gate that fails on every run (which is how the
# app-names gate once thrashed every worker) or 40 deletions nobody reviewed.
# The Memory surfaces the operator reported ARE at zero.
#
# UNCONDITIONAL, for the reason the two whole-tree checks above are: the file a
# new explainer gets written in is by definition a file nobody thought to add to
# a gate list. Delta mode keeps it honest about the 40 it inherited.
EXPLAINER_CHECK="skipped"
_delta_check EXPLAINER_CHECK "check_explainer_text.py" "explainer_baseline.json"

# One state mechanism for server reads (ADR 0025, "The check" clause 3,
# generalised to usePoll itself now that the query layer exists). Counts the two
# LEGACY mechanisms under frontend/src -- usePoll call sites and polling
# setInterval timers -- and fails a run that adds one.
#
# UNCONDITIONAL, not gated on which files changed, for the same reason
# check_no_app_names.py is: a change-gated version of that check inspected five
# files and reported "clean" for five weeks while the violation grew. The thing
# this catches is a NEW read written against the old mechanism, and the file it
# is written in is by definition a file nobody thought to add to a gate list --
# the dashboard added four usePoll sites in four brand-new files days after the
# query layer landed. Delta mode keeps it honest about the 43 it inherited.
FRONTEND_STATE_CHECK="skipped"
_delta_check FRONTEND_STATE_CHECK "check_frontend_state.py" "frontend_state_baseline.json"

# A CHANGE DELETES THE SURFACE IT REPLACES, in the same commit that adds the
# new one. That rule is the settled model of record
# (threads-branches-and-pages-2026-08-29.md section 7) and it is the only
# invariant in this repo that has never held: 1 of 22 retirement commitments
# landed, five design generations are routed at once, and the page marked for
# deletion GREW while marked. Every rule here that DOES hold is enforced by
# something that fails a run; that one was enforced by prose.
#
# So this counts what nothing reaches -- unreferenced frontend modules (via
# knip, which sees dynamic `import()` where a grep does not), top-level routes
# in no nav, backend modules no Python file imports, API areas nothing calls --
# and refuses to let any of those grow.
#
# UNCONDITIONAL, for the reason the whole-tree checks above are: a superseded
# surface is by definition in a file nobody thought to add to a gate list, and
# the FILE THAT ADDS the replacement is usually not the file holding the thing
# it replaced. Per-item baselines keep it honest about the debt it inherited --
# only an item this run introduced fails.
DEAD_SURFACE_CHECK="skipped"
_delta_check DEAD_SURFACE_CHECK "check_dead_surface.py" "dead_surface_baseline.json"

# 4.6c-2b A response shape is NAMED from the generated client, not hand-written.
# The companion to check_generated_client.py above: that one keeps
# frontend/src/api/schema.gen.ts true to the backend, this one is what makes the
# frontend USE it. Counts the response boundaries carrying a hand-written type
# and the ones carrying no type at all, with the COLLECTION reads split out as
# their own number because that is where the failure is invisible.
#
# The measured cost of not having this: GET /api/tasks and GET /api/jobs moved
# to Page[T] on 2026-08-17 (7e4aacbf) and seven frontend call sites never
# followed. Every one still typechecked, because every one had hand-written its
# own type, so /tickets printed "No tickets yet" over 188 open tickets for two
# weeks with no error anywhere. An empty list is what a missing key looks like.
#
# UNCONDITIONAL, for the reason the whole-tree checks above are: the file a new
# hand-written response type is written in is by definition a file nobody
# thought to add to a gate list. A RATCHET rather than an absolute because the
# tree carries hundreds, and because a route with no `response_model=` has no
# generated type to name yet -- that half of the debt is RESPONSE_MODEL_CHECK's.
RESPONSE_TYPES_CHECK="skipped"
_delta_check RESPONSE_TYPES_CHECK "check_response_types.py" "response_types_baseline.json"

# 4.6c-x THE FRESH-CLONE ASSERTION: what a stranger's clone actually contains.
# UNCONDITIONAL, like the two whole-tree checks above it, and for the same
# reason -- the gate this one supplements (check_pluggable_apps.sh) was scoped
# to five files and reported "clean" for five weeks while the violation grew.
#
# This is the check `docs/INDEX-knowledge-2026-08-26.md:542` has cited as the
# only acceptable evidence that app separation is done, and which did not exist
# until 2026-08-27. It clones the repo and asks what came out, which is the one
# question no in-tree grep can answer: from inside an install, an untracked
# file, a gitignored directory and a tracked one all look the same.
#
# ALL FIVE assertions in the design doc are implemented as of 2026-09-01: the
# three tree counts (no app source, no personal data, no hardcoded identity
# INCLUDING docs) plus the two that actually run the thing -- build the clone
# with the app source DELETED, and boot it against a throwaway Postgres cluster
# with an empty catalog. Until then those two printed as NOT IMPLEMENTED, and a
# check that has never run reads, to every agent after it, exactly like a check
# that passed.
#
# `--since` because absolute mode fails today on 106 inherited app-source files
# and 210 inherited doc references -- gating a worker on that is the documented
# cause of 2,066 loop jobs with tasks_done: 0. Absolute mode is what a human
# runs before claiming separation is done: `bash scripts/check_fresh_clone.sh`.
#
# COST HERE IS ~14s, NOT ~80s, and the reason is the pin file rather than a
# policy someone has to remember: both run-it assertions are recorded in
# scripts/fresh_clone_baseline.json as expected failures, so review mode prints
# NOT YET and does not re-execute them. The pin file IS the recorded base state
# -- it is committed, it says the assertion did not hold as of a date, and
# check_ratchets_not_loosened.py rejects growing it inside a review -- so a diff
# cannot regress a pinned assertion without first deleting a pin, which is a
# visible edit. The 80 seconds arrive on the day the pin is deleted, which is
# the day this starts being able to catch something.
FRESH_CLONE_CHECK="skipped"
_delta_check FRESH_CLONE_CHECK "check_fresh_clone.sh" "fresh_clone_baseline.json"

# 4.6b-iv dev_only catalog gate — the development app is absent from GET /apps
# when WORKSHOP_DEV_MODE is not '1'. Proven by check_dev_mode_off.sh, which
# runs backend/tests/test_dev_only_gate.py against the service layer with the
# database patched out. ALWAYS runs (functional, not delta): the gate is a
# property of the code, not of any particular diff.
DEV_MODE_OFF_CHECK="skipped"
_delta_check DEV_MODE_OFF_CHECK "check_dev_mode_off.sh"

# 4.6c-iv response_model= on every route (ADR 0023, §2.4). Gated on touching a
# routes/ file specifically (narrower than the two checks above, because this
# one IS scoped per-file) and uses `--only-changed` exactly like
# check_one_definition.py: FAILURE is restricted to route files this diff
# actually touches, so pre-existing routes/ debt elsewhere never blocks
# unrelated work — see the script's own header.
RESPONSE_MODEL_CHECK="skipped"
if printf '%s\n' "$CHANGED_ALL" | grep -qE '^backend/routes/.*\.py$'; then
  if [[ -f "$WORKSHOP/scripts/check_response_model.py" && -f "$WORKSHOP/scripts/response_model_baseline.json" ]]; then
    if python3 "$WORKSHOP/scripts/check_response_model.py" --only-changed "$BASELINE" >/dev/null 2>&1; then
      RESPONSE_MODEL_CHECK="ok"
    else
      RESPONSE_MODEL_CHECK="fail"
    fi
  else
    AMBIGUOUS+=("response_model_check_unavailable")
  fi
fi

# 4.6c-v No raw browser-storage access (ADR 0025 clause 4). Unlike the three
# above this is ABSOLUTE, not a ratchet, and it can be: the tree is already at
# zero. Every localStorage/sessionStorage call now goes through
# frontend/src/lib/storage.ts, which namespaces the key by user id. A new raw
# call is therefore not "debt getting slightly worse", it is a key that a
# second signed-in account on the same machine can read, so there is nothing to
# ratchet toward and no reason to allow one.
NO_RAW_STORAGE_CHECK="skipped"
if printf '%s\n' "$CHANGED_ALL" | grep -qE '^(frontend/src|mobile)/.*\.tsx?$'; then
  if [[ -f "$WORKSHOP/scripts/check_no_raw_storage.py" ]]; then
    if python3 "$WORKSHOP/scripts/check_no_raw_storage.py" >/dev/null 2>&1; then
      NO_RAW_STORAGE_CHECK="ok"
    else
      NO_RAW_STORAGE_CHECK="fail"
    fi
  else
    AMBIGUOUS+=("no_raw_storage_check_unavailable")
  fi
fi

# 4.6c-3 Duplicated helper bodies. RATCHET, not absolute: 10 groups exist today
# and failing every task until they are all consolidated is how a checker gets
# disabled. The baseline is keyed by body hash to file count, so a copy MOVING
# between files is not a regression (which matters while the chat rename is in
# flight) but a new body or an extra copy is.
#
# Note its honest limit, stated in its own header: it catches COPY-PASTE, not
# independent reimplementation. None of the three fmtTs copies it was written
# for would have matched lib/time.ts's timeAbsolute, because one used isNaN and
# the other Number.isNaN. So a green result here is not evidence that a helper
# has no equivalent already.
DUPLICATE_HELPERS_CHECK="skipped"
if printf '%s\n' "$CHANGED_ALL" | grep -qE '^(frontend/src|packages/ui/src)/.*\.tsx?$'; then
  if [[ -f "$WORKSHOP/scripts/check_duplicate_helpers.py" && -f "$WORKSHOP/scripts/duplicate_helpers_baseline.json" ]]; then
    if python3 "$WORKSHOP/scripts/check_duplicate_helpers.py" --only-changed "$BASELINE" >/dev/null 2>&1; then
      DUPLICATE_HELPERS_CHECK="ok"
    else
      DUPLICATE_HELPERS_CHECK="fail"
    fi
  else
    AMBIGUOUS+=("duplicate_helpers_check_unavailable")
  fi
fi

# 4.6c-2 Generated-client staleness (ADR 0023). ABSOLUTE, like raw storage and
# unlike the ratchets: `frontend/src/api/schema.gen.ts` is a committed build
# artifact, so "stale" means the committed types no longer describe the API the
# backend serves, which is the exact drift the generated client exists to make
# impossible. There is nothing to ratchet toward and no acceptable number of
# stale lines. The remedy is `npm run gen:api-types` and commit, under two
# seconds. Gated on a diff that could move the schema: a backend route or model,
# or the generator itself. Note it also fires on a route DOCSTRING edit, because
# FastAPI puts the docstring in the schema as `description`; that is friction by
# design, since dropping descriptions would mean the committed file is no longer
# what the generator produces.
GENERATED_CLIENT_CHECK="skipped"
# Gated on any backend source outside tests, plus the generator and the artifact
# itself. Deliberately WIDER than routes/: a Pydantic model or a closed enum
# living in `services/` or `db/models.py` can flow into a response_model and move
# the schema, and a missed staleness silently reintroduces exactly the drift the
# generated client exists to kill. Measured cost of widening: 1.8s, and it cannot
# produce a false failure, because the check only fails when the committed
# artifact genuinely no longer matches what the generator produces. `backend/tests/`
# is excluded because it cannot reach the schema.
if printf '%s\n' "$CHANGED_ALL" \
   | grep -vE '^backend/tests/' \
   | grep -qE '^(backend/|scripts/gen_openapi\.py|frontend/src/api/)'; then
  if [[ -f "$WORKSHOP/scripts/check_generated_client.py" ]]; then
    if python3 "$WORKSHOP/scripts/check_generated_client.py" >/dev/null 2>&1; then
      GENERATED_CLIENT_CHECK="ok"
    else
      GENERATED_CLIENT_CHECK="fail"
    fi
  else
    AMBIGUOUS+=("generated_client_check_unavailable")
  fi
fi

# 4.5f Opaque response FIELDS, ratcheted (ADR 0023 clause 6, second half).
# `check_response_model` asks whether a ROUTE names a model; this asks whether
# that model's FIELDS say anything. A field typed `dict[str, Any]` satisfies the
# route-level check and still generates `Record<string, never>` in the client,
# so the frontend hand-writes the shape and drifts from it -- which is the exact
# defect the response_model work exists to remove, one level down. Same trigger
# set as the generated-client check, and for the same reason: a model in
# `services/` or `db/` can flow into a response and move the schema.
OPAQUE_FIELDS_CHECK="skipped"
if printf '%s\n' "$CHANGED_ALL" \
   | grep -vE '^backend/tests/' \
   | grep -qE '^(backend/|scripts/gen_openapi\.py)'; then
  _delta_check OPAQUE_FIELDS_CHECK "check_opaque_fields.py" "opaque_fields_baseline.json"
fi

# 4.5g Every GET route answers (ADR 0023 clause 6, third half).
# 4.5e and 4.5f read the DECLARATION: is a model named, and do its fields say
# anything. Neither can see the VALUE, and no static check can -- a payload is
# assembled from service calls across modules. A `response_model` is a runtime
# behaviour change (FastAPI validates every response against it and 500s on a
# mismatch), so the only oracle is calling the route. On 2026-08-17 a field
# declared `str` and handed a dict returned 500 from THE chat sync endpoint for
# seven minutes with both static checks green.
# Same trigger set as 4.5f. Exit 3 is "no backend to probe", which is ambiguous
# rather than a pass: a check that never ran must not read as clean.
ROUTES_ANSWER_CHECK="skipped"
if printf '%s\n' "$CHANGED_ALL" \
   | grep -vE '^backend/tests/' \
   | grep -qE '^(backend/|scripts/gen_openapi\.py)'; then
  if [[ -f "$WORKSHOP/scripts/check_routes_answer.py" ]]; then
    python3 "$WORKSHOP/scripts/check_routes_answer.py" >/dev/null 2>&1
    case $? in
      0) ROUTES_ANSWER_CHECK="ok" ;;
      1) ROUTES_ANSWER_CHECK="fail" ;;
      *) AMBIGUOUS+=("routes_answer_check_could_not_run") ;;
    esac
  else
    AMBIGUOUS+=("routes_answer_check_unavailable")
  fi
fi

# 4.6c-vi The memory system still answers (scripts/verify_memory.py). GATES,
# and it is here because the memory acceptance harness existed for a week
# wired into nothing at all: reviewer.sh ran check_one_definition,
# check_no_app_names and field_occupancy and never ran this, so on 2026-08-23
# every scoped search in the system returned zero documents -- the platform's
# own model of record unfindable by every agent including the operator's chat
# -- and no gate said a word. A harness nobody runs is a harness that reports
# whatever it likes.
#
# Gated on the diff touching the retrieval path (the index, the share policy,
# the scope tree, the tool the agents call, or the harness itself), because
# this drives the REAL stores and takes seconds, not milliseconds; it is not
# free enough to run on a CSS change. `--retrieval` runs the "can an agent
# still find things" subset rather than the whole harness -- see
# RETRIEVAL_CHECKS in verify_memory.py: the learnings checks carry a standing
# unrelated finding, and a gate that fails on day one for reasons outside the
# diff is a gate somebody switches off. The `-x` venv guard is the same one
# 4.6d uses and degrades the same way: a worktree with no backend venv reports
# ambiguous rather than a pass, since a check that never ran must not read as
# clean.
MEMORY_REACHABLE_CHECK="skipped"
if printf '%s\n' "$CHANGED_ALL" \
   | grep -qE '^(backend/services/(semantic_index|memory_policy|knowledge|scope_tree)\.py|backend/mcp/workshop_query_server\.py|scripts/verify_memory\.py)$'; then
  if [[ -x "$WORKSHOP/backend/.venv/bin/python" && -f "$WORKSHOP/scripts/verify_memory.py" ]]; then
    if (cd "$WORKSHOP/backend" && "$WORKSHOP/backend/.venv/bin/python" \
          "$WORKSHOP/scripts/verify_memory.py" --retrieval) >/dev/null 2>&1; then
      MEMORY_REACHABLE_CHECK="ok"
    else
      MEMORY_REACHABLE_CHECK="fail"
    fi
  else
    AMBIGUOUS+=("memory_reachable_check_unavailable")
  fi
fi

# 4.6d Field-occupancy ratchet (R2) and doc-status ratchet (R1) are measurement,
# not gates — R2 is explicit that a newly-introduced zero-occupancy field must
# be reported loudly, not fail the build on first introduction (a design that
# has not landed yet is a question, not automatically a defect, and R1's docs
# are historical debt no single diff is responsible for clearing). Both are
# ADDITIVE fields on the JSON output; neither can flip `complete` to false.
OCCUPANCY_NOTICE=""
if printf '%s\n' "$CHANGED_ALL" | grep -qE '^backend/(db/(models|ticket_sync)\.py|services/(tasks|jobs|chat_store|learnings)\.py)$'; then
  if [[ -x "$WORKSHOP/backend/.venv/bin/python" && -f "$WORKSHOP/scripts/field_occupancy_baseline.json" ]]; then
    OCC_OUT=$(cd "$WORKSHOP/backend" && "$WORKSHOP/backend/.venv/bin/python" "$WORKSHOP/scripts/field_occupancy.py" --fail-on-new-zero 2>/dev/null)
    OCC_RC=$?
    if [[ $OCC_RC -eq 1 ]]; then
      OCCUPANCY_NOTICE="$OCC_OUT"
    fi
  fi
fi

DOC_STATUS_NOTICE=""
if printf '%s\n' "$CHANGED_ALL" | grep -q '^docs/platform-redesign/.*\.md$'; then
  if [[ -f "$WORKSHOP/scripts/doc_status.py" ]]; then
    DOC_STATUS_JSON=$(python3 "$WORKSHOP/scripts/doc_status.py" --json 2>/dev/null)
    if [[ -n "$DOC_STATUS_JSON" ]]; then
      # Only surface docs the DIFF actually touches — the other ~60 docs'
      # pre-existing no-status/misleading state is not this diff's problem.
      # Data passed via env vars, not stdin: this command already needs stdin
      # for the heredoc script itself (mirrors the STUB_HITS_JSON block above).
      DOC_STATUS_NOTICE=$(DOC_STATUS_JSON="$DOC_STATUS_JSON" CHANGED_ALL="$CHANGED_ALL" python3 - <<'PYEOF'
import json, os
data = json.loads(os.environ.get("DOC_STATUS_JSON") or "{}")
changed = {l.rsplit("/", 1)[-1] for l in os.environ.get("CHANGED_ALL", "").splitlines()
           if l.startswith("docs/platform-redesign/")}
docs = data.get("docs", {})
notes = []
for name in sorted(changed):
    entry = docs.get(name)
    if not entry:
        continue
    if not entry.get("has_status"):
        notes.append(f"{name}: no status line")
    if entry.get("misleading"):
        notes.append(f"{name}: NOT-BUILT banner disputes an artifact that exists")
print("; ".join(notes))
PYEOF
)
    fi
  fi
fi

# 4.7 Executed-criteria results (optional). A task can carry machine-checkable
#     acceptance assertions; project_ralph.sh EXECUTES them in the scope dir and
#     passes the RESULTS here via REVIEWER_CRITERIA_FILE. Surfacing them makes the
#     reviewer judge the DIFF plus what was actually executed, never the worker's
#     own self-report/summary (which is NOT part of this reviewer's context at all).
#     Absent/unreadable file => behavior is exactly as before this field existed.
CRITERIA_JSON='[]'
CRIT_FAIL_COUNT=0
CRIT_FAIL_DESC=""
if [[ -n "${REVIEWER_CRITERIA_FILE:-}" && -r "${REVIEWER_CRITERIA_FILE:-}" ]]; then
  if python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if isinstance(d,list) else 1)" "$REVIEWER_CRITERIA_FILE" 2>/dev/null; then
    CRITERIA_JSON=$(cat "$REVIEWER_CRITERIA_FILE")
    IFS=$'\t' read -r CRIT_FAIL_COUNT CRIT_FAIL_DESC < <(python3 - "$REVIEWER_CRITERIA_FILE" <<'PYEOF'
import json, sys
try:
    r = json.load(open(sys.argv[1]))
    if not isinstance(r, list):
        r = []
except Exception:
    r = []
fails = [c for c in r if isinstance(c, dict) and not c.get("pass")]
desc = str(fails[0].get("criterion") or fails[0].get("desc") or "criterion") if fails else ""
# keep the reason single-line so the line-based reasons parser is not corrupted
print(f"{len(fails)}\t{desc.splitlines()[0][:120] if desc else ''}")
PYEOF
)
  fi
fi

# 5. Decide.
REASONS=()
COMPLETE="true"

if (( CRIT_FAIL_COUNT > 0 )); then
  REASONS+=("criteria_failed:${CRIT_FAIL_DESC}")
  COMPLETE="false"
fi

if [[ "$DESIGN_CHECK" == "fail" ]]; then
  REASONS+=("design_violations")
  COMPLETE="false"
fi
if [[ "$APPS_CHECK" == "fail" ]]; then
  REASONS+=("pluggable_app_violations")
  COMPLETE="false"
fi
if [[ "$HARDCODE_CHECK" == "fail" ]]; then
  REASONS+=("hardcoded_identity")
  COMPLETE="false"
fi
if [[ "$HARDCODE_DOCS_CHECK" == "fail" ]]; then
  REASONS+=("hardcoded_identity_in_docs")
  COMPLETE="false"
fi
if [[ "$FRESH_CLONE_CHECK" == "fail" ]]; then
  REASONS+=("fresh_clone_regression")
  COMPLETE="false"
fi
if [[ "$DEV_MODE_OFF_CHECK" == "fail" ]]; then
  REASONS+=("dev_mode_off_regression")
  COMPLETE="false"
fi
if [[ "$ONE_DEF_CHECK" == "fail" ]]; then
  REASONS+=("duplicate_definitions:${ONE_DEF_COUNT}")
  COMPLETE="false"
fi
if [[ "$RATCHETS_NOT_LOOSENED_CHECK" == "fail" ]]; then
  REASONS+=("ratchet_baseline_loosened")
fi
if [[ "$MODULE_LAYOUT_CHECK" == "fail" ]]; then
  REASONS+=("module_layout_regression")
  COMPLETE="false"
fi
if [[ "$NO_PROCESS_GLOBALS_CHECK" == "fail" ]]; then
  REASONS+=("process_globals_regression")
  COMPLETE="false"
fi
if [[ "$NO_STATE_IN_SOURCE_CHECK" == "fail" ]]; then
  REASONS+=("state_file_in_source")
  COMPLETE="false"
fi
if [[ "$NO_RAW_PATHS_CHECK" == "fail" ]]; then
  REASONS+=("raw_path_in_agent_text")
  COMPLETE="false"
fi
if [[ "$NO_APP_NAMES_CHECK" == "fail" ]]; then
  REASONS+=("app_name_in_platform_source")
  COMPLETE="false"
fi
if [[ "$PROMPT_LAYERS_CHECK" == "fail" ]]; then
  REASONS+=("installation_fact_in_platform_prompt")
  COMPLETE="false"
fi
if [[ "$EXPLAINER_CHECK" == "fail" ]]; then
  REASONS+=("explainer_text_in_the_application")
  COMPLETE="false"
fi
if [[ "$FRONTEND_STATE_CHECK" == "fail" ]]; then
  REASONS+=("frontend_state_regression")
  COMPLETE="false"
fi
if [[ "$DEAD_SURFACE_CHECK" == "fail" ]]; then
  REASONS+=("dead_surface_grew")
  COMPLETE="false"
fi
if [[ "$RESPONSE_TYPES_CHECK" == "fail" ]]; then
  REASONS+=("handwritten_response_type")
  COMPLETE="false"
fi
if [[ "$RESPONSE_MODEL_CHECK" == "fail" ]]; then
  REASONS+=("response_model_regression")
  COMPLETE="false"
fi
if [[ "$NO_RAW_STORAGE_CHECK" == "fail" ]]; then
  REASONS+=("raw_browser_storage")
  COMPLETE="false"
fi
if [[ "$GENERATED_CLIENT_CHECK" == "fail" ]]; then
  REASONS+=("generated_client_stale")
  COMPLETE="false"
fi
if [[ "$OPAQUE_FIELDS_CHECK" == "fail" ]]; then
  REASONS+=("opaque_response_field")
  COMPLETE="false"
fi
if [[ "$ROUTES_ANSWER_CHECK" == "fail" ]]; then
  REASONS+=("route_returns_5xx")
  COMPLETE="false"
fi
if [[ "$MEMORY_REACHABLE_CHECK" == "fail" ]]; then
  REASONS+=("memory_acceptance_failed")
  COMPLETE="false"
fi
if [[ "$DUPLICATE_HELPERS_CHECK" == "fail" ]]; then
  REASONS+=("duplicate_helper")
  COMPLETE="false"
fi
# OCCUPANCY_NOTICE and DOC_STATUS_NOTICE are deliberately NOT checked here —
# both are measurement (R1/R2), not gates. See their computation above for why.

if [[ "$DIFF_FILES" -eq 0 ]]; then
  # An empty diff normally fails the gate (nothing changed). But a command- or
  # operator-acceptance task ("restart the analyzer", "investigate and write
  # findings") legitimately produces no tracked-file diff; its acceptance command
  # or operator review is the real gate. project_ralph.sh sets REVIEWER_ALLOW_NO_DIFF
  # only for those tasks, so a normal reviewer-acceptance code task never gets the
  # exemption and the no_diff gate holds unchanged for it. (review F18)
  if [[ "${REVIEWER_ALLOW_NO_DIFF:-0}" == "1" ]]; then
    :
  else
    REASONS+=("no_diff")
    COMPLETE="false"
  fi
fi
if [[ "$STUB_COUNT" -gt 0 ]]; then
  REASONS+=("new_stubs:$STUB_COUNT")
  COMPLETE="false"
fi
if [[ "$BACKEND_TESTS_CHECK" == "fail" ]]; then
  REASONS+=("backend_tests_regressed"); COMPLETE="false"
fi
if [[ "$PY_CHECK" == "fail" ]]; then
  REASONS+=("py_typecheck_fail")
  COMPLETE="false"
fi
if [[ "$TS_CHECK" == "fail" ]]; then
  REASONS+=("ts_typecheck_fail")
  COMPLETE="false"
fi
if [[ "$STRICT" == "1" ]]; then
  [[ "$PY_CHECK" == "skipped" ]] && { REASONS+=("py_typecheck_unavailable_strict"); COMPLETE="false"; }
  [[ "$TS_CHECK" == "skipped" ]] && { REASONS+=("ts_typecheck_unavailable_strict"); COMPLETE="false"; }
fi

# 5. Verdict. Three outcomes, not two:
#   reject             — a hard check failed. The worker can see what and iterate;
#                        an ambiguity elsewhere is not the interesting fact here,
#                        so a reject stays a reject at every latitude.
#   block-for-decision — the gate would APPROVE, but only because a check that
#                        applied could not run, and this sweep is `strict`. Strict
#                        means ask rather than assume: the task blocks and the
#                        operator decides. This is a correct outcome, not a failure.
#   approve            — everything that applied passed.
VERDICT="approve"
BLOCKED_REASON=""
if [[ "$COMPLETE" != "true" ]]; then
  VERDICT="reject"
elif [[ "$LATITUDE_VAL" == "strict" && ${#AMBIGUOUS[@]} -gt 0 ]]; then
  COMPLETE="false"
  VERDICT="block-for-decision"
  AMBIG_LIST=$(IFS=,; echo "${AMBIGUOUS[*]}")
  BLOCKED_REASON="latitude:strict-and-ambiguous: the gate could not verify ${AMBIG_LIST}, so completeness is unestablished; a strict sweep asks rather than assumes"
  REASONS+=("block_for_decision:$AMBIG_LIST")
fi

REASONS_JSON=$(printf '%s\n' "${REASONS[@]:-}" | python3 -c "import json,sys; r=[l.strip() for l in sys.stdin if l.strip()]; print(json.dumps(r))")
AMBIGUOUS_JSON=$(printf '%s\n' "${AMBIGUOUS[@]:-}" | python3 -c "import json,sys; r=[l.strip() for l in sys.stdin if l.strip()]; print(json.dumps(r))")

# Emit JSON. Build via env vars + Python decoding so shell quoting is safe.
COMPLETE_BOOL=$([[ "$COMPLETE" == "true" ]] && echo 1 || echo 0)
TS_NOW=$(ts)
export COMPLETE_BOOL DIFF_FILES STUB_HITS_JSON PY_CHECK TS_CHECK DESIGN_CHECK APPS_CHECK BACKEND_TESTS_CHECK REASONS_JSON TS_NOW BASELINE CHANGED_ALL CRITERIA_JSON
export VERDICT BLOCKED_REASON LATITUDE_VAL AMBIGUOUS_JSON
export ONE_DEF_CHECK ONE_DEF_COUNT OCCUPANCY_NOTICE DOC_STATUS_NOTICE
export DEAD_SURFACE_CHECK
export FRESH_CLONE_CHECK DEV_MODE_OFF_CHECK FRONTEND_STATE_CHECK RESPONSE_TYPES_CHECK RATCHETS_NOT_LOOSENED_CHECK MODULE_LAYOUT_CHECK NO_PROCESS_GLOBALS_CHECK RESPONSE_MODEL_CHECK NO_RAW_STORAGE_CHECK GENERATED_CLIENT_CHECK DUPLICATE_HELPERS_CHECK OPAQUE_FIELDS_CHECK ROUTES_ANSWER_CHECK NO_STATE_IN_SOURCE_CHECK NO_RAW_PATHS_CHECK NO_APP_NAMES_CHECK PROMPT_LAYERS_CHECK EXPLAINER_CHECK MEMORY_REACHABLE_CHECK
RESULT_JSON=$(python3 - <<'PYEOF'
import json, os
# The changed-file list, capped so a huge diff cannot bloat the receipt/Job
# evidence. project_ralph.sh reads this into the persisted receipt.
files = [l for l in os.environ.get("CHANGED_ALL", "").splitlines() if l.strip()][:200]
try:
    criteria = json.loads(os.environ.get("CRITERIA_JSON") or "[]")
    if not isinstance(criteria, list):
        criteria = []
except Exception:
    criteria = []
out = {
    "complete":  bool(int(os.environ["COMPLETE_BOOL"])),
    "diff_files": int(os.environ["DIFF_FILES"]),
    "files":     files,
    "stub_hits": json.loads(os.environ["STUB_HITS_JSON"]),
    "typecheck": {"py": os.environ["PY_CHECK"], "ts": os.environ["TS_CHECK"]},
    "design":    os.environ["DESIGN_CHECK"],
    "backend_tests": os.environ.get("BACKEND_TESTS_CHECK", "skipped"),
    "pluggable_apps": os.environ["APPS_CHECK"],
    "hardcoding": os.environ.get("HARDCODE_CHECK", "skip"),
    # The same probes over docs/ and *.md, which `--all` covers and nothing
    # used to pass. Separate verdict so "code clean, docs dirty" cannot read
    # as clean. See section 4.6b-ii.
    "hardcoding_docs": os.environ.get("HARDCODE_DOCS_CHECK", "skip"),
    # What a `git clone` of this repo actually hands a stranger. Three of five
    # assertions implemented; the script names the other two on every run.
    "fresh_clone": os.environ.get("FRESH_CLONE_CHECK", "skipped"),
    # dev_only catalog gate: development app absent from GET /apps when dev mode off.
    # Ticket 2d6e7597366e4541ab2e52e05aae43dd.
    "dev_mode_off": os.environ.get("DEV_MODE_OFF_CHECK", "skipped"),
    # T6 ratchet (coherence-overhaul-2026-08-16.md §7). `one_definition` gates
    # (see REASONS above); `occupancy_notice` and `doc_status_notice` are pure
    # visibility, never affecting `complete` — R2 is explicit that a
    # newly-introduced zero-occupancy field is a question, not a build failure.
    "one_definition": os.environ.get("ONE_DEF_CHECK", "skipped"),
    "occupancy_notice": os.environ.get("OCCUPANCY_NOTICE") or None,
    "doc_status_notice": os.environ.get("DOC_STATUS_NOTICE") or None,
    # Target-architecture item 7 ratchets (target-architecture-2026-08-17.md
    # §4). All three GATE, same as `one_definition` above: each is a pinned
    # baseline that fails on regression, not an absolute count, for the
    # reasons each script's own header explains.
    "ratchets_not_loosened": os.environ.get("RATCHETS_NOT_LOOSENED_CHECK", "skipped"),
    "module_layout": os.environ.get("MODULE_LAYOUT_CHECK", "skipped"),
    "no_process_globals": os.environ.get("NO_PROCESS_GLOBALS_CHECK", "skipped"),
    # Layer 0 ratchet (managed-file-references-and-path-scoping-2026-08-18.md §9):
    # no NEW mutable state file under a source dir. Pinned baseline, GATES.
    "no_state_in_source": os.environ.get("NO_STATE_IN_SOURCE_CHECK", "skipped"),
    "no_raw_paths": os.environ.get("NO_RAW_PATHS_CHECK", "skipped"),
    "no_app_names": os.environ.get("NO_APP_NAMES_CHECK", "skipped"),
    # Absolute, not ratcheted: L0/L1/L2 prompt text is at zero
    # installation-specific facts, so a new one is not incremental debt,
    # it is a fact about this checkout shipped to every installation.
    "prompt_layers": os.environ.get("PROMPT_LAYERS_CHECK", "skipped"),
    "explainer_text": os.environ.get("EXPLAINER_CHECK", "skipped"),
    "frontend_state": os.environ.get("FRONTEND_STATE_CHECK", "skipped"),
    # Ratcheted per item: surface nothing reaches. The rule it enforces is
    # "a change deletes the surface it replaces in the same commit that adds
    # the new one", which until this gate existed was enforced by prose alone.
    "dead_surface": os.environ.get("DEAD_SURFACE_CHECK", "skipped"),
    # Ratcheted: a response shape named from schema.gen.ts instead of written
    # by hand. Three counts, and the list one is the one that matters most --
    # a wrong shape on a list renders the empty state and nobody sees it.
    "response_types": os.environ.get("RESPONSE_TYPES_CHECK", "skipped"),
    "response_model": os.environ.get("RESPONSE_MODEL_CHECK", "skipped"),
    # Absolute, not ratcheted: the tree is at zero raw storage calls, so any
    # new one is a cross-account leak rather than incremental debt.
    "no_raw_storage": os.environ.get("NO_RAW_STORAGE_CHECK", "skipped"),
    "generated_client": os.environ.get("GENERATED_CLIENT_CHECK", "skipped"),
    "duplicate_helpers": os.environ.get("DUPLICATE_HELPERS_CHECK", "skipped"),
    # Reported, not newly checked: 4.5f has computed and GATED on this since it
    # was written (see `opaque_response_field` in REASONS above) but never
    # emitted it, so the one consumer that shows a reviewer's per-check
    # outcomes to a person could not show the one that blocked the run.
    "opaque_fields": os.environ.get("OPAQUE_FIELDS_CHECK", "skipped"),
    # "skipped" covers both "this diff did not touch backend/" and "there was no
    # backend to probe"; the latter also lands in `ambiguous`, which is what
    # distinguishes them for a strict sweep.
    "routes_answer": os.environ.get("ROUTES_ANSWER_CHECK", "skipped"),
    # scripts/verify_memory.py, the memory acceptance harness. GATES. "skipped"
    # means the diff did not touch the retrieval path; "unavailable" lands in
    # `ambiguous` instead, never in a pass.
    "memory_reachable": os.environ.get("MEMORY_REACHABLE_CHECK", "skipped"),
    # executed machine-checkable acceptance assertions (empty unless the caller
    # passed REVIEWER_CRITERIA_FILE); surfaced so the Work UI shows what was checked.
    "criteria":  criteria,
    "reasons":   json.loads(os.environ["REASONS_JSON"]),
    # The verdict the caller acts on. `block-for-decision` carries the ambiguity
    # in `blocked_reason`, verbatim, so the block is legible without re-deriving it.
    "verdict":   os.environ["VERDICT"],
    "latitude":  os.environ["LATITUDE_VAL"],
    "ambiguous": json.loads(os.environ["AMBIGUOUS_JSON"]),
    "blocked_reason": os.environ.get("BLOCKED_REASON") or None,
    "ts":        os.environ["TS_NOW"],
    "baseline":  os.environ["BASELINE"],
}
print(json.dumps(out))
PYEOF
)
printf '%s\n' "$RESULT_JSON"

# Also log a one-liner for humans.
echo "[$(ts)] complete=$COMPLETE verdict=$VERDICT latitude=$LATITUDE_VAL diff=$DIFF_FILES stubs=$STUB_COUNT py=$PY_CHECK ts=$TS_CHECK design=$DESIGN_CHECK backend_tests=$BACKEND_TESTS_CHECK apps=$APPS_CHECK one_def=$ONE_DEF_CHECK ratchets_not_loosened=$RATCHETS_NOT_LOOSENED_CHECK module_layout=$MODULE_LAYOUT_CHECK no_process_globals=$NO_PROCESS_GLOBALS_CHECK no_state_in_source=$NO_STATE_IN_SOURCE_CHECK no_raw_paths=$NO_RAW_PATHS_CHECK no_app_names=$NO_APP_NAMES_CHECK prompt_layers=$PROMPT_LAYERS_CHECK explainer_text=$EXPLAINER_CHECK frontend_state=$FRONTEND_STATE_CHECK dead_surface=$DEAD_SURFACE_CHECK response_types=$RESPONSE_TYPES_CHECK response_model=$RESPONSE_MODEL_CHECK no_raw_storage=$NO_RAW_STORAGE_CHECK generated_client=$GENERATED_CLIENT_CHECK opaque_fields=$OPAQUE_FIELDS_CHECK routes_answer=$ROUTES_ANSWER_CHECK memory_reachable=$MEMORY_REACHABLE_CHECK duplicate_helpers=$DUPLICATE_HELPERS_CHECK dev_mode_off=$DEV_MODE_OFF_CHECK ambiguous=${AMBIGUOUS[*]:-none} reasons=${REASONS[*]:-none}" >> "$LOG"
if [[ -n "$OCCUPANCY_NOTICE" ]]; then
  echo "[$(ts)] occupancy notice: $(printf '%s' "$OCCUPANCY_NOTICE" | tr '\n' ' ')" >> "$LOG"
fi
if [[ -n "$DOC_STATUS_NOTICE" ]]; then
  echo "[$(ts)] doc-status notice: $DOC_STATUS_NOTICE" >> "$LOG"
fi

# 5.9 Attach these per-check outcomes to the CHANGE they judged (step C3 of
#     docs/platform-redesign/changes-and-review-2026-08-22.md, §5.3: machine
#     gates first, human attention last). Until this block the results above
#     went to stdout and $LOG and nowhere a reviewer could see them, so the
#     review surface said — honestly — that no machine checks were recorded and
#     a person re-answered by hand what a checker had already answered.
#
#     Addressed by REVIEWER_CHANGE_ID when the caller knows the change, or by
#     REVIEWER_GATE_PROJECT + REVIEWER_GATE_BRANCH when it only knows the branch
#     (the sweep learns the branch first). Unset on a manual run, which then
#     behaves exactly as before.
#
#     BEST-EFFORT BY CONTRACT. The verdict above is already made; recording it
#     is bookkeeping. No database, no open change, an unreadable payload: each
#     is logged by services/change_gates.py and none of them touches this
#     script's output or its "exits 0 always" contract. The module pins the data
#     owner itself before the store, for the reason project_ralph.sh's
#     open_change_for_task learned the hard way.
if [[ -n "${REVIEWER_CHANGE_ID:-}" ]] || \
   [[ -n "${REVIEWER_GATE_PROJECT:-}" && -n "${REVIEWER_GATE_BRANCH:-}" ]]; then
  GATE_ARGS=()
  [[ -n "${REVIEWER_CHANGE_ID:-}" ]] && GATE_ARGS+=(--change-id "$REVIEWER_CHANGE_ID")
  [[ -n "${REVIEWER_GATE_PROJECT:-}" ]] && GATE_ARGS+=(--project "$REVIEWER_GATE_PROJECT")
  [[ -n "${REVIEWER_GATE_BRANCH:-}" ]] && GATE_ARGS+=(--branch "$REVIEWER_GATE_BRANCH")
  # The backend venv, where sqlalchemy lives, in preference to whatever python3
  # is on PATH — the store is unreachable from the bare interpreter, and the
  # module would log an import error forever instead of writing anything. Same
  # resolution the occupancy block above uses.
  GATE_PY=$WORKSHOP/backend/.venv/bin/python
  [[ -x "$GATE_PY" ]] || GATE_PY=python3
  printf '%s' "$RESULT_JSON" | (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" \
    "$GATE_PY" -m services.change_gates "${GATE_ARGS[@]}") >> "$LOG" 2>&1 || true
fi

# 6. Meter the reviewer's OWN auxiliary claude spend (research rec 5). When this
#    reviewer performs an LLM review, its `claude --output-format json` envelope is
#    handed here via REVIEWER_CLAUDE_OUT; when a bill scope is set (REVIEWER_BILL_SCOPE,
#    passed by ralph.sh / project_ralph.sh for an autonomous run) we sum that
#    envelope's tokens the SAME weighted way ralph.sh does and record them to the
#    usage ledger against the WORKER's scope, tagged role=reviewer. This closes the
#    auxiliary reviewer cost the research flags as the 15x multiplier, so it counts
#    against the same rolling-window budget as the worker instead of running free.
#    Inert when REVIEWER_BILL_SCOPE is unset (a manual reviewer run bills nothing) or
#    when there is no reviewer claude envelope (the deterministic checks above spend
#    no Claude tokens, so nothing is recorded). Never affects the JSON verdict above.
if [[ -n "${REVIEWER_BILL_SCOPE:-}" && -n "${REVIEWER_CLAUDE_OUT:-}" && -f "${REVIEWER_CLAUDE_OUT:-}" ]]; then
  REV_TOKENS=$(python3 - "$REVIEWER_CLAUDE_OUT" <<'PYEOF'
import json, sys
tok = 0
try:
    txt = open(sys.argv[1]).read()
    obj = None
    try:
        obj = json.loads(txt)
    except Exception:
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith('{') and '"usage"' in line:
                try:
                    obj = json.loads(line); break
                except Exception:
                    pass
        if obj is None:
            i = txt.find('{"type"')
            if i >= 0:
                try:
                    obj = json.loads(txt[i:])
                except Exception:
                    obj = None
    u = (obj or {}).get('usage') or {}
    # Same weighted ladder as ralph.sh: cache reads at 0.1 (the subscription rate
    # limits count a cache read far below a fresh token), everything else at parity.
    tok = (int(u.get('input_tokens', 0))
           + int(0.1 * int(u.get('cache_read_input_tokens', 0)))
           + int(u.get('cache_creation_input_tokens', 0))
           + int(u.get('output_tokens', 0)))
except Exception:
    tok = 0
print(tok)
PYEOF
)
  if [[ -n "$REV_TOKENS" && "$REV_TOKENS" -gt 0 ]]; then
    REV_ATTR=(--role reviewer)
    [[ -n "${REVIEWER_BILL_TASK_ID:-}" ]] && REV_ATTR+=(--task-id "$REVIEWER_BILL_TASK_ID")
    [[ -n "${REVIEWER_BILL_RUN:-}" ]] && REV_ATTR+=(--run "$REVIEWER_BILL_RUN")
    (cd "$WORKSHOP" && PYTHONPATH="$WORKSHOP/backend" python3 -m services.usage_ledger record \
       --project "$REVIEWER_BILL_SCOPE" --tokens "$REV_TOKENS" "${REV_ATTR[@]}" >/dev/null 2>&1) || true
    echo "[$(ts)] reviewer metered $REV_TOKENS tokens (scope=$REVIEWER_BILL_SCOPE role=reviewer task=${REVIEWER_BILL_TASK_ID:-none} run=${REVIEWER_BILL_RUN:-none})" >> "$LOG"
  fi
fi

# The reviewer's contract is "exits 0 always" (consumers parse the JSON verdict),
# so return 0 regardless of the optional metering block's trailing test status.
exit 0
