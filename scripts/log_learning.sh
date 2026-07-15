#!/bin/bash
# log_learning.sh - record a one-line lesson into the structured learnings store.
# Called by work_verifier.sh + work-reviewer + cron failure paths.
#
# Usage:
#   log_learning.sh "pattern text" [source] [scope]
# Source is a short label (verifier|reviewer|cron|discord). Scope defaults to
# `global` (the workspace guardrails store). This delegates to
# services.learnings (structured, deduped JSONL) which ALSO mirrors global
# entries into shared/learnings.md so the SessionStart hook keeps seeing them.

set -u
PATTERN=${1:?usage: log_learning.sh <pattern> [source] [scope]}
SOURCE=${2:-anon}
SCOPE=${3:-global}

ROOT=/home/clawd-a/workshop
PY=$ROOT/backend/.venv/bin/python
[[ -x "$PY" ]] || PY=python3

PYTHONPATH=$ROOT/backend "$PY" -m services.learnings record "$PATTERN" \
  --source "$SOURCE" --scope "$SCOPE" >/dev/null
