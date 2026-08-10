#!/usr/bin/env bash
# One answer to "may background work run right now", for the shell side.
#
# There were two off-switches with no relationship. The JSON overlay at
# .runtime/autonomy-override.json is what the API and the trigger dispatcher
# read; the AUTONOMY_DISABLED flag file is what actually gated the shell worker
# loop, and cron_agent.sh, queue_runner.sh and project_ralph.sh each tested the
# flag while none of them had ever read the JSON. So "is autonomy on" had two
# answers in two formats and asking the wrong one gave the wrong answer — the
# API could report clear while every tick skipped, which is exactly how a toggle
# comes to do nothing.
#
# The read was unified in services/autonomy_override (it reports the UNION of
# both switches). This is the shell's door to that same function, so there is
# ONE implementation of the semantics rather than a bash reimplementation that
# will drift from it.
#
# FAILS CLOSED, always. If python is missing, the venv is broken, the import
# fails, or anything else goes wrong, this reports "engaged" and the caller
# skips. A kill switch that fails open is not a kill switch. That is also why
# this returns a STRING rather than an exit code: an exit code from a crashed
# interpreter is indistinguishable from a legitimate "clear".
#
# Usage:
#   source "$WORKSHOP/scripts/autonomy_gate.sh"
#   if autonomy_engaged; then echo "skipping"; exit 0; fi

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Prints "engaged" or "clear". Never fails, never prints anything else.
autonomy_state() {
  local py="$WORKSHOP/backend/.venv/bin/python"
  [ -x "$py" ] || { echo "engaged"; return; }
  local out
  out="$("$py" -c '
import sys
sys.path.insert(0, "'"$WORKSHOP"'/backend")
try:
    from services import autonomy_override as o
    print("engaged" if o.is_engaged() else "clear")
except Exception:
    print("engaged")
' 2>/dev/null)"
  case "$out" in
    clear) echo "clear" ;;
    *)     echo "engaged" ;;   # includes empty output from a crash
  esac
}

# True (exit 0) when background work must NOT run.
autonomy_engaged() {
  [ "$(autonomy_state)" = "engaged" ]
}

# The human-readable reason, for a log line. Best-effort; empty on any failure.
autonomy_reason() {
  local py="$WORKSHOP/backend/.venv/bin/python"
  [ -x "$py" ] || { echo "cannot read the override (no interpreter)"; return; }
  "$py" -c '
import sys
sys.path.insert(0, "'"$WORKSHOP"'/backend")
try:
    from services import autonomy_override as o
    print(o.read().get("reason") or "engaged")
except Exception:
    print("cannot read the override")
' 2>/dev/null || echo "cannot read the override"
}
