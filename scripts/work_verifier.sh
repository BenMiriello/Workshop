#!/bin/bash
# work_verifier.sh — audits `[x]` claims and demotes broken ones to `[ ]`.
#
# The cron loop's autonomy is only as honest as the [x] checks the agent
# writes. Past behavior: claude -p marks tasks done, leaving caveats like
# "needs backend restart" or "stubbed, needs real impl" — those should never
# have been `[x]`. This script enforces the contract retroactively.
#
# Strategy:
#   1. Walk every `[x]` line in TODO.md (skip [~]/[ ]).
#   2. Extract referenced artifacts: file paths (backend/.../*.py etc.) and
#      HTTP endpoints (/foo/bar) from the line body.
#   3. For each file path: assert exists + size > some-floor.
#   4. For each endpoint: curl http://127.0.0.1:8000<endpoint>; pass if 2xx,
#      3xx, or 4xx (404 itself is a real signal vs. dead). 5xx or connection
#      refused fails the claim.
#   5. On verify failure, REPLACE the leading `[x]` with `[ ]` and append
#      `(verify-failed YYYY-MM-DD: <reasons>)` to the line so the cron picks
#      it back up and the failure reason is visible.
#
# Conservative: only modifies lines where every cited artifact fails. A single
# valid citation is enough to keep the [x]. Idempotent: re-running over a
# line that already has `(verify-failed ...)` does not re-stamp it.

set -u

WORKSHOP=/home/clawd-a/workshop
TODO=$WORKSHOP/TODO.md
LOG=$WORKSHOP/.runtime/work_verifier.log

mkdir -p "$(dirname "$LOG")"

ts() { date -Iseconds; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

# Floor for "non-trivial" file: 32 bytes. Anything smaller is almost
# certainly an empty stub.
FILE_SIZE_FLOOR=32

# Tunable: only audit lines added in the last AUDIT_RECENT_LINES of the
# TODO file. We don't want to re-audit 6 months of prior `[x]`. The TODO is
# 183 lines as of writing, so all of it. Bump if it grows.
AUDIT_RECENT_LINES=400

# Read the tail of the TODO, find [x] lines, audit them.
TODO_TAIL=$(tail -n "$AUDIT_RECENT_LINES" "$TODO")

# Use a temp file so we can stream-edit safely.
TMP=$TODO.verifier.$$
cp "$TODO" "$TMP"

demoted=0
audited=0

# We loop by line number (against TODO, not TMP — TMP is what we mutate).
LINENO=0
while IFS= read -r line; do
  LINENO=$((LINENO+1))
  # only [x] lines, and only if not already audit-failed
  if [[ "$line" != "[x] "* ]]; then continue; fi
  if echo "$line" | grep -q '(verify-failed '; then continue; fi
  audited=$((audited+1))

  # Extract paths and endpoints.
  paths=$(echo "$line" | grep -oE '(backend|frontend|scripts|apps|mobile|infra)(/[A-Za-z0-9_./-]+)+' | sort -u || true)
  endpoints=$(echo "$line" | grep -oE '(^|[^A-Za-z0-9])/(commute|relay|events|finance|agents|services|plaid|backlog|healthz|auto-pr)[A-Za-z0-9/_-]*' | tr -d ' ' | sed 's/^[^/]*//' | sort -u || true)

  cited=0
  passed=0
  reasons=()

  while IFS= read -r p; do
    if [[ -z "$p" ]]; then continue; fi
    cited=$((cited+1))
    abs="$WORKSHOP/$p"
    if [[ ! -e "$abs" ]]; then
      reasons+=("missing:$p")
    elif [[ -f "$abs" ]]; then
      sz=$(stat -c %s "$abs" 2>/dev/null || echo 0)
      if (( sz < FILE_SIZE_FLOOR )); then
        reasons+=("empty:$p($sz B)")
      else
        passed=$((passed+1))
      fi
    else
      passed=$((passed+1))   # directories etc.
    fi
  done <<< "$paths"

  while IFS= read -r ep; do
    if [[ -z "$ep" ]]; then continue; fi
    cited=$((cited+1))
    # quick HEAD with 3s timeout. Treat connection refused / 5xx as fail; 2xx-4xx as pass.
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:8000${ep}" 2>/dev/null || echo "000")
    if [[ "$code" == "000" ]]; then
      reasons+=("connrefused:$ep")
    elif [[ "$code" =~ ^5 ]]; then
      reasons+=("5xx:$ep($code)")
    else
      passed=$((passed+1))
    fi
  done <<< "$endpoints"

  # No citations? Can't audit; leave alone.
  if (( cited == 0 )); then continue; fi
  # Any citation passed? Trust the claim.
  if (( passed > 0 )); then continue; fi

  # Every cited artifact failed — demote.
  today=$(date +%Y-%m-%d)
  reasons_str=$(printf '%s,' "${reasons[@]}" | sed 's/,$//')
  # Note: `${line#\[x\] }` literally strips a leading "[x] " — the brackets
  # are escaped because bash's `${...#pat}` uses glob syntax where `[x]` means
  # "any char in {x}", which would NOT match the literal `[`.
  new_line="[ ] ${line#\[x\] } (verify-failed $today: $reasons_str)"
  # Escape sed special chars in the search pattern (use awk for safety).
  awk -v ln="$LINENO" -v newl="$new_line" 'NR==ln{print newl; next}{print}' "$TMP" > "$TMP.next" && mv "$TMP.next" "$TMP"
  log "demoted line=$LINENO reasons=$reasons_str body=$(echo "$line" | head -c 100)"
  demoted=$((demoted+1))
done < "$TODO"

if (( demoted > 0 )); then
  # Atomically replace TODO with the verified copy.
  mv "$TMP" "$TODO"
  log "summary: audited=$audited demoted=$demoted"
else
  rm -f "$TMP"
  log "summary: audited=$audited demoted=0 (no changes)"
fi

echo "$demoted"
