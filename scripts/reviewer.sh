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
#   - typescript tsc --noEmit passes on changed .ts/.tsx files
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

set -u

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export WORKSHOP
# ROOT is the tree to inspect (git repo + stub scan). Defaults to the workshop
# install, but a per-project Ralph run points it at projects/<slug> via
# REVIEWER_ROOT so the reviewer gates the PROJECT's own git repo. WORKSHOP stays
# the tooling home (venv mypy, tsc, check_design.sh).
ROOT=${REVIEWER_ROOT:-$WORKSHOP}
BASELINE=${1:-HEAD}
LOG=$WORKSHOP/.runtime/reviewer.log
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
  TSC=$WORKSHOP/frontend/node_modules/.bin/tsc
  if [[ -x "$TSC" ]]; then
    # Run tsc against the whole frontend project (incremental, fast). This catches
    # cross-file errors that per-file checks miss. If no changed file is in
    # frontend/, skip — the change was in a different ts project (apps/, mobile/).
    if echo "$TS_FILES" | grep -q '^frontend/'; then
      if (cd "$WORKSHOP/frontend" && timeout 90 "$TSC" -b --noEmit) >/dev/null 2>&1; then
        TS_CHECK="ok"
      else
        TS_CHECK="fail"
      fi
    fi
  elif echo "$TS_FILES" | grep -q '^frontend/'; then
    AMBIGUOUS+=("ts_typecheck_unavailable")
  fi
fi

# 4.5 Design-system lint on ADDED lines in changed frontend .tsx files.
#     Enforces frontend/CLAUDE.md: no new inline hex / emoji / not-allowed.
#     "added lines only" mirrors the stub check — pre-existing legacy styling in
#     unmigrated pages never blocks; you just can't introduce new violations.
DESIGN_CHECK="skipped"
if printf '%s\n' "$CHANGED_ALL" | grep -qE '^frontend/src/.*\.tsx$'; then
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

# 4.6b No hardcoded machine or operator identity. Unlike the apps check this has
# no relevance precondition: a path or a hostname can land in any file, and the
# whole point is that it looks fine from inside the install that has it.
HARDCODE_CHECK="skip"
if [[ -x "$WORKSHOP/scripts/check_no_hardcoding.sh" ]]; then
  if bash "$WORKSHOP/scripts/check_no_hardcoding.sh" >/dev/null 2>&1; then
    HARDCODE_CHECK="ok"
  else
    HARDCODE_CHECK="fail"
  fi
else
  AMBIGUOUS+=("hardcoding_check_unavailable")
fi
export HARDCODE_CHECK

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
export COMPLETE_BOOL DIFF_FILES STUB_HITS_JSON PY_CHECK TS_CHECK DESIGN_CHECK APPS_CHECK REASONS_JSON TS_NOW BASELINE CHANGED_ALL CRITERIA_JSON
export VERDICT BLOCKED_REASON LATITUDE_VAL AMBIGUOUS_JSON
python3 - <<'PYEOF'
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
    "pluggable_apps": os.environ["APPS_CHECK"],
    "hardcoding": os.environ.get("HARDCODE_CHECK", "skip"),
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

# Also log a one-liner for humans.
echo "[$(ts)] complete=$COMPLETE verdict=$VERDICT latitude=$LATITUDE_VAL diff=$DIFF_FILES stubs=$STUB_COUNT py=$PY_CHECK ts=$TS_CHECK design=$DESIGN_CHECK apps=$APPS_CHECK ambiguous=${AMBIGUOUS[*]:-none} reasons=${REASONS[*]:-none}" >> "$LOG"

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
