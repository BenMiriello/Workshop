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
# ONE SURFACE IS REPORTED AND NEVER APPLIED: the database schema. Every other
# check here ends in this script fixing the thing; the schema check ends in it
# telling you and refusing to restart the backend on top of the gap. Applying a
# migration to a live database is a deliberate act with a blast radius this
# script cannot judge, so it stays the operator's, in a terminal. See section 2.
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
PUBLISH_WIP=0
UI_SKIPPED=0
BE_SKIPPED=0
BE_SKIP_WHY=""      # named, because "still stale by choice" must say whose choice
for arg in "$@"; do
  case "$arg" in
    --check|-n) CHECK=1 ;;
    --publish-wip) PUBLISH_WIP=1 ;;
    # The whole leading comment block, however long it grows -- a hardcoded line
    # range silently truncates the help the moment anyone edits the header.
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
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
# Drift this script will NOT close. `plan` prints "apply" on a real run, which
# would be a lie for the schema: nothing here runs a migration, by design.
stale(){ DRIFT=1; say "  STALE   $1"; }

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
  # THE BUILT ORIGIN SERVES COMMITTED WORK.
  #
  # A build takes the working tree, so on a shared checkout it carries whatever
  # anyone else has open. Uncommitted is the definition of unfinished, and this
  # origin is the one the operator looks at -- so a backend-only change should
  # not drag somebody's half-written chat UI onto it, which is exactly the
  # situation this hit on 2026-08-13 (ten files, mid-refactor, not mine).
  #
  # Skipping is safe because it is not the only path: committing rebuilds
  # automatically (.githooks/post-commit), committing as you go is already the
  # repo rule, and the :5173 dev origin serves the working tree live for
  # iterating. So the owner of that work still gets it published, by the act
  # that already means "this is finished".
  #
  # A stale bundle with a CLEAN tree is a different thing entirely -- a missed
  # hook, a failed build -- and that is the case this must always fix.
  ui_wip="$(git status --porcelain -- frontend/src packages/ui frontend/index.html 2>/dev/null | awk '{print $2}')"
  if [ -n "$ui_wip" ] && [ "$PUBLISH_WIP" = 0 ]; then
    warn "not building: the bundle is stale because of UNCOMMITTED UI work:"
    printf '            %s\n' $ui_wip
    say  "          commit it and the post-commit hook builds, or: bash scripts/apply.sh --publish-wip"
    say  "          this is expected on a shared tree -- the work above may be"
    say  "          another agent's, and publishing it would ship a half-edit."
    say  "          Why it declines rather than builds: docs/applying-changes.md"
    UI_SKIPPED=1
  fi
  if [ "$CHECK" = 0 ] && [ "$UI_SKIPPED" = 0 ]; then
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
# 2. The database schema: the tree's alembic head against the live database's
#    alembic_version. REPORTED, NEVER APPLIED.
#
#    THIS IS THE ONE SURFACE THIS SCRIPT DOES NOT FIX. Every other check ends in
#    a rebuild or a restart; this one ends in a refusal, because applying DDL to
#    a live database is an act whose blast radius this script cannot judge.
#
#    It exists because on 2026-09-01 a commit added two columns to chat_thread
#    plus the migration that creates them, and NOTHING in this deploy path ran
#    alembic -- not this script, not restart_backend.sh. Live stayed at
#    b9f3e2d07a16, a read through the committed code answered
#    `UndefinedColumn: column chat_thread.armed does not exist`, and a deferred
#    restart was already armed. The commit was reverted to save the app.
#
#    restart_backend.sh's import preflight is structurally unable to catch it:
#    a missing column fails at QUERY time and `import main` imports perfectly.
#    So the comparison has to happen here, before the restart is armed.
# ---------------------------------------------------------------------------
# The venv python has alembic and sqlalchemy; the system one usually does not,
# and answers "COULD NOT DETERMINE" rather than pretending.
MIG_PY="backend/.venv/bin/python"
[ -x "$MIG_PY" ] || MIG_PY="$(command -v python3 2>/dev/null || true)"
MIG_STATE="unknown"
MIG_MSG="schema: COULD NOT DETERMINE -- no python available to run scripts/migration_state.py"
MIG_FIX=""
if [ -n "$MIG_PY" ] && [ -f scripts/migration_state.py ]; then
  mig_line="$("$MIG_PY" scripts/migration_state.py 2>/dev/null | tail -1)"
  if [ -n "$mig_line" ]; then
    MIG_STATE="${mig_line%%$'\t'*}"
    mig_rest="${mig_line#*$'\t'}"
    MIG_MSG="${mig_rest%%$'\t'*}"
    MIG_FIX="${mig_rest#*$'\t'}"
  fi
fi

MIG_BLOCKS=0    # the tree needs schema the live database does not have
MIG_UNKNOWN=0   # the comparison did not happen; this is NOT "in sync"
case "$MIG_STATE" in
  ok)
    ok "$MIG_MSG" ;;
  tree_ahead)
    MIG_BLOCKS=1
    stale "$MIG_MSG"
    say  "          apply.sh does not run migrations, because the migration has to land"
    say  "          BEFORE the restart. That is ORDER, not permission: this is a dev"
    say  "          installation and applying it is ordinary work. Run it, then rerun:"
    say  "            $MIG_FIX"
    say  "          UNLESS the line above says 'per a direct connection' -- then the"
    say  "          database measured is not the one the backend uses (a sandbox), and"
    say  "          migrating it would change the wrong one." ;;
  live_ahead)
    # The database is ahead of the tree -- a revert, or an old checkout. Real
    # drift, so it is reported. It does NOT hold the restart: the code on disk
    # never names whatever that revision added, so the extra schema is inert,
    # and after a revert like 2026-09-01's the restart is the very thing that
    # puts the app back on code the database can serve. Blocking it would hold
    # the app down to protect it from columns nothing reads.
    stale "$MIG_MSG"
    say  "          this does not hold the restart back: $MIG_FIX" ;;
  not_configured)
    warn "$MIG_MSG" ;;
  *)
    # A check that silently passes when it could not measure is the failure mode
    # this repo keeps getting bitten by. Undecidable is loud, and it is not ok.
    MIG_UNKNOWN=1
    warn "$MIG_MSG"
    [ -n "$MIG_FIX" ] && say "          $MIG_FIX" ;;
esac

# ---------------------------------------------------------------------------
# 3. The backend. The only genuinely disruptive one, so it is the only one that
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
  # THE RUNNING BACKEND RUNS COMMITTED WORK, for exactly the reason the built
  # origin serves it (see the UI section above). A restart starts the WORKING
  # TREE, so on a shared checkout it starts whatever anyone else has open, and
  # backend_source_id.sh hashes the working tree too -- which means someone
  # else's half-written edit is itself what reports "stale" here.
  #
  # This is not hypothetical and it is not symmetric-for-tidiness. On
  # 2026-08-14 the UI half of this rule was added after a concurrent build
  # published a half-finished chat migration to the operator's screen; the
  # backend half was left open, and within the hour the tree held another
  # agent's mid-edit knowledge.py that raised NameError on the notes write
  # path. restart_backend.sh's preflight would NOT have caught it: it does
  # what uvicorn does, `import main`, and a name that only resolves when a
  # function is called imports perfectly.
  #
  # Skipping strands nothing. Committing is the act that already means
  # "finished", and the owner of the work restarts by doing it.
  # `backend/tests/` is NOT in this set, for the same reason it is not in
  # backend_source_id.sh: uvicorn does not import it, so a test file cannot
  # change what the server serves and must not be able to hold a restart. On
  # 2026-08-26 an uncommitted `tests/conftest.py` did exactly that -- it moved
  # the source id, which asked for a restart, which this guard then refused,
  # leaving a finished route unserved behind a fixture nobody was running.
  # Excluding it ENFORCES this guard's stated rule (do not start someone else's
  # half-written code) rather than relaxing it.
  # THE SCHEMA COMES FIRST, before the uncommitted-work question, because it is
  # the more certain outage of the two. Someone else's half-written file MIGHT
  # not import; code whose migration has not landed WILL fail every request that
  # touches the new column, and it will do it after the restart, from a service
  # that started cleanly. Same shape as the guard below and for the same reason:
  # decline, say exactly why, name what to do.
  if [ "$MIG_BLOCKS" = 1 ]; then
    warn "not restarting: this tree's migrations have not been applied to the live database (above)."
    say  "          Restarting onto that is an outage, not a risk: the service starts fine and"
    say  "          then fails every query for a column that is not there. The import preflight"
    say  "          cannot see it. Apply the migration and rerun:"
    say  "            $MIG_FIX"
    BE_SKIPPED=1
    BE_SKIP_WHY="unapplied migrations above"
  fi
  be_wip="$(git status --porcelain -uall -- backend 2>/dev/null | awk '{print $NF}' \
    | grep -E '(\.py|requirements[^/]*\.txt)$' | grep -v 'workshop_public_app\.py$' \
    | grep -v '^backend/tests/' || true)"
  if [ -n "$be_wip" ] && [ "$PUBLISH_WIP" = 0 ]; then
    warn "not restarting: the backend is stale because of UNCOMMITTED work:"
    printf '            %s\n' $be_wip
    say  "          commit it and rerun, or: bash scripts/apply.sh --publish-wip"
    say  "          this is expected on a shared tree -- the work above may be"
    say  "          another agent's, and restarting onto a half-written file"
    say  "          takes the backend down for everyone."
    say  "          Why it declines rather than restarts: docs/applying-changes.md"
    BE_SKIPPED=1
    [ -n "$BE_SKIP_WHY" ] || BE_SKIP_WHY="uncommitted backend work above"
  fi
  if [ "$CHECK" = 0 ] && [ "$BE_SKIPPED" = 0 ]; then
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
  # EXIT 0 MEANS "MEASURED, AND IN SYNC" -- never "asked, and could not tell".
  # The schema is the one surface whose live side can be unreachable, and a
  # check that passes when it could not measure is worth less than no check.
  if [ "$DRIFT" = 1 ]; then
    say "drift found (run: bash scripts/apply.sh)"
    [ "$MIG_BLOCKS" = 1 ] && say "  ...except the schema, which apply.sh will not touch: $MIG_FIX"
  elif [ "$MIG_UNKNOWN" = 1 ]; then
    say "NOT in sync as far as this can tell: the schema could not be measured (above)"
  else
    say "in sync"
  fi
  exit $(( DRIFT || MIG_UNKNOWN ))
fi
[ "$FAILED" = 1 ] && { say "apply: something failed (above)"; exit 1; }
# Never let a skip pass for a success. Something deliberately not done still has
# to be said out loud, or the next reader concludes the bundle is current.
[ "$UI_SKIPPED" = 1 ] && say "apply: the bundle is still STALE by choice (uncommitted UI work above)"
[ "$BE_SKIPPED" = 1 ] && say "apply: the backend is still STALE by choice ($BE_SKIP_WHY)"
[ "$MIG_UNKNOWN" = 1 ] && say "apply: the schema was NOT compared (above), so this run cannot claim in sync"
[ "$ACTED" = 0 ] && [ "$DRIFT" = 0 ] && [ "$MIG_UNKNOWN" = 0 ] && say "already in sync; nothing to do"
exit 0
