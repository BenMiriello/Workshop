#!/usr/bin/env bash
# Build the Workshop SPA and (re)start the public edge app.
#
# Mirrors the birds dist-springer rebuild step. Run after any frontend change
# that should reach workshop.benmiriello.com. The public edge serves the freshly
# built frontend/dist and proxies the API to the backend on :8000.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Building frontend -> frontend/dist ..."
(cd "$REPO_ROOT/frontend" && npm run build)

if systemctl --user cat workshop-public.service >/dev/null 2>&1; then
  echo "Restarting workshop-public.service ..."
  systemctl --user restart workshop-public.service
  sleep 2
  systemctl --user is-active --quiet workshop-public.service \
    && echo "public edge active on :8002" \
    || { echo "public edge failed to come up"; journalctl --user -u workshop-public.service -n 20 --no-pager; exit 1; }
else
  # No systemd unit: launch the edge directly. Port is configurable; the edge's
  # backend target and dist dir come from WORKSHOP_BACKEND / WORKSHOP_DIST (see
  # workshop_public_app.py), inherited from this shell's environment.
  PORT="${WORKSHOP_PUBLIC_PORT:-8002}"
  echo "workshop-public.service not installed; launching edge via nohup on :${PORT} ..."
  cd "$REPO_ROOT/backend"
  pkill -f "uvicorn workshop_public_app:app.*${PORT}" || true
  sleep 1
  nohup .venv/bin/uvicorn workshop_public_app:app --host 0.0.0.0 --port "${PORT}" \
    >>/tmp/workshop-public.log 2>&1 &
  disown || true
  sleep 2
  echo "public edge (nohup) on :${PORT}. Log: /tmp/workshop-public.log"
fi
