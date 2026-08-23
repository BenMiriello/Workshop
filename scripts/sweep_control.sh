#!/bin/bash
# sweep_control.sh — the control channel, from the sweep's side.
#
# Step B6 of docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md
# (section 8). A sweep is a bash loop with no stdin, so steer and yield reach it
# through `.runtime/control/<run-id>.json`, read at the TOP OF EACH ITERATION.
# Section 8 is explicit that this is not a downgrade: an iteration boundary is a
# better place to fold a request into than the middle of a turn, because the
# sweep is between tasks and has nothing half-applied.
#
# Sourced rather than executed, so the caller gets the two variables back:
#
#   control_read <run-id>     -> CONTROL_YIELD=0|1, CONTROL_STEER="<text>"
#   control_fold "<prompt>"   -> echoes the prompt with CONTROL_STEER folded in
#   control_yield_snapshot <run-id> <repo>
#                             -> snapshot the tree to refs/wip/<run-id>
#
# FAIL SOFT IS THE WHOLE CONTRACT. Every command here is guarded, every failure
# is swallowed, and a malformed or unreadable control file leaves CONTROL_YIELD
# at 0 and CONTROL_STEER empty. A bookkeeping file must never be able to stop a
# sweep, and the python side (services/run_control.py) fails soft for the same
# reason, so this is belt and braces on purpose.
#
# It is a separate file from project_ralph.sh so the behaviour can be TESTED --
# backend/tests/test_run_control.py sources exactly this -- and so ralph.sh and
# queue_runner.sh can pick it up without a second copy.

# WORKSHOP is set by every caller; derive it if this file was sourced bare.
: "${WORKSHOP:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CONTROL_PY="${CONTROL_PY:-$WORKSHOP/scripts/control.py}"

CONTROL_YIELD=0
CONTROL_STEER=""

# $1 = run id. Sets CONTROL_YIELD and CONTROL_STEER. Always returns 0.
control_read() {
  CONTROL_YIELD=0
  CONTROL_STEER=""
  local run_id="${1:-}"
  [[ -z "$run_id" ]] && return 0
  [[ -x "$CONTROL_PY" || -f "$CONTROL_PY" ]] || return 0
  # Yield first and NON-CONSUMING: it is a standing instruction to stop, not a
  # message, so it stays readable after the sweep has acted on it.
  if python3 "$CONTROL_PY" yielding "$run_id" >/dev/null 2>&1; then
    CONTROL_YIELD=1
  fi
  # `take` is consuming: what is folded into this iteration's prompt must not be
  # folded into every iteration after it.
  CONTROL_STEER=$(python3 "$CONTROL_PY" take "$run_id" --format text 2>/dev/null) || CONTROL_STEER=""
  return 0
}

# $1 = the prompt. Echoes it with the steer requests appended, or unchanged when
# there are none. The framing lives HERE and not in the python side, so there is
# one place that decides how a steered request is introduced to the model.
control_fold() {
  local prompt="${1:-}"
  if [[ -z "$CONTROL_STEER" ]]; then
    printf '%s' "$prompt"
    return 0
  fi
  printf '%s\n\n%s\n%s\n' "$prompt" \
    "--- sent by the operator while this run was working ---" \
    "$CONTROL_STEER"
}

# $1 = run id, $2 = repo. Section 9: yield means SNAPSHOT, release, exit cleanly.
# The snapshot goes to a dangling ref (refs/wip/<run-id>) and never to the
# branch, so the tip does not move and the next holder starts where it chose to.
# Best-effort: a repo that cannot be snapshotted must not stop the exit path.
control_yield_snapshot() {
  local run_id="${1:-}" repo="${2:-}"
  [[ -z "$run_id" || -z "$repo" ]] && return 0
  python3 "$CONTROL_PY" snapshot "$run_id" --repo "$repo" 2>/dev/null || true
  return 0
}
