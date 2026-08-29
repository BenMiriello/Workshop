#!/bin/bash
# log_learning.sh - record a one-line lesson as a standing note.
# Called by work_verifier.sh + work-reviewer + cron failure paths.
#
# Usage:
#   log_learning.sh "pattern text" [source] [scope]
# Source is a short label (verifier|reviewer|cron|discord). Scope defaults to
# `workshop`, which is the widest read layer and therefore reaches every scope:
# that is what the old `global` store meant, expressed in the one store that
# survives the 2026-08-28 fold (ticket 81198145).
#
# It records a STANDING NOTE: a note with `reach: always`, whose body is
# injected verbatim into every prompt in scope. The write reconciles, so a
# restatement of an existing rule updates it rather than adding a second copy,
# and a bare status token is refused with a reason on stderr.

set -u
PATTERN=${1:?usage: log_learning.sh <pattern> [source] [scope]}
SOURCE=${2:-anon}
SCOPE=${3:-workshop}

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PY=$ROOT/backend/.venv/bin/python
[[ -x "$PY" ]] || PY=python3

PYTHONPATH=$ROOT/backend "$PY" -c '
import sys
from services import standing
r = standing.record(sys.argv[1], source=sys.argv[2], scope=sys.argv[3])
if r.get("resolution") == "refused":
    print("not recorded:", r.get("reason"), file=sys.stderr)
    raise SystemExit(1)
' "$PATTERN" "$SOURCE" "$SCOPE" >/dev/null
