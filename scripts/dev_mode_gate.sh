#!/bin/bash
# dev_mode_gate.sh — the shell half of developer mode. ADR 0014.
#
# Developer mode is ONE question: is the platform repo permitted as a run
# target? The gate is the resolved working directory, not the scope name,
# because the platform repo is reachable under at least five names through four
# subsystems (see docs/platform-redesign/dev-mode-and-development-scope-2026-08-09.md
# §2). Gating a name closes one door and leaves eight open.
#
# This is a PRODUCT CAPABILITY BOUNDARY AND A SAFETY INTERLOCK, not a security
# boundary. Anyone with a shell on this box can edit the source directly; that
# is [SEC-A1] (execution identity) and still open. What this buys is that the
# always-on loops cannot silently rewrite the platform, and that "edit the
# Workshop" is a named visible mode instead of an emergent property of a default
# value. Do not describe it as confinement.
#
# The two behaviours, both of which used to be the opposite:
#
#   1. A run target must be NAMED. `DIR="$WORKSHOP"` as a default meant a bare
#      `ralph.sh "some task"` drove the platform repo. That default is the one
#      §3 of the design calls most easily forgotten and most consequential.
#   2. The platform repo is refused as a target unless WORKSHOP_DEV_MODE=1.
#
# Source it, then call assert_run_target before doing any work in a tree:
#
#   source "$(dirname "${BASH_SOURCE[0]}")/dev_mode_gate.sh"
#   assert_run_target "$DIR" "queue_runner" || exit 2
#
# The backend half is WORKSHOP_DEV_MODE read in backend/services/app_runner.py
# (is_self_dir); the frontend half is the build-time VITE_DEV_MODE. All three
# read one flag in one place per side, deliberately.

# The platform repo: this script's parent directory. Derived, not hardcoded, so
# an alternate checkout gates its own root rather than someone else's.
_DEV_GATE_SELF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

# Is developer mode on? Only the exact string "1" counts, so a stray empty or
# "0" or "false" in the environment can never read as on.
dev_mode_on() {
  [[ "${WORKSHOP_DEV_MODE:-}" == "1" ]]
}

# Resolve a path for comparison without requiring it to exist yet.
_dev_gate_resolve() {
  local p="$1"
  if [[ -d "$p" ]]; then
    (cd "$p" && pwd -P)
  else
    printf '%s' "$p"
  fi
}

# Does this path resolve to the platform repo itself?
is_self_dir() {
  local p
  p="$(_dev_gate_resolve "$1")"
  [[ "$p" == "$_DEV_GATE_SELF_ROOT" ]]
}

# The gate. Returns 0 if `dir` is an acceptable run target, 1 otherwise, with a
# specific reason on stderr. `who` names the caller so the message says which
# loop refused.
assert_run_target() {
  local dir="${1:-}" who="${2:-run}"

  if [[ -z "$dir" ]]; then
    cat >&2 <<EOF
[$who] refusing to start: no working tree named.
  The platform repo is no longer the default target (ADR 0014). Pass --dir PATH.
  To work on the Workshop itself: WORKSHOP_DEV_MODE=1 $who --dir $_DEV_GATE_SELF_ROOT
EOF
    return 1
  fi

  if is_self_dir "$dir" && ! dev_mode_on; then
    cat >&2 <<EOF
[$who] refusing to start: $dir is the Workshop's own source tree.
  Developer mode is off, so the platform repo is not a permitted run target.
  Set WORKSHOP_DEV_MODE=1 to allow it. This is a safety interlock, not a
  security boundary. See ADR 0014.
EOF
    return 1
  fi

  return 0
}
