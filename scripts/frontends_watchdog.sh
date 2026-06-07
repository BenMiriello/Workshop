#!/usr/bin/env bash
# Frontend watchdog.
#
# The Workshop Vite dev servers run as bare nohup/setsid processes — there is
# no systemd on this box. When one dies (reboot, OOM, stray kill) the app 502s
# behind Tailscale Serve, which is exactly how the Commute app (:5174 -> :8444)
# went dark. This restarts any frontend that isn't listening.
#
# Installed via cron (*/2). Idempotent: a port that's already bound is left
# alone, so re-runs never double-spawn. Backend (uvicorn :8000) is deliberately
# NOT managed here — it carries env-sensitive secrets and must be restarted by
# hand with its EnvironmentFile, not blind-relaunched.
set -u

RUNTIME=/home/clawd-a/workshop/.runtime
LOG="$RUNTIME/frontends_watchdog.log"
mkdir -p "$RUNTIME"

# Cron runs with a minimal env; this box installs node via nvm. Put node on PATH.
if [ -s "$HOME/.nvm/nvm.sh" ]; then . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1; fi
for d in "$HOME"/.nvm/versions/node/*/bin; do [ -d "$d" ] && PATH="$d:$PATH"; done
export PATH

log() { echo "[$(date -Is)] $*" >> "$LOG"; }

is_listening() { ss -ltn 2>/dev/null | grep -q ":$1 "; }

ensure() {
  local name="$1" port="$2" dir="$3"
  if is_listening "$port"; then return 0; fi
  if [ ! -x "$dir/node_modules/.bin/vite" ]; then
    log "frontend '$name': vite not found in $dir (deps not installed?) — skipping"
    return 1
  fi
  log "frontend '$name' down on :$port — restarting"
  ( cd "$dir" && setsid nohup ./node_modules/.bin/vite \
      > "/tmp/workshop-$name-frontend.log" 2>&1 < /dev/null & )
  log "  relaunched '$name'"
}

ensure workshop 5173 /home/clawd-a/workshop/frontend
# Commute was retired as a standalone app (:8444 PWA) — it's now a native page
# inside the Workshop frontend at /apps/commute. No separate dev server.
