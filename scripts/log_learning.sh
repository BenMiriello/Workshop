#!/bin/bash
# log_learning.sh — append a one-line pattern to workshop/shared/learnings.md.
# Called by work_verifier.sh + work-reviewer + cron failure paths.
#
# Usage:
#   log_learning.sh "pattern text" [source]
# Source is a short label (verifier|reviewer|cron|discord) for auditability.

set -u
PATTERN=${1:?usage: log_learning.sh <pattern> [source]}
SOURCE=${2:-anon}
LEARN=/home/clawd-a/workshop/shared/learnings.md
mkdir -p "$(dirname "$LEARN")"
[[ -e "$LEARN" ]] || touch "$LEARN"

# Dedupe by content hash — don't append the same pattern twice.
SHA=$(printf '%s' "$PATTERN" | sha1sum | cut -c1-12)
if grep -qF "$SHA" "$LEARN"; then
  exit 0
fi

# Append in the "Failure patterns" section, or create it if missing.
if ! grep -q '^## Failure patterns the worker has hit before' "$LEARN"; then
  printf '\n## Failure patterns the worker has hit before\n\n' >> "$LEARN"
fi
TS=$(date -u +%Y-%m-%dT%H:%MZ)
printf -- '- %s — %s [src:%s sha:%s]\n' "$PATTERN" "$TS" "$SOURCE" "$SHA" >> "$LEARN"
