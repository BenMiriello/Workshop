#!/usr/bin/env bash
# Build the Workshop SPA that the public edge serves from frontend/dist.
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
# shellcheck source=/dev/null
[ -f "$REPO_ROOT/scripts/lib/instance.sh" ] && source "$REPO_ROOT/scripts/lib/instance.sh"
PUBLIC_UNIT="${WORKSHOP_PUBLIC_UNIT:-workshop-public.service}"

restart_edge="${RESTART_EDGE:-0}"
for arg in "$@"; do
  case "$arg" in
    --restart-edge) restart_edge=1 ;;
  esac
done

# ONE build at a time. The post-commit hook fires a background rebuild on every
# commit, so a manual build can easily overlap one. Two `vite build` runs writing
# the same dist/ can leave a half-written bundle on the LIVE site, and they also
# race on the single stamp capture file (scripts/stamp_build.sh --capture): the
# first --write consumes it, so the second stamps `unknown-no-capture` and the
# freshness check then reports STALE forever. A permanently-crying staleness
# warning is worse than none, because it trains everyone to ignore the one signal
# that says the operator is looking at old code. That is exactly what happened.
LOCK="$REPO_ROOT/tmp/ui-build.lock"
mkdir -p "$REPO_ROOT/tmp"
exec 9>"$LOCK"
if ! flock -w 300 9; then
  echo "build_workshop_public: timed out waiting for another build to finish" >&2
  exit 1
fi

# THE LIVE dist/ IS NEVER EMPTIED. vite empties its outDir before writing, which
# on the live dir meant two things on every build: the edge answered "frontend
# not built" to any load during the write, and every hashed chunk an open tab had
# not fetched yet was gone for good, so its next navigation 404'd and forced a
# full reload through the splash, often while apply.sh was restarting the backend.
# So the build goes to a staging dir and is merged in: new hashed assets are added
# beside the old ones (never rewritten in place, a tab may be reading one), every
# other file is renamed into place with index.html last, and assets no build has
# produced for a week are pruned.
LIVE="$REPO_ROOT/frontend/dist"
STAGE="$REPO_ROOT/tmp/dist-next"

merge_into_live() {
  mkdir -p "$LIVE/assets"
  (cd "$STAGE/assets" && find . -type f -print0) | while IFS= read -r -d '' f; do
    if [ -e "$LIVE/assets/$f" ]; then
      touch "$LIVE/assets/$f"
    else
      mkdir -p "$(dirname "$LIVE/assets/$f")"
      cp "$STAGE/assets/$f" "$LIVE/assets/$f.tmp" && mv -f "$LIVE/assets/$f.tmp" "$LIVE/assets/$f"
    fi
  done
  for f in "$STAGE"/*; do
    name="$(basename "$f")"
    case "$name" in assets|index.html) continue ;; esac
    if [ -d "$f" ]; then cp -r "$f" "$LIVE/"; continue; fi
    cp "$f" "$LIVE/$name.tmp" && mv -f "$LIVE/$name.tmp" "$LIVE/$name"
  done
  cp "$STAGE/index.html" "$LIVE/index.html.tmp" && mv -f "$LIVE/index.html.tmp" "$LIVE/index.html"
  find "$LIVE/assets" -type f -mtime +7 -delete
}

echo "Building frontend -> frontend/dist (staged in tmp/dist-next) ..."
rm -rf "$STAGE"
(cd "$REPO_ROOT/frontend" && WORKSHOP_DIST_OUT="$STAGE" npm run build -- --outDir "$STAGE" --emptyOutDir)
merge_into_live
rm -rf "$STAGE"

if [ "$restart_edge" != "1" ]; then
  echo "Edge not restarted (serves dist from disk — change is already live)."
  echo "Pass --restart-edge only when backend/workshop_public_app.py changed."
  exit 0
fi

# `show -p LoadState` is a D-Bus query, unlike `cat` which reads the unit file
# off disk client-side and fails inside a scoped-exec sandbox that does not
# bind ~/.config/systemd (see restart_backend.sh have_unit()).
if [ "$(systemctl --user show -p LoadState --value "$PUBLIC_UNIT" 2>/dev/null)" = "loaded" ]; then
  echo "Restarting $PUBLIC_UNIT (edge code changed) ..."
  systemctl --user restart "$PUBLIC_UNIT"
  sleep 2
  systemctl --user is-active --quiet "$PUBLIC_UNIT" \
    && echo "public edge active on :${WORKSHOP_PUBLIC_PORT:-8002}" \
    || { echo "public edge failed to come up"; journalctl --user -u "$PUBLIC_UNIT" -n 20 --no-pager; exit 1; }
else
  # No systemd unit: launch the edge directly. Port is configurable; the edge's
  # backend target and dist dir come from WORKSHOP_BACKEND / WORKSHOP_DIST (see
  # workshop_public_app.py), inherited from this shell's environment.
  PORT="${WORKSHOP_PUBLIC_PORT:-8002}"
  echo "$PUBLIC_UNIT not installed; launching edge via nohup on :${PORT} ..."
  cd "$REPO_ROOT/backend"
  pkill -f "uvicorn workshop_public_app:app.*${PORT}" || true
  sleep 1
  nohup .venv/bin/uvicorn workshop_public_app:app --host 127.0.0.1 --port "${PORT}" \
    >>/tmp/workshop-public.log 2>&1 &
  disown || true
  sleep 2
  echo "public edge (nohup) on :${PORT}. Log: /tmp/workshop-public.log"
fi
