#!/bin/bash
# work_generator.sh — fuel source for the autonomous cron loop.
#
# The cron agent's loop is fuel-starved when TODO.md has zero `[ ]` items. This
# script proactively GENERATES `[ ]` items by:
#
#   1. Scanning `## Log` and recently-marked `[x]` lines for incompletion cues
#      ("still needs", "deferred", "needs operator", "TODO", "blocked", etc.)
#      and producing concrete follow-ups for each one not already tracked.
#   2. Grepping the backend + frontend + scripts trees for stub markers
#      (TODO, FIXME, HACK, XXX, placeholder, NotImplemented, raise NotImplementedError).
#   3. Diffing claimed-shipped artifacts against the filesystem — e.g. if a Log
#      entry says "wrote backend/services/foo.py" and the file is gone, that is
#      a regression follow-up.
#
# All new items are appended to TODO.md under a `## Autogen` section. They use
# a `[ ]` checkbox so the cron picks them up like any other task. Each line
# carries a stable `gen:<sha>` provenance suffix used for dedupe — re-runs
# produce no duplicates.

set -u

WORKSHOP=/home/clawd-a/workshop
TODO=$WORKSHOP/TODO.md
GEN_SECTION="## Autogen"
GEN_DB=$WORKSHOP/.runtime/autogen-seen.txt
LOG=$WORKSHOP/.runtime/work_generator.log

mkdir -p "$(dirname "$GEN_DB")" "$(dirname "$LOG")"
touch "$GEN_DB"

ts() { date -Iseconds; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

# Produce a stable provenance hash for an item — used for dedupe.
gen_sha() {
  printf '%s' "$1" | sha1sum | cut -c1-12
}

# Append a `[ ]` item to TODO.md under `## Autogen`, only if its sha has not
# been seen before. Returns 0 if appended, 1 if skipped.
append_item() {
  local body="$1"
  local provenance="$2"
  local sha
  sha=$(gen_sha "$body|$provenance")
  if grep -qF "gen:$sha" "$GEN_DB"; then
    return 1
  fi
  # Ensure the `## Autogen` section exists.
  if ! grep -qE "^${GEN_SECTION}$" "$TODO"; then
    printf '\n%s\n<!-- Items produced by work_generator.sh. Dedupe key is the trailing `gen:<sha>` suffix. -->\n' "$GEN_SECTION" >> "$TODO"
  fi
  # Append the item.
  printf -- '[ ] %s — provenance: %s — gen:%s\n' "$body" "$provenance" "$sha" >> "$TODO"
  printf '%s\n' "$sha" >> "$GEN_DB"
  log "appended gen:$sha — $body"
  return 0
}

added=0

# =============================================================================
# Pass 1: caveat scan of TODO.md
# =============================================================================
# Look for `[x]` lines (or recent log entries) containing incompletion cues
# and produce a follow-up to verify / finish each.
#
# Cue patterns: case-insensitive, anchored to phrases that consistently signal
# work was claimed-done but actually deferred. We avoid `blocked` alone since
# `[~] BLOCKED` is the official deferral marker — only flag deferrals hidden
# inside `[x]` lines.
# -----------------------------------------------------------------------------
log "pass 1: caveat scan of TODO.md"
while IFS= read -r line; do
  # only consider [x] items (skip [~] which is already-flagged blocked work)
  if [[ "$line" != "[x] "* ]]; then continue; fi
  # match cue patterns
  if ! echo "$line" | grep -qiE '\b(still needs|needs operator|deferred|needs backend restart|needs operator-side|TODO:|pending until|gated on|next pass)\b'; then
    continue
  fi
  body="verify / finish caveat on prior [x]: $(echo "$line" | head -c 240)"
  if append_item "$body" "todo-caveat"; then
    added=$((added+1))
  fi
done < "$TODO"

# =============================================================================
# Pass 2: stub-marker grep of backend + frontend + scripts
# =============================================================================
# rg-style fast grep for stub markers in code we own. Excludes vendor dirs,
# .git, node_modules, .venv, __pycache__, dist, build.
#
# Each hit produces one follow-up per (file, marker-kind). We collapse multiple
# hits in the same file with the same marker into one item to keep the queue
# sane.
# -----------------------------------------------------------------------------
log "pass 2: stub-marker grep"

SCAN_ROOTS=(
  "$WORKSHOP/backend"
  "$WORKSHOP/frontend/src"
  "$WORKSHOP/scripts"
  "$WORKSHOP/apps"
  "$WORKSHOP/mobile"
)
EXCLUDES=(
  --exclude-dir=node_modules
  --exclude-dir=.venv
  --exclude-dir=__pycache__
  --exclude-dir=dist
  --exclude-dir=build
  --exclude-dir=.git
  --exclude-dir=coverage
  --exclude-dir=.next
)

# Tokens we hunt. Each token's per-file occurrence rolls up into one item.
# Canonical-form only (TODO:, FIXME:, HACK:, XXX:) so we skip docstring
# references and identifier substrings (TODO_PATH, "TODO.md", etc.).
TOKENS=(
  'TODO:'
  'FIXME:'
  'XXX:'
  'HACK:'
  'NotImplementedError'
  'raise NotImplemented'
  'pass  # stub'
  'pass # stub'
  'STUB:'
)

for root in "${SCAN_ROOTS[@]}"; do
  if [[ ! -d "$root" ]]; then continue; fi
  for token in "${TOKENS[@]}"; do
    # grep -RIn = recursive, binary-skip, line-number; -F = fixed string for safety
    # We use grep with --include extensions to keep noise low.
    matches=$(grep -RIn -F "$token" "$root" \
      "${EXCLUDES[@]}" \
      --include='*.py' --include='*.ts' --include='*.tsx' \
      --include='*.js' --include='*.jsx' \
      2>/dev/null || true)
    if [[ -z "$matches" ]]; then continue; fi
    # Group by file → one item per (file, token).
    echo "$matches" | awk -F: '{print $1}' | sort -u | while IFS= read -r file; do
      rel=${file#$WORKSHOP/}
      count=$(echo "$matches" | grep -c "^${file}:")
      body="resolve $count occurrence(s) of '$token' in $rel (no placeholder/stub work)"
      append_item "$body" "stub-scan:$token:$rel" && added=$((added+1)) || true
    done
  done
done

# =============================================================================
# Pass 3: file-existence audit of `## Log` claims
# =============================================================================
# Recent log entries name file paths and endpoints. If the cited file is gone
# (or never existed), the claim is regressed — emit a follow-up.
# -----------------------------------------------------------------------------
log "pass 3: file-existence audit of ## Log"

# Take last 50 log entries (most recent work) and pull out paths starting with
# `backend/`, `frontend/`, `scripts/`, `apps/`, `mobile/`, `infra/`.
LOG_TAIL=$(awk '/^## Log/{found=1; next} found' "$TODO" | head -200)
echo "$LOG_TAIL" | grep -oE '(backend|frontend|scripts|apps|mobile|infra)(/[A-Za-z0-9_./-]+)+' \
  | sort -u \
  | while IFS= read -r relpath; do
    # Strip trailing punctuation that leaks in from prose (".", ",", ")", etc.).
    relpath="${relpath%[.,;:)]}"
    # Iteratively strip — handles "...py)." → "...py".
    while [[ "$relpath" == *[.,\;\:\)] ]]; do relpath="${relpath%?}"; done
    abs="$WORKSHOP/$relpath"
    # Skip absurdly long paths and obvious code-fences leakage.
    if [[ ${#relpath} -gt 200 ]]; then continue; fi
    # Only audit paths that look like files (have an extension after stripping).
    if [[ "$relpath" != *.* ]]; then continue; fi
    if [[ ! -e "$abs" ]]; then
      body="cited-but-missing artifact: $relpath (claimed in ## Log but file does not exist on disk)"
      append_item "$body" "log-audit:missing:$relpath" && added=$((added+1)) || true
    elif [[ -f "$abs" && ! -s "$abs" ]]; then
      body="cited-but-empty artifact: $relpath (claimed in ## Log but file is zero bytes)"
      append_item "$body" "log-audit:empty:$relpath" && added=$((added+1)) || true
    fi
  done

# =============================================================================
# Pass 4: Inbox graduation
# =============================================================================
# Items the operator drops in via `!todo` land in `## Inbox` as `[ ] <text>`.
# Those are ALREADY in `[ ]` form, so the cron will pick them — nothing to do
# here. But if any Inbox items are still untouched after ≥ INBOX_GRAD_HOURS
# from now (file mtime as a coarse proxy), promote them out of Inbox into the
# main flow so they're not stuck at the bottom of the file. (Skipped for now —
# trivial follow-up, leave Inbox alone.)
# -----------------------------------------------------------------------------

log "summary: added=$added open=$(grep -c '^\[ \]' "$TODO" 2>/dev/null || echo 0)"
echo "$added"
