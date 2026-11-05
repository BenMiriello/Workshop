#!/usr/bin/env bash
# Prove that the app the OPERATOR opens is serving the code on disk.
#
#   scripts/check_app_live.sh <slug> [service]
#
# The failure this exists to catch: an agent verifies an app by curling
# 127.0.0.1:<port> directly, sees it working, and reports it done — while the
# operator opens Workshop and gets a blank frame. Hitting the port bypasses the
# entire chain that actually serves them (edge relay -> backend -> runner proxy
# -> the app's base path), so it proves almost nothing. This walks the whole
# chain instead, and every step is a real failure that has happened here.
#
# Exit non-zero on any broken link. Run it before calling an app change done.
set -uo pipefail

slug="${1:?usage: check_app_live.sh <slug> [service]}"
svc="${2:-}"
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
[ -f "$here/lib/instance.sh" ] && source "$here/lib/instance.sh" 2>/dev/null || true
origin="${WORKSHOP_PUBLIC_ORIGIN:-}"
backend="http://127.0.0.1:${WORKSHOP_BACKEND_PORT:-8000}"
fail=0
note() { printf '  %s\n' "$1"; }
bad()  { printf '  FAIL: %s\n' "$1"; fail=1; }

echo "check_app_live: $slug"

# 1. The manifest, and the service that is actually exposed.
manifest="$(ls "$here/../projects/$slug/workshop.app.json" 2>/dev/null || true)"
[ -n "$manifest" ] || { bad "no workshop.app.json for $slug"; exit 1; }
if [ -z "$svc" ]; then
  svc="$(python3 -c "
import json,sys
m=json.load(open('$manifest'))
s=[x for x in m['services'] if x.get('expose')] or m['services']
print(s[0]['name'])
" 2>/dev/null)"
fi
[ -n "$svc" ] || { bad "could not determine a service"; exit 1; }
note "service: $svc"

# 2. The runner's own bookkeeping. This is what decides whether Workshop will
#    frame the app at all: if the runner does not think the service is running,
#    the operator gets an empty panel no matter how healthy the process is.
reg="${WORKSHOP_PORT_REGISTRY:-$here/../.runtime/ports.json}"
port="$(python3 -c "
import json
try: d=json.load(open('$reg'))
except Exception: raise SystemExit
a=d.get('allocations',{}).get('app:$slug/$svc')
print(a['port'] if a else '')
" 2>/dev/null)"
[ -n "$port" ] || { bad "no port allocation for app:$slug/$svc (is it started?)"; exit 1; }
note "port: $port"

base="/base-apps/run/$slug/proxy/$svc/"

# 3. The app answers ON ITS PROXY BASE PATH, not merely on /. The runner forwards
#    the full path verbatim, so an app served at / 404s or SPA-falls-back here.
body="$(curl -s -m 8 "http://127.0.0.1:$port$base" || true)"
[ -n "$body" ] || bad "nothing served at 127.0.0.1:$port$base"
case "$body" in
  *"<title>Workshop</title>"*) bad "the app's own base path returns the Workshop shell" ;;
esac

# 4. Its entry module is real JavaScript. A dev server that SPA-falls-back
#    returns index.html with content-type text/html, which the browser refuses to
#    execute as a module — a blank page with no network error to show for it.
entry="$(printf '%s' "$body" | grep -oE 'src="[^"]*(main|index)[^"]*\.(ts|js|mjs)"' | head -1 | sed 's/^src="//; s/"$//')"
if [ -n "$entry" ]; then
  # Entries are document-relative (./assets/x.js) so the app can be framed under more
  # than one prefix; resolve them against the base rather than against the host root.
  case "$entry" in
    /*) url="http://127.0.0.1:$port$entry" ;;
    *)  url="http://127.0.0.1:$port$base${entry#./}" ;;
  esac
  ct="$(curl -s -m 8 -o /dev/null -w '%{content_type}' "$url" || true)"
  case "$ct" in
    *javascript*) note "entry: $entry ($ct)" ;;
    *) bad "entry module $url served as '$ct', not javascript" ;;
  esac
else
  note "entry: none found in HTML (not a module app?)"
fi

# 5. The edge RELAYS the proxy prefix. The edge proxies /api/* and serves the SPA
#    for everything else; until the /base-apps route existed, the operator's frame
#    silently loaded Workshop inside itself — a blank page with two headers, and
#    nothing in any log to say so. 401 here is a PASS: it means the request
#    reached the backend and was refused for want of this shell's cookie.
if [ -n "$origin" ]; then
  edge="$(curl -sk -m 8 "$origin$base" || true)"
  code="$(curl -sk -m 8 -o /dev/null -w '%{http_code}' "$origin$base" || true)"
  case "$edge" in
    *"<title>Workshop</title>"*) bad "edge serves the Workshop SPA at $base — the frame will render Workshop inside itself" ;;
    *) note "edge: $code (401 = relayed and auth-gated, which is correct)" ;;
  esac
else
  note "edge: skipped (WORKSHOP_PUBLIC_ORIGIN unset)"
fi

if [ "$fail" -eq 0 ]; then
  echo "OK: $slug/$svc serves current code through the operator's chain"
else
  echo "BROKEN: the operator will NOT see your change. Fix the above."
fi
exit "$fail"
