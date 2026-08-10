#!/usr/bin/env bash
# Restart the Workshop backend.
#
# Preferred path: the systemd --user service installed by
# scripts/install_service.sh (`systemctl --user restart workshop-backend`).
# This is the canonical setup — it auto-starts on boot and auto-restarts on
# crash. If the unit is not installed (e.g. a minimal/dev box without systemd),
# this falls back to the legacy nohup launch on :8000.
set -euo pipefail

SERVICE=workshop-backend.service
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

have_unit() {
  command -v systemctl >/dev/null 2>&1 \
    && systemctl --user cat "$SERVICE" >/dev/null 2>&1
}

# Deferred mode: schedule the restart DETACHED and after a short delay, then exit
# immediately. A builder agent editing the Workshop runs `restart_backend.sh
# --deferred` as its final action so this backend — which is serving the agent's
# own chat turn — only goes down AFTER the reply has been delivered. setsid
# detaches the restarter from the agent's process tree so it survives the backend
# dying. (See docs/app-platform-runner.md, "restart the server that hosts the agent".)
if [ "${1:-}" = "--deferred" ]; then
  # WAIT FOR THE TURN TO END, don't guess how long it will take.
  #
  # This used to sleep a fixed 5s and then run the restart, which hits the
  # live-turn guard below and is REFUSED whenever the turn is still going. A
  # long turn therefore never restarted at all: the refusal went to a log
  # nobody reads, the command reported success, and the backend silently kept
  # serving old code. That failure is invisible from the chat and it cost a
  # whole session's backend work being "shipped" but not running.
  #
  # So: poll the same condition the guard checks, and restart the moment it
  # clears. `--deferred <seconds>` still caps the wait so a wedged turn can't
  # leave a restarter parked forever.
  #
  # The cap was 30 minutes, and that became wrong when the turn time limit was
  # removed: an agentic turn now runs until the work is done, routinely past
  # half an hour. The old default reproduced the exact failure the polling was
  # added to fix — /tmp/workshop-backend.log has "gave up waiting for turn to
  # end" followed by hours of the backend serving code nobody shipped. Six
  # hours is longer than any turn that is still healthy, and a wedged one is a
  # problem the operator wants to see rather than have quietly restarted.
  max_wait="${2:-21600}"
  self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  turns="$REPO_ROOT/.runtime/turns"
  chat="$REPO_ROOT/.runtime/users"
  # THE CHILD EXITING IS NOT THE REPLY LANDING.
  #
  # A turn's claude process writes its last line and exits; the BACKEND then
  # folds the result and appends the assistant message. That gap is short, and
  # restarting inside it kills the commit: the process is already gone, so the
  # pid check says "no turn running", the backend dies before the append, and
  # the reply exists nowhere. On 2026-08-09 that destroyed a long reply
  # outright, and from the chat it read as the assistant never answering at all.
  #
  # So there are two waits. First for the process, then for the commit: a thread
  # holds `active_run_id` in its meta from the moment a run is claimed until the
  # moment its reply is committed, which is exactly the window that must be
  # clear. The second wait is bounded, because a crashed run can leave the field
  # set until the next startup reconcile and must not park a restarter forever.
  commit_grace=180
  setsid bash -c '
    self="$1"; turns="$2"; chat="$3"; max="$4"; grace="$5"; waited=0
    turn_live() {
      [ -d "$turns" ] || return 1
      for meta in "$turns"/*/meta.json; do
        [ -f "$meta" ] || continue
        grep -q "\"status\": *\"running\"" "$meta" || continue
        pid="$(sed -n "s/.*\"pid\": *\([0-9]\+\).*/\1/p" "$meta" | head -1)"
        [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && return 0
      done
      return 1
    }
    commit_pending() {
      [ -d "$chat" ] || return 1
      grep -rlq "\"active_run_id\": *\"[0-9a-f-]" "$chat"/*/chat/*/meta.json 2>/dev/null
    }
    while turn_live && [ "$waited" -lt "$max" ]; do sleep 3; waited=$((waited+3)); done
    settled=0
    while commit_pending && [ "$settled" -lt "$grace" ]; do sleep 2; settled=$((settled+2)); done
    [ "$settled" -ge "$grace" ] && echo "warn: a run still held its thread after ${grace}s; restarting anyway"
    sleep 1
    bash "$self"
  ' _ "$self" "$turns" "$chat" "$max_wait" "$commit_grace" >>/tmp/workshop-backend.log 2>&1 </dev/null &
  disown || true
  echo "Backend restart armed: it fires as soon as the running turn finishes (waiting up to ${max_wait}s)."
  exit 0
fi

# --- Refuse to restart on top of a live chat turn --------------------------
# Restarting mid-turn is operator-visible damage, not just a blip. The turn's
# process is killed; on startup reattach_running_turns() adopts the orphan and
# re-tails its journal FROM THE BEGINNING, rebuilding the assistant message's
# segments from scratch and persisting the partial rebuild over the stored copy.
# From the chat it looks like the conversation spontaneously rewound to an
# earlier state, and the client's SSE drops at the same moment, so a re-hydrate
# replaces what was on screen with the mid-rebuild version. Nothing is lost —
# the replay restores it — but the operator has no way to know that while it is
# happening.
#
# A turn is live if any turn journal is still marked running with a live pid.
# Pass --force to override (or --deferred, which is usually what you want).
turns_dir="$REPO_ROOT/.runtime/turns"
if [ "${1:-}" != "--force" ] && [ -d "$turns_dir" ]; then
  live=""
  for meta in "$turns_dir"/*/meta.json; do
    [ -f "$meta" ] || continue
    grep -q '"status": *"running"' "$meta" || continue
    pid="$(sed -n 's/.*"pid": *\([0-9]\+\).*/\1/p' "$meta" | head -1)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && { live="$pid"; break; }
  done
  if [ -n "$live" ]; then
    echo "REFUSING: a chat turn is running right now (pid $live)." >&2
    echo "Restarting would kill it and visibly rewind the transcript mid-reply." >&2
    echo "Wait for it to finish, or use:" >&2
    echo "  scripts/restart_backend.sh --deferred   # restart after the reply lands" >&2
    echo "  scripts/restart_backend.sh --force      # restart anyway" >&2
    exit 2
  fi
  # And refuse during the COMMIT window, which the pid check above cannot see:
  # the child is gone, the backend is still assembling and appending the reply.
  # Restarting here loses the reply outright rather than rewinding it.
  #
  # Only a RECENT holder counts. A crashed run leaves `active_run_id` set until
  # the next startup reconcile clears it, and reconcile only runs on a restart:
  # refusing on a stale field would mean the one action that fixes it is the one
  # action forbidden, with --force as the only way out. A commit takes seconds,
  # so a meta untouched for STALE_HOLD_S is not a commit in progress.
  STALE_HOLD_S=300
  holder=""
  for meta in "$REPO_ROOT"/.runtime/users/*/chat/*/meta.json; do
    [ -f "$meta" ] || continue
    grep -q '"active_run_id": *"[0-9a-f-]' "$meta" || continue
    age=$(( $(date +%s) - $(stat -c %Y "$meta" 2>/dev/null || echo 0) ))
    [ "$age" -lt "$STALE_HOLD_S" ] && { holder="$meta"; break; }
  done
  if [ -n "$holder" ]; then
    echo "REFUSING: a run still holds its thread, so its reply is not committed yet." >&2
    echo "  $holder" >&2
    echo "Restarting now would lose that reply. Use --deferred, which waits for it." >&2
    exit 2
  fi
fi

# --- Preferred: systemd --user service -------------------------------------
if have_unit; then
  systemctl --user restart "$SERVICE"
  sleep 2
  if systemctl --user is-active --quiet "$SERVICE"; then
    echo "Backend restarted via systemd ($SERVICE active)."
  else
    echo "systemd restart did not come up active. Recent logs:" >&2
    journalctl --user -u "$SERVICE" --no-pager -n 20 >&2 || true
    exit 1
  fi
  exit 0
fi

# --- Fallback: legacy nohup launch -----------------------------------------
# No systemd unit installed. Snapshot the env of any running backend (to preserve
# secrets passed at launch), overlay auth.env so the OAuth token + 511 key can't
# drift blank, then relaunch. If nothing is running, launch from auth.env +
# secrets.env directly. Consider running scripts/install_service.sh instead.
echo "No systemd unit found; using legacy nohup path. (Run scripts/install_service.sh to adopt systemd.)" >&2
cd "$REPO_ROOT/backend"

envv=()
pid="$(pgrep -f 'uvicorn main:app.*8000' | head -1 || true)"
if [ -n "${pid:-}" ]; then
  while IFS= read -r -d '' line; do
    [ -n "$line" ] && envv+=("$line")
  done < "/proc/$pid/environ"
fi

# Overlay auth/secrets files LAST so their values win over any stale snapshot.
for f in "${WORKSHOP_AUTH_ENV:-$HOME/.config/clawdbot/auth.env}" "$HOME/.config/workshop/secrets.env"; do
  [ -f "$f" ] || continue
  while IFS= read -r line; do
    line="${line#export }"
    case "$line" in ''|\#*) continue;; *=*) envv+=("$line");; esac
  done < "$f"
done
# Defaults the unit would otherwise provide.
envv+=("LETTA_URL=${LETTA_URL:-http://127.0.0.1:8283}")
envv+=("LETTA_SERVER_PASS=${LETTA_SERVER_PASS:-workshop-local}")
envv+=("WORKSHOP_PROJECTS_ROOT=${WORKSHOP_PROJECTS_ROOT:-$HOME}")
envv+=("WORKSHOP_ROOT=${WORKSHOP_ROOT:-$REPO_ROOT}")
envv+=("HOME=$HOME" "PATH=$PATH")

pkill -f 'uvicorn main:app.*8000' || true
sleep 1
env -i "${envv[@]}" nohup .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 \
  >>/tmp/workshop-backend.log 2>&1 &
disown || true
sleep 2
newpid="$(pgrep -f 'uvicorn main:app.*8000' | head -1 || true)"
echo "Backend restarted via nohup (new pid ${newpid:-unknown}). Log: /tmp/workshop-backend.log"
