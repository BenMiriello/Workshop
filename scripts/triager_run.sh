#!/usr/bin/env bash
# triager_run.sh: the triager entrypoint the dispatcher calls (final-plan P4).
#
# Runs one note-triage pass (services.triager.run_triage) for a scope, off the hot
# path. This is the role runner for `role: triager` agents: the dispatcher invokes
# it instead of project_ralph.sh, exactly as it invokes memory_keeper_run.sh for a
# keeper.
#
# Contract:
#   bash scripts/triager_run.sh <scope-key> [--agent <slug>] [--job <id>]
#     -> one pass over <scope-key>'s notes; exit 0 on success, nonzero on a hard
#        failure. Runs FOR REAL (files tasks) by default, so the dispatcher needs
#        no extra flag.
#   bash scripts/triager_run.sh <scope-key> --dry-run
#     -> PREVIEW only: decides, writes nothing (no tasks, no sidecar, no watermark
#        advance). --apply forces the for-real default explicitly.
#
#   bash scripts/triager_run.sh <scope-key> --queue --dry-run
#     -> the QUEUE pass instead of the note pass (autonomy-implementation
#        2026-08-13 section 5.6): reads the scope's `todo` TICKETS, asks whether
#        each is the right size and shape, prints every decision, writes nothing.
#
# THE QUEUE PASS IS MANUAL AND PREVIEWS BY DEFAULT. --queue without --apply is a
# dry run whatever the note pass's default is, and there is deliberately no cron
# entry and no dispatcher hook for it: the plan is to read a week of these before
# it is ever given a trigger. --queue --apply writes into the operator's queue
# (questions and child drafts), so it is spelled out every time.
#
# --agent names the triager whose definition body carries the textual routing
# rules; without it the pass runs on the model's judgement alone.
#
# A scope key is one of: workshop | project:<slug> | agent:<slug> | app:<name>.
#
# What this NEVER does: write a note. The runner has no such path (see
# services/triager.py) and the model call runs with tools disabled. That is what
# keeps a triager from changing its own trigger input and re-firing itself.

set -euo pipefail

DRY=0
MODE_SET=0
QUEUE=0
SCOPE=""
AGENT=""
JOB=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1; MODE_SET=1; shift ;;
    --apply)   DRY=0; MODE_SET=1; shift ;;
    --queue)   QUEUE=1; shift ;;
    --agent)   AGENT="${2:-}"; shift 2 ;;
    --job)     JOB="${2:-}"; shift 2 ;;
    *)         SCOPE="$1"; shift ;;
  esac
done
[[ -n "$SCOPE" ]] || { echo "usage: triager_run.sh <scope-key> [--queue] [--agent slug] [--job id] [--dry-run|--apply]" >&2; exit 2; }

# The note pass runs for real by default (the dispatcher calls it with no flag).
# The queue pass does not: writing questions and child drafts into the operator's
# queue is never something to do by omission.
if [[ "$QUEUE" == "1" && "$MODE_SET" == "0" ]]; then
  DRY=1
fi

RUN_FLAG="--apply"; [[ "$DRY" == "1" ]] && RUN_FLAG="--dry-run"

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
AUTH_ENV="${WORKSHOP_AUTH_ENV:-$HOME/.config/workshop/auth.env}"
BACKEND="$WORKSHOP/backend"
VENV_PY="$BACKEND/.venv/bin/python"
PY="${VENV_PY:-python3}"
[[ -x "$VENV_PY" ]] || PY=python3

# Subscription auth only; never fall back to an API key (user-scope rule).
if [[ -r "$AUTH_ENV" ]]; then
  set -a; . "$AUTH_ENV"; set +a
fi
unset ANTHROPIC_API_KEY

cd "$WORKSHOP"
export PYTHONPATH="$BACKEND"

ARGS=("$SCOPE" "$RUN_FLAG")
if [[ "$QUEUE" == "1" ]]; then
  ARGS+=(--queue)
fi
if [[ "$QUEUE" != "1" && -n "$AGENT" ]]; then
  ARGS+=(--agent "$AGENT")
fi
if [[ "$QUEUE" != "1" && -n "$JOB" ]]; then
  ARGS+=(--job "$JOB")
fi

WHAT="notes"; [[ "$QUEUE" == "1" ]] && WHAT="queue"
echo "[triager] triaging $WHAT scope=$SCOPE agent=${AGENT:-none} ($RUN_FLAG)"
exec "$PY" -m services.triager "${ARGS[@]}"
