#!/usr/bin/env bash
# Start the frontend Vite dev server (hot reload) against THIS deployment's
# backend and origin — not whatever the tracked defaults say.
#
# Reads the same per-deployment env this instance's backend service uses
# (WORKSHOP_CANONICAL_ORIGIN, WORKSHOP_TAILNET_HOST, backend PORT) from the
# Control Panel env file if one is registered, so the dev server proxies /api
# to the right backend and doesn't get redirect-bounced to another instance's
# origin. Override WORKSHOP_DEV_PORT if this box's default (5173) is already
# taken by another deployment.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Pull only the specific KEY=VALUE lines we need (not a full `source` — these
# files also carry a COMMAND= line with embedded shell syntax that isn't safe
# to source directly).
env_val() {
  local key="$1" f
  for f in "$HOME/.config/control-panel/env/workshop-backend.env" \
           "$HOME/.config/control-panel/env/backend.env"; do
    [ -f "$f" ] || continue
    grep -m1 "^${key}=" "$f" | cut -d= -f2- && return 0
  done
  return 1
}

export WORKSHOP_BACKEND_PORT="${WORKSHOP_BACKEND_PORT:-$(env_val PORT || echo 8000)}"
export WORKSHOP_CANONICAL_ORIGIN="${WORKSHOP_CANONICAL_ORIGIN:-$(env_val WORKSHOP_CANONICAL_ORIGIN || true)}"
export WORKSHOP_TAILNET_HOST="${WORKSHOP_TAILNET_HOST:-$(env_val WORKSHOP_TAILNET_HOST || true)}"
export WORKSHOP_DEV_PORT="${WORKSHOP_DEV_PORT:-5173}"

echo "Dev server: backend proxy -> :${WORKSHOP_BACKEND_PORT}, listening on :${WORKSHOP_DEV_PORT}"
cd "$REPO_ROOT/frontend"
exec npm run dev -- --port "$WORKSHOP_DEV_PORT" --strictPort
