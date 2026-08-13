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

repo_root="$(cd "${here}/.." && pwd)"

# Self / platform project → deferred backend restart.
#
# RESOLVED FROM DISK, NOT FROM THE API. This used to ask
# `GET /base-apps/run/<slug>/status` for a `"self": true` flag — an
# AUTHENTICATED endpoint. From a script it answers
# `{"detail":"authentication required"}`, the grep finds no `self`, and the
# platform falls through to the app-runner branch below, which prints
# "no running services to restart" and exits 0 having restarted NOTHING.
# The one tool documented as "apply your backend change" was a silent no-op on
# the one project every agent uses it for, and the failure looks like success.
#
# The registry file has the same fact and needs no credentials.
is_self() {
  python3 - "$1" "$repo_root" <<'PY' 2>/dev/null
import json, os, sys
from pathlib import Path
slug, root = sys.argv[1], Path(sys.argv[2]).resolve()
reg = Path(os.environ.get("WORKSHOP_PROJECTS_REGISTRY",
                          str(root / ".runtime" / "projects.json")))
try:
    projects = (json.loads(reg.read_text()).get("projects") or {})
except Exception:
    projects = {}
p = projects.get(slug) or {}
# Either the registry says so, or the project simply IS this checkout. The
# second test is what makes an alias work: the platform is registered as
# `development`, while the docs and every agent brief say `workshop`.
if p.get("self") or (p.get("dir") and Path(p["dir"]).resolve() == root):
    raise SystemExit(0)
if not p and slug in ("workshop", "development", "platform"):
    raise SystemExit(0)
raise SystemExit(1)
PY
}

if is_self "$slug"; then
  echo "platform project (${slug}) → deferred backend restart"
  exec bash "${here}/restart_backend.sh" --deferred
fi

status="$(curl -s "${base}/status" 2>/dev/null || echo '{}')"

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
