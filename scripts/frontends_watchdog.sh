#!/usr/bin/env bash
# Frontend watchdog.
#
# When a Vite dev server dies (reboot, OOM, stray kill) the app 502s behind
# Tailscale Serve, which is exactly how the Commute app (:5174 -> :8444) went
# dark. This restarts any frontend that isn't listening.
#
# IT PREFERS SYSTEMD, AND THAT IS A CORRECTION. This script used to assert
# "there is no systemd on this box" and always relaunch vite itself with
# setsid+nohup. That stopped being true once workshop-frontend.service was
# installed: two owners for one port, and if this one won the race it held
# :5173 outside the unit's cgroup and left the unit crash-looping on a port it
# could not bind. So when a unit exists, restarting the UNIT is the whole job;
# the bare relaunch is now only the fallback for a box that has no unit.
#
# Installed via cron (*/2). Idempotent: a port that's already bound is left
# alone, so re-runs never double-spawn. Backend (uvicorn :8000) is deliberately
# NOT managed here — it carries env-sensitive secrets and must be restarted by
# hand with its EnvironmentFile, not blind-relaunched.
set -u

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RUNTIME="$WORKSHOP/.runtime"
LOG="$RUNTIME/frontends_watchdog.log"
mkdir -p "$RUNTIME"

# Cron runs with a minimal env; this box installs node via nvm. Put node on PATH.
if [ -s "$HOME/.nvm/nvm.sh" ]; then . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1; fi
for d in "$HOME"/.nvm/versions/node/*/bin; do [ -d "$d" ] && PATH="$d:$PATH"; done
export PATH

log() { echo "[$(date -Is)] $*" >> "$LOG"; }

is_listening() { ss -ltn 2>/dev/null | grep -q ":$1 "; }

# vite lives in the workspace's node_modules, which after a root `npm install`
# means the ROOT one: npm hoists, and frontend/node_modules/.bin is then empty.
# Checking only the app-local path made this watchdog log "deps not installed?"
# on a perfectly good checkout and never restart anything.
find_vite() {
  local dir="$1"
  for c in "$dir/node_modules/.bin/vite" "$WORKSHOP/node_modules/.bin/vite"; do
    [ -x "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}

ensure() {
  local name="$1" port="$2" dir="$3" unit="$4"
  if is_listening "$port"; then return 0; fi

  # A unit owns this port -> it is systemd's job. Restarting the unit keeps the
  # process inside its cgroup, which is what makes it restartable at all.
  if [ -n "$unit" ] && command -v systemctl >/dev/null 2>&1 \
     && systemctl --user cat "$unit" >/dev/null 2>&1; then
    log "frontend '$name' down on :$port — restarting unit $unit"
    systemctl --user restart "$unit" >/dev/null 2>&1 \
      && log "  restarted $unit" \
      || log "  FAILED to restart $unit (journalctl --user -u $unit)"
    return 0
  fi

  local vite
  if ! vite="$(find_vite "$dir")"; then
    log "frontend '$name': vite not found under $dir or $WORKSHOP (run npm install at the repo root) — skipping"
    return 1
  fi
  log "frontend '$name' down on :$port — restarting (no systemd unit)"
  ( cd "$dir" && setsid nohup "$vite" \
      > "/tmp/workshop-$name-frontend.log" 2>&1 < /dev/null & )
  log "  relaunched '$name'"
}

ensure workshop "${WORKSHOP_VITE_PORT:-5173}" "$WORKSHOP/frontend" workshop-frontend.service
# Commute was retired as a standalone app (:8444 PWA) — it's now a native page
# inside the Workshop frontend at /apps/commute. No separate dev server.
