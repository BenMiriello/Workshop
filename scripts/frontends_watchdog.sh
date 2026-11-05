#!/usr/bin/env bash
# Frontend watchdog.
#
# When a Vite dev server dies (reboot, OOM, stray kill) the app 502s behind
# Tailscale Serve, which is exactly how an app's dev frontend once went
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
#
# AN INSTALLATION MAY NOT WANT A DEV SERVER AT ALL, and until 2026-09-05 this
# script gave it no way to say so. A dev server is a second origin serving
# unbuilt source, and a deployment whose users are on the built bundle has no
# reason to run one. Because this watchdog re-checks every two minutes and will
# relaunch OUTSIDE systemd when it cannot reach the user bus (cron's env has no
# DBUS_SESSION_BUS_ADDRESS, so the `show -p LoadState` probe below comes back
# empty and the fallback wins), simply stopping the unit does not stop the dev
# server: it comes back within two minutes as a bare process this script owns,
# and `systemctl --user status` then reports the unit inactive while the port is
# very much listening. That is measured, not hypothetical — SEC-L2, ticket
# 61ac9564. So the opt-out has to live here, where the resurrection happens.
#
# Set WORKSHOP_DEV_SERVER=0 in ~/.config/workshop/instance.env to opt out. It is
# read via lib/instance.sh, because cron passes almost no environment and a knob
# that only works from an interactive shell would be worse than none.
set -u

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Installation identity (WORKSHOP_DEV_SERVER, WORKSHOP_VITE_PORT). Never fatal:
# a watchdog that dies because the operator has no instance.env is a worse
# failure than one that runs with defaults.
# shellcheck source=lib/instance.sh
. "$WORKSHOP/scripts/lib/instance.sh" 2>/dev/null || true
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
  # `show -p LoadState` is a D-Bus query, unlike `cat` which reads the unit
  # file off disk client-side and fails inside a scoped-exec sandbox that does
  # not bind ~/.config/systemd (see restart_backend.sh have_unit()).
  if [ -n "$unit" ] && command -v systemctl >/dev/null 2>&1 \
     && [ "$(systemctl --user show -p LoadState --value "$unit" 2>/dev/null)" = "loaded" ]; then
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

if [ "${WORKSHOP_DEV_SERVER:-1}" = "0" ]; then
  # Deliberately off for this installation. Say it once per run rather than
  # silently doing nothing: the failure this guards against is a later reader
  # finding :5173 dead, assuming the watchdog is broken, and "fixing" it.
  log "dev server disabled for this installation (WORKSHOP_DEV_SERVER=0) — not starting or restarting it"
else
  ensure workshop "${WORKSHOP_VITE_PORT:-5173}" "$WORKSHOP/frontend" workshop-frontend.service
fi
