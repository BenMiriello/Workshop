#!/usr/bin/env bash
# liveness_watchdog.sh: self-healing liveness monitor (coherent-rebuild 2d).
#
# Mirrors frontends_watchdog.sh, but for the autonomy run-state instead of the
# dev servers. Two jobs, both pure reconciliation (it never LAUNCHES work, so it
# is safe to run even while the autonomy brake is engaged):
#
#   1. Jobs stuck `running` whose launching process is dead get closed `failed`
#      (jobs.reconcile_stuck). A crashed/killed sweep otherwise lingers forever as
#      a phantom "still running" record, blocking its scope from redispatch.
#   2. Tasks stranded in `doing` past their lease whose claiming pid is dead get
#      released back to `todo` (or `parked` at the attempt cap) via tasks.reclaim,
#      per scope tree. This is the store-side half of the same recovery.
#
# It also probes the backend /health and logs a warning if it is down, so a dead
# backend is visible in one place (it does NOT restart the backend: that carries
# env-sensitive secrets and is restarted by hand, same rule as the frontends).
#
# Idempotent and cheap: reclaim scans lock-free and only writes when there is
# something to reclaim; reconcile_stuck only appends a terminal line for a Job it
# actually fails.
#
# Suggested crontab (every 5 minutes), NOT installed by this script:
#   */5 * * * * <workshop>/scripts/liveness_watchdog.sh >/dev/null 2>&1
set -u

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export WORKSHOP
RUNTIME="$WORKSHOP/.runtime"
LOG="$RUNTIME/liveness_watchdog.log"
HEALTH_URL="${WORKSHOP_HEALTH_URL:-http://127.0.0.1:8000/health}"
mkdir -p "$RUNTIME"

log() { echo "[$(date -Is)] $*" >> "$LOG"; }

# Backend health probe (advisory only; never restarts the backend).
if command -v curl >/dev/null 2>&1; then
  if ! curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
    log "WARN backend health probe failed ($HEALTH_URL)"
  fi
fi

# Reconcile stuck Jobs + reclaim stranded tasks across every scope tree. The
# scope roots are the workshop root itself plus every registered project dir and
# build-agent working tree, so a strand in any scope is recovered.
OUT=$(cd "$WORKSHOP" && python3 - <<'PYEOF' 2>&1
import json, os, sys
from pathlib import Path

sys.path.insert(0, os.environ["WORKSHOP"] + "/backend")
from services import jobs, tasks  # noqa: E402

WORKSHOP = Path(os.environ["WORKSHOP"])

# 1) Fail Jobs stuck `running` whose process is dead.
try:
    dead_jobs = jobs.reconcile_stuck()
except Exception as e:  # never let one bad record stop the sweep
    dead_jobs = []
    print(f"reconcile_stuck error: {e}", file=sys.stderr)

# 2) Reclaim stranded `doing` tasks across all scope roots.
roots: list[Path] = [WORKSHOP]
reg = Path(os.environ.get("WORKSHOP_PROJECTS_REGISTRY",
                          str(WORKSHOP / ".runtime" / "projects.json")))
try:
    projects = (json.loads(reg.read_text()).get("projects") or {})
    for p in projects.values():
        d = p.get("dir")
        if d:
            roots.append(Path(d))
except Exception:
    pass
try:
    from services import build_agents
    for rec in build_agents.list_agents():
        cwd = rec.get("cwd")
        if cwd:
            roots.append(Path(cwd))
except Exception:
    pass

reclaimed: list[dict] = []
seen: set[str] = set()
for root in roots:
    key = str(root)
    if key in seen or not root.is_dir():
        continue
    seen.add(key)
    # "Is there a store", not "is there a file": a scope whose tickets live in
    # Postgres has no `.tasks.json` to find, and testing for the file skipped it
    # silently, which is a watchdog that stops watching.
    if not tasks.has_store(root):
        continue
    try:
        for r in tasks.reclaim(root):
            reclaimed.append({"root": key, **r})
    except Exception as e:
        print(f"reclaim error {key}: {e}", file=sys.stderr)

print(json.dumps({
    "failed_jobs": [j.get("id") for j in dead_jobs],
    "reclaimed": reclaimed,
}))
PYEOF
)

# Last non-error line is the JSON summary; stderr lines (if any) precede it.
SUMMARY=$(echo "$OUT" | tail -n 1)
NJOBS=$(echo "$SUMMARY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('failed_jobs',[])))" 2>/dev/null || echo "?")
NTASKS=$(echo "$SUMMARY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('reclaimed',[])))" 2>/dev/null || echo "?")

if [[ "$NJOBS" != "0" || "$NTASKS" != "0" ]]; then
  log "reconciled: failed_jobs=$NJOBS reclaimed_tasks=$NTASKS :: $SUMMARY"
fi
exit 0
