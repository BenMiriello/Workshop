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
  echo "workshop-public.service not installed yet — run scripts/install_public_service.sh first."
fi
