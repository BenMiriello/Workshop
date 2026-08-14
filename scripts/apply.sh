#!/usr/bin/env bash
# apply.sh — make the running system match the source on disk.
#
#   bash scripts/apply.sh            apply whatever has drifted
#   bash scripts/apply.sh --check    report drift, change nothing
#
# THIS IS THE ONE COMMAND. Run it when you have finished a change, whatever you
# changed. It takes no arguments and asks you nothing, because the thing agents
# were getting wrong was never the typing -- it was the judgement calls in front
# of it: which of four surfaces did I touch, which of three commands applies,
# is it safe right now, is another agent mid-flight. Every one of those is a
# question this script can answer better than the agent can, from the running
# system rather than from memory of what it edited.
#
# HOW IT DECIDES. It does not read git and it does not ask what you changed. It
# compares WHAT IS LIVE against WHAT IS ON DISK, surface by surface, and acts
# only where they differ. The backend and the bundle both report their own
# source id (GET /healthz, frontend/dist/build-info.json), so those comparisons
# are exact. That makes the script idempotent: running it twice does nothing the
# second time, and running it when you changed nothing does nothing at all.
#
# IT IS ALWAYS SAFE TO RUN, from any agent, at any time, concurrently with any
# other agent. You do not need to coordinate and you do not need to ask. The
# build takes an flock; the backend restart waits for its own turn and briefly
# for anyone else's before it fires. Serialization is this script's problem, and
# an agent reasoning about other agents' in-flight work is inventing a problem
# the tooling already owns.
#
# WHAT IT COSTS (measured 2026-08-13, so nobody has to guess again):
#   bundle rebuild   ~17s, and NOTHING restarts -- the edge serves dist from
#                    disk per request, so no requests drop and no stream cuts
#   vite restart     ~2s, does not touch the operator's origin at all
#   backend restart  deferred: it fires after your reply lands. In-flight turns
#                    survive it (KillMode=process) and reattach.
#
# Detail and rationale: docs/applying-changes.md
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CHECK=0
for arg in "$@"; do
  case "$arg" in
    --check|-n) CHECK=1 ;;
    -h|--help) sed -n '2,34p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "apply.sh: unknown argument '$arg' (try --check or --help)" >&2; exit 64 ;;
  esac
done

DRIFT=0      # something is out of date
FAILED=0     # something we tried to do did not work
ACTED=0      # we changed something

say()  { printf '%s\n' "$*"; }
plan() { DRIFT=1; if [ "$CHECK" = 1 ]; then say "  STALE   $1"; else say "  apply   $1"; fi; }
ok()   { say "  ok      $1"; }
warn() { say "  warn    $1"; }
fail() { FAILED=1; say "  FAILED  $1"; }

# A linked worktree builds into ITS OWN frontend/dist, while the public edge
# serves the canonical tree's dist by absolute path (WORKSHOP_DIST in the unit).
# So a UI change applied from a worktree reaches nobody, silently. Say so rather
# than reporting success for a build the operator will never see.
CANONICAL=1
if [ "$(git rev-parse --git-dir 2>/dev/null)" != "$(git rev-parse --git-common-dir 2>/dev/null)" ]; then
  CANONICAL=0
fi

unit_active() { systemctl --user is-active --quiet "$1" 2>/dev/null; }

say "apply: $REPO_ROOT"
[ "$CANONICAL" = 1 ] || warn "this is a linked worktree; the public origin serves the CANONICAL tree's dist, so a UI build here will not reach the operator until this branch merges"

# ---------------------------------------------------------------------------
# 1. The UI bundle the operator's origin serves.
# ---------------------------------------------------------------------------
SRC_ID="$(bash scripts/ui_source_id.sh 2>/dev/null || echo unknown)"
DIST_ID="$(python3 -c '
import json,sys
try:
    print(json.load(open("frontend/dist/build-info.json")).get("source_id") or "none")
except Exception:
    print("none")
' 2>/dev/null || echo none)"

if [ "$SRC_ID" = "unknown" ]; then
  warn "could not compute the UI source id; skipping the bundle check"
elif [ "$SRC_ID" = "$DIST_ID" ]; then
  ok "ui bundle ($SRC_ID)"
else
  plan "ui bundle: dist=$DIST_ID source=$SRC_ID"
  # A BUILD PUBLISHES THE WORKING TREE, NOT THE COMMITTED STATE.
  #
  # That is deliberate and it has to be: an agent that just edited the UI wants
  # to see it, and its edits are uncommitted at that moment too. But on a shared
  # checkout it also means a build carries whatever anyone else has open, and
  # the operator views this origin. Naming those files is the difference between
  # a decision and a surprise -- the post-commit hook, by contrast, only ever
  # builds committed state.
  ui_wip="$(git status --porcelain -- frontend/src packages/ui frontend/index.html 2>/dev/null | awk '{print $2}')"
  if [ -n "$ui_wip" ]; then
    warn "the build will publish these UNCOMMITTED files:"
    printf '            %s\n' $ui_wip
  fi
  if [ "$CHECK" = 0 ]; then
    if bash scripts/build_workshop_public.sh >/dev/null 2>&1; then
      NEW_ID="$(python3 -c '
import json
try:
    print(json.load(open("frontend/dist/build-info.json")).get("source_id") or "none")
except Exception:
    print("none")
' 2>/dev/null || echo none)"
      # Verify, do not assume: a build that "succeeded" but left the stamp
      # unchanged is the failure mode that trained everyone to ignore the
      # staleness warning in the first place.
      if [ "$NEW_ID" = "$SRC_ID" ]; then ACTED=1; ok "ui bundle rebuilt ($NEW_ID)"
      else fail "build finished but dist still stamps $NEW_ID (wanted $SRC_ID)"; fi
    else
      fail "ui build failed -- rerun by hand to see it: bash scripts/build_workshop_public.sh"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 2. The backend. The only genuinely disruptive one, so it is the only one that
#    is deferred rather than immediate.
# ---------------------------------------------------------------------------
BE_SRC="$(bash scripts/backend_source_id.sh 2>/dev/null || echo unknown)"
BE_RUN="$(curl -sf --max-time 4 http://127.0.0.1:8000/healthz 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("source_id") or "unknown")' 2>/dev/null || echo unreachable)"

if [ "$BE_RUN" = "unreachable" ]; then
  warn "backend not answering on :8000; cannot tell what it is running"
elif [ "$BE_RUN" = "unknown" ] || [ "$BE_SRC" = "unknown" ]; then
  # Undecidable is NOT the same as in-sync and must never be reported as it.
  warn "backend does not report a source id (restart it once to pick this up: bash scripts/restart_backend.sh --deferred)"
elif [ "$BE_SRC" = "$BE_RUN" ]; then
  ok "backend ($BE_RUN)"
else
  plan "backend: running=$BE_RUN source=$BE_SRC"
  if [ "$CHECK" = 0 ]; then
    if bash scripts/restart_backend.sh --deferred >/dev/null 2>&1; then
      ACTED=1
      ok "backend restart armed -- it fires after this turn's reply lands"
      say "          verify next turn with: bash scripts/apply.sh --check"
    else
      fail "could not arm the backend restart"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 3 + 4. Two surfaces with no self-reported id: the Vite dev server (reads
#        frontend/.env only at service start) and the public edge (its own
#        code, backend/workshop_public_app.py).
#
#        These use a recorded content hash rather than mtime, because mtime
#        moves on any checkout or rebase and would restart a service for a
#        change that never happened. On the FIRST run the stamp is missing: we
#        record it WITHOUT restarting and say so, so introducing this check can
#        never itself cause a bounce.
# ---------------------------------------------------------------------------
mkdir -p .runtime 2>/dev/null || true

stamped_surface() {  # <label> <file> <stamp> <unit> <restart-cmd...>
  local label="$1" file="$2" stamp="$3" unit="$4"; shift 4
  [ -f "$file" ] || return 0
  local now prev
  now="$(sha1sum "$file" 2>/dev/null | cut -c1-12)"
  prev="$(cat "$stamp" 2>/dev/null || echo "")"
  if [ -z "$prev" ]; then
    if [ "$CHECK" = 1 ]; then
      ok "$label (no baseline recorded yet; a real run records $now without restarting)"
    else
      printf '%s\n' "$now" > "$stamp" 2>/dev/null
      ok "$label (first run: recorded $now, not restarting)"
    fi
    return 0
  fi
  if [ "$now" = "$prev" ]; then ok "$label ($now)"; return 0; fi
  plan "$label changed ($prev -> $now)"
  [ "$CHECK" = 1 ] && return 0
  if ! unit_active "$unit"; then warn "$unit is not running; nothing to restart"; return 0; fi
  if "$@" >/dev/null 2>&1; then
    printf '%s\n' "$now" > "$stamp" 2>/dev/null
    ACTED=1; ok "$label applied"
  else
    fail "could not apply $label"
  fi
}

stamped_surface "vite env (frontend/.env)" \
  "frontend/.env" ".runtime/applied-vite-env.sha" "workshop-frontend.service" \
  systemctl --user restart workshop-frontend.service

stamped_surface "public edge code" \
  "backend/workshop_public_app.py" ".runtime/applied-edge.sha" "workshop-public.service" \
  bash scripts/build_workshop_public.sh --restart-edge

# ---------------------------------------------------------------------------
# The receipt from the last deferred backend restart. This is the loop-closer:
# a deferred restart reports "armed" and then succeeds or fails minutes later,
# long after the agent that armed it has stopped looking. Until now that outcome
# went only to /tmp/workshop-backend.log, which nothing reads -- which is how a
# restart could fail 14 times in one log while every caller was told it worked.
# ---------------------------------------------------------------------------
if [ -f .runtime/apply-last.json ]; then
  python3 - <<'PY' 2>/dev/null || true
import json
try:
    d = json.load(open(".runtime/apply-last.json"))
except Exception:
    raise SystemExit(0)
out = d.get("outcome", "?")
mark = "  ok     " if out == "restarted" else "  warn   "
print(f"{mark} last backend restart: {out} "
      f"(pid {d.get('old_pid')} -> {d.get('new_pid')}) at {d.get('when','?')[:19]}")
PY
fi

if [ "$CHECK" = 1 ]; then
  [ "$DRIFT" = 0 ] && say "in sync" || say "drift found (run: bash scripts/apply.sh)"
  exit "$DRIFT"
fi
[ "$FAILED" = 1 ] && { say "apply: something failed (above)"; exit 1; }
[ "$ACTED" = 0 ] && [ "$DRIFT" = 0 ] && say "already in sync; nothing to do"
exit 0
