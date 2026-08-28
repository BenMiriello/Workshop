#!/bin/bash
# tree_lease.sh — the WORKTREE lease, from a run's side.
#
# Sections 4 and 5 of
# docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md, for the
# participant `services/chat_worktree.py` does not cover: a NON-INTERACTIVE run.
#
# A run that is handed an isolated worktree must HOLD it, and until 2026-08-27
# none ever did. `worktrees.provision_worktree` creates the tree and records it;
# neither it nor `allocate_task_worktree` acquires anything. That is exactly why
# WORKSHOP_TREE_LEASE_MOUNT could not be armed: with the flag on,
# `tree_access.decide` gives a READ-ONLY mount to any holder without a lease, so
# every sweep would have been read-only inside the worktree it had just been
# given and every unattended run would have failed at its first write.
#
# THE HOLDER IS THE RUN, under the id the rest of the system already uses for it
# (the child Job id): `services/tasks.py` claims the TICKET lease under it,
# `ralph.sh` writes its presence row under it, and `services/run_control.py`
# names its control channel after it — so Queue, Steer and Preempt
# (`services/control.py`) find the same holder they were written to find rather
# than a second name for one run.
#
# THE RENEWAL IS A BACKGROUND CHILD, because nothing in a sweep ticks during the
# work: `ralph.sh` beats at the TOP of an iteration and then blocks in
# `timeout $PER_ITER_TIMEOUT claude -p`, bounded at 1500s against a 90-second
# LEASE_TTL_SEC. That is not a heartbeat gap, it is sixteen of them, and a lease
# that lapses under a live writer is worse than no lease: it makes the tree read
# as reclaimable while work is still going into it. The beater watches the
# caller's pid and exits when the caller is gone. IT NEVER RELEASES — a crashed
# run falls to the EXISTING expiry-and-fence path, not to a second reclaim
# mechanism racing the one that already works.
#
# Sourced rather than executed, so the caller gets the state back, and a
# separate file so both `project_ralph.sh` and `ralph.sh` use one copy and
# backend/tests/test_tree_lease_sh.py can source exactly this.
#
# FAIL SOFT, like sweep_control.sh beside it. An installation with no relational
# store (every file-backed project tree is one, by design) answers "no lease
# store", and the caller carries on exactly as it did before this table existed.

# WORKSHOP is set by every caller; derive it if this file was sourced bare.
: "${WORKSHOP:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RUN_LEASE_PY="${RUN_LEASE_PY:-$WORKSHOP/scripts/run_lease.py}"

TREE_SUBJECT=""      # the lease subject id of the tree we hold, while we hold it
TREE_GENERATION=""   # the generation we took it with, for the fenced release
TREE_BEAT_PID=""     # the background renewer

# Log through the caller's own `log` when it has one (so the line lands in the
# sweep's log beside everything else), else to stderr. Never a hard dependency:
# this file is sourced before $LOG exists in both callers.
_tree_lease_log() {
  if declare -F log >/dev/null 2>&1; then log "$*"; else echo "[tree-lease] $*" >&2; fi
}

# Take the lease on a tree. $1=holder id (the run/child-job id), $2=tree dir.
# Exit: 0 taken, 1 somebody else holds it, 3 no lease store / unnameable tree.
tree_lease_take() {
  tree_lease_release
  local out rc
  out=$(python3 "$RUN_LEASE_PY" enter --run-id "$1" --dir "$2" 2>>"${TREE_LEASE_LOG:-/dev/null}")
  rc=$?
  if (( rc != 0 )); then
    _tree_lease_log "tree lease NOT taken on $2 (exit $rc): $(printf '%s' "$out" | head -c 300)"
    return "$rc"
  fi
  TREE_SUBJECT=$(printf '%s' "$out" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("subject_id") or "")
except Exception: print("")' 2>/dev/null)
  TREE_GENERATION=$(printf '%s' "$out" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("generation") or "")
except Exception: print("")' 2>/dev/null)
  if [[ -z "$TREE_SUBJECT" || -z "$TREE_GENERATION" ]]; then
    TREE_SUBJECT=""; TREE_GENERATION=""
    _tree_lease_log "tree lease answer for $2 could not be read; continuing without one"
    return 3
  fi
  python3 "$RUN_LEASE_PY" beat \
      --subject-id "$TREE_SUBJECT" --generation "$TREE_GENERATION" \
      --parent-pid $$ ${TREE_LEASE_LOG:+--log "$TREE_LEASE_LOG"} \
      </dev/null >>"${TREE_LEASE_LOG:-/dev/null}" 2>&1 &
  TREE_BEAT_PID=$!
  _tree_lease_log "tree lease HELD on $2 (subject=$TREE_SUBJECT gen=$TREE_GENERATION holder=$1 beat=$TREE_BEAT_PID)"
  return 0
}

# Give the tree back. Idempotent and best-effort, which is what lets it sit both
# on an EXIT trap and on the one choke point every terminal leg goes through: a
# run that ends ANY way — done, failed, blocked, parked, displaced, thrashed,
# timed out, cancelled, or parked awaiting_input — stops holding the tree, and
# only a run that is KILLED falls to expiry.
tree_lease_release() {
  if [[ -n "${TREE_BEAT_PID:-}" ]]; then
    kill "$TREE_BEAT_PID" 2>/dev/null || true
    wait "$TREE_BEAT_PID" 2>/dev/null || true
    TREE_BEAT_PID=""
  fi
  if [[ -n "${TREE_SUBJECT:-}" && -n "${TREE_GENERATION:-}" ]]; then
    python3 "$RUN_LEASE_PY" release \
        --subject-id "$TREE_SUBJECT" --generation "$TREE_GENERATION" \
        >/dev/null 2>>"${TREE_LEASE_LOG:-/dev/null}" || true
    _tree_lease_log "tree lease released (subject=$TREE_SUBJECT gen=$TREE_GENERATION)"
  fi
  TREE_SUBJECT=""; TREE_GENERATION=""
}
