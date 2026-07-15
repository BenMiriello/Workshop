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
#     "reasons":    [string, ...]
#   }
#
# `complete: true` requires ALL of:
#   - non-empty diff (something changed), UNLESS REVIEWER_ALLOW_NO_DIFF=1, set by
#     project_ralph.sh for command/operator-acceptance tasks, whose real gate is
#     the acceptance command / operator review, not a tracked-file diff (F18)
#   - zero NEW stub markers introduced (preexisting stubs in unchanged code are OK)
#   - python typecheck passes on changed .py files (uses backend venv mypy if present)
#   - typescript tsc --noEmit passes on changed .ts/.tsx files
#
# Conservative: if a check can't run (no tooling), it's "skipped" — counted as pass.
# Strict-mode (REVIEWER_STRICT=1) flips skipped to fail.
#
# Usage:
#   reviewer.sh [baseline-rev]        # default baseline = HEAD
#
# Exits 0 always (output is JSON; consumers parse `complete` field).

set -u

WORKSHOP=/home/clawd-a/workshop
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
  fi
fi

# 4.6 Pluggable-app invariant — apps must not be welded back into platform-core
#     files, and per-instance data must stay untracked (docs/apps-are-pluggable.md).
#     Repo-wide invariant, so it always runs (not gated on changed paths).
APPS_CHECK="skipped"
if [[ -x "$WORKSHOP/scripts/check_pluggable_apps.sh" ]]; then
  if bash "$WORKSHOP/scripts/check_pluggable_apps.sh" >/dev/null 2>&1; then
    APPS_CHECK="ok"
  else
    APPS_CHECK="fail"
  fi
fi

# 5. Decide.
REASONS=()
COMPLETE="true"

if [[ "$DESIGN_CHECK" == "fail" ]]; then
  REASONS+=("design_violations")
  COMPLETE="false"
fi
if [[ "$APPS_CHECK" == "fail" ]]; then
  REASONS+=("pluggable_app_violations")
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

REASONS_JSON=$(printf '%s\n' "${REASONS[@]:-}" | python3 -c "import json,sys; r=[l.strip() for l in sys.stdin if l.strip()]; print(json.dumps(r))")

# Emit JSON. Build via env vars + Python decoding so shell quoting is safe.
COMPLETE_BOOL=$([[ "$COMPLETE" == "true" ]] && echo 1 || echo 0)
TS_NOW=$(ts)
export COMPLETE_BOOL DIFF_FILES STUB_HITS_JSON PY_CHECK TS_CHECK DESIGN_CHECK APPS_CHECK REASONS_JSON TS_NOW BASELINE
python3 - <<'PYEOF'
import json, os
out = {
    "complete":  bool(int(os.environ["COMPLETE_BOOL"])),
    "diff_files": int(os.environ["DIFF_FILES"]),
    "stub_hits": json.loads(os.environ["STUB_HITS_JSON"]),
    "typecheck": {"py": os.environ["PY_CHECK"], "ts": os.environ["TS_CHECK"]},
    "design":    os.environ["DESIGN_CHECK"],
    "pluggable_apps": os.environ["APPS_CHECK"],
    "reasons":   json.loads(os.environ["REASONS_JSON"]),
    "ts":        os.environ["TS_NOW"],
    "baseline":  os.environ["BASELINE"],
}
print(json.dumps(out))
PYEOF

# Also log a one-liner for humans.
echo "[$(ts)] complete=$COMPLETE diff=$DIFF_FILES stubs=$STUB_COUNT py=$PY_CHECK ts=$TS_CHECK design=$DESIGN_CHECK apps=$APPS_CHECK reasons=${REASONS[*]:-none}" >> "$LOG"
