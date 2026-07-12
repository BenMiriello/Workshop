#!/usr/bin/env bash
# Build the Workshop SPA for workshop.benmiriello.com.
#
# The public edge (workshop-public.service on :8002) serves frontend/dist from
# DISK, per request (StaticFiles + FileResponse) — so a frontend rebuild is
# picked up LIVE with no restart. Restarting the edge needlessly drops in-flight
# requests (including the live chat SSE) and flashes a Cloudflare 502 while it
# comes back up. So by default we ONLY rebuild.
#
# Restart the edge ONLY when the edge's OWN code changed
# (backend/workshop_public_app.py) — pass --restart-edge (or RESTART_EDGE=1).
#
#   scripts/build_workshop_public.sh                # frontend change: build only
#   scripts/build_workshop_public.sh --restart-edge # edge-code change: build + restart
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

restart_edge="${RESTART_EDGE:-0}"
for arg in "$@"; do
  case "$arg" in
    --restart-edge) restart_edge=1 ;;
  esac
done

echo "Building frontend -> frontend/dist ..."
(cd "$REPO_ROOT/frontend" && npm run build)

if [ "$restart_edge" != "1" ]; then
  echo "Edge not restarted (serves dist from disk — change is already live)."
  echo "Pass --restart-edge only when backend/workshop_public_app.py changed."
  exit 0
fi

if systemctl --user cat workshop-public.service >/dev/null 2>&1; then
  echo "Restarting workshop-public.service (edge code changed) ..."
  systemctl --user restart workshop-public.service
  sleep 2
  systemctl --user is-active --quiet workshop-public.service \
    && echo "public edge active on :8002" \
    || { echo "public edge failed to come up"; journalctl --user -u workshop-public.service -n 20 --no-pager; exit 1; }
else
  echo "workshop-public.service not installed yet — run scripts/install_public_service.sh first."
fi
