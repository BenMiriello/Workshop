#!/usr/bin/env bash
# Apply a finished change to a running project — the builder's "make it live" tool.
#
#   restart_app.sh <slug> [service]
#
# - SELF / platform project (workshop): the backend serves this very chat, so it
#   does a DEFERRED + detached backend restart (your reply lands first, then it
#   reloads). Frontend changes are already live via Vite HMR — no restart needed.
# - App project: restarts the named service, or every currently-running service,
#   through the app runner (separate processes — safe, never touches this chat).
#   Vite/dev-server services hot-reload on their own; pass the specific backend
#   service you changed rather than bouncing the frontend unnecessarily.
#
# Run this when a change is FINISHED — that's the expected way to update the app.
set -euo pipefail

slug="${1:?usage: restart_app.sh <slug> [service]}"
svc="${2:-}"
here="$(cd "$(dirname "$0")" && pwd)"
base="http://127.0.0.1:8000/base-apps/run/${slug}"

status="$(curl -s "${base}/status" 2>/dev/null || echo '{}')"

# Self / platform project → deferred backend restart.
if printf '%s' "$status" | grep -q '"self"[[:space:]]*:[[:space:]]*true'; then
  exec bash "${here}/restart_backend.sh" --deferred
fi

restart_one() {
  local s="$1"
  if curl -s -o /dev/null -w '%{http_code}' -X POST "${base}/services/${s}/restart" | grep -q '^2'; then
    echo "restarted ${slug}/${s}"
  else
    echo "could not restart ${slug}/${s} (is it running?)" >&2
  fi
}

if [ -n "$svc" ]; then
  restart_one "$svc"
  exit 0
fi

# No service named → restart every currently-running service.
running="$(printf '%s' "$status" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
print(" ".join(s["name"] for s in d.get("services", []) if s.get("status") == "running"))
' 2>/dev/null || true)"

if [ -z "$running" ]; then
  echo "no running services to restart for ${slug}"
  exit 0
fi
for s in $running; do restart_one "$s"; done
