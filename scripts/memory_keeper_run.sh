#!/usr/bin/env bash
# memory_keeper_run.sh: the memory-keeper entrypoint the dispatcher calls.
#
# Runs one memory-consolidation pass (services.memory_keeper.run_consolidation)
# for a scope, off the hot path. This is the assistant-runtime "non-code loop"
# body for the memory-keeper agent (agents/memory-keeper.md): the dispatcher
# invokes it instead of project_ralph.sh for that agent.
#
# Contract (another agent depends on this EXACTLY):
#   bash scripts/memory_keeper_run.sh <scope-key>
#     -> one consolidation pass for <scope-key>; exit 0 on success, nonzero on a
#        hard failure. Runs FOR REAL (writes) by default, so the dispatcher gets
#        a real consolidation with no extra flag.
#   bash scripts/memory_keeper_run.sh          (no scope)
#     -> iterate every active scope (workshop root + each project + each agent
#        scope), one pass each; exit 0 unless a pass hard-fails.
#   bash scripts/memory_keeper_run.sh <scope-key> --dry-run
#     -> PREVIEW only: gather + route + counts, writes nothing. --apply forces the
#        for-real default explicitly. The flag order is free.
#
# A scope key is one of: workshop | project:<slug> | agent:<slug> | app:<name>.

set -euo pipefail

# Default is a for-real run (writes) so the dispatcher needs no flag; --dry-run
# previews. --apply is the explicit form of the default.
DRY=0
SCOPE=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --apply)   DRY=0 ;;
    *)         SCOPE="$arg" ;;
  esac
done
RUN_FLAG="--apply"; [[ "$DRY" == "1" ]] && RUN_FLAG="--dry-run"

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export WORKSHOP
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

run_one() {  # $1 = scope key
  echo "[memory_keeper] consolidating scope=$1 ($RUN_FLAG)"
  "$PY" -m services.memory_keeper "$1" "$RUN_FLAG"
}

if [[ -n "$SCOPE" ]]; then
  run_one "$SCOPE"
  exit 0
fi

# No scope given: enumerate the active scopes and consolidate each. The workshop
# root is always included; projects and agent scopes come from the registries.
mapfile -t SCOPES < <("$PY" - <<'PYEOF'
import json, os
from pathlib import Path

runtime = Path(os.environ.get("WORKSHOP_RUNTIME") or Path(os.environ["WORKSHOP"]) / ".runtime")
scopes = ["workshop"]

reg = Path(os.environ.get("WORKSHOP_PROJECTS_REGISTRY", str(runtime / "projects.json")))
try:
    data = json.loads(reg.read_text())
    for slug in (data.get("projects") or {}):
        scopes.append(f"project:{slug}")
except Exception:
    pass

agents_dir = runtime / "agents"
if agents_dir.is_dir():
    for child in sorted(agents_dir.iterdir()):
        if (child / "knowledge").is_dir():
            scopes.append(f"agent:{child.name}")

# Preserve order, drop dupes.
seen = set()
for s in scopes:
    if s not in seen:
        seen.add(s)
        print(s)
PYEOF
)

rc=0
for scope in "${SCOPES[@]}"; do
  [[ -z "$scope" ]] && continue
  if ! run_one "$scope"; then
    echo "[memory_keeper] pass failed for scope=$scope" >&2
    rc=1
  fi
done
exit "$rc"
