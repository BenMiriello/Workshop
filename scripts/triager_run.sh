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
SCOPE=""
AGENT=""
JOB=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --apply)   DRY=0; shift ;;
    --agent)   AGENT="${2:-}"; shift 2 ;;
    --job)     JOB="${2:-}"; shift 2 ;;
    *)         SCOPE="$1"; shift ;;
  esac
done
[[ -n "$SCOPE" ]] || { echo "usage: triager_run.sh <scope-key> [--agent slug] [--job id] [--dry-run]" >&2; exit 2; }

RUN_FLAG="--apply"; [[ "$DRY" == "1" ]] && RUN_FLAG="--dry-run"

WORKSHOP=/home/clawd-a/workshop
AUTH_ENV=/home/clawd-a/.config/clawdbot/auth.env
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
[[ -n "$AGENT" ]] && ARGS+=(--agent "$AGENT")
[[ -n "$JOB" ]] && ARGS+=(--job "$JOB")

echo "[triager] triaging scope=$SCOPE agent=${AGENT:-none} ($RUN_FLAG)"
exec "$PY" -m services.triager "${ARGS[@]}"
