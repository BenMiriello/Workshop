#!/usr/bin/env bash
# THE COMPOSER EMPTIES WHEN YOU SEND. See scripts/smoke/composer_clear.mjs for
# why this exists as a browser check and not as a unit test.
#
#   bash scripts/smoke_chat.sh          no model turn, no chat data written
#   bash scripts/smoke_chat.sh --live   also sends one real message
#
# Exit 0 pass, 1 fail, 3 could not run. "Could not run" is never reported as a
# pass: a green that means "I checked nothing" is what let this bug survive five
# fixes.
set -uo pipefail
cd "$(dirname "$0")/.."

NODE=""
for c in "$HOME/.nvm/versions/node/v22.22.3/bin/node" "$(command -v node || true)"; do
  [ -x "$c" ] || continue
  v=$("$c" -e 'process.stdout.write(String(process.versions.node.split(".")[0]))' 2>/dev/null || echo 0)
  if [ "${v:-0}" -ge 20 ]; then NODE="$c"; break; fi
done
[ -n "$NODE" ] || { echo "smoke: needs node >= 20 (playwright-core does not run on 18)"; exit 3; }

CHROME=$(ls -d "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux*/chrome 2>/dev/null | head -1)
[ -x "${CHROME:-}" ] || { echo "smoke: no chromium under ~/.cache/ms-playwright"; exit 3; }

ORIGIN="${SMOKE_ORIGIN:-http://127.0.0.1:8002}"
curl -sf -o /dev/null "$ORIGIN/" || { echo "smoke: nothing serving the app at $ORIGIN"; exit 3; }

# A session for the operator, minted locally. The app is cookie-authenticated
# and there is no test-only bypass, which is correct; this is the local owner
# asking for a session on his own machine.
TOKEN=$(cd backend && .venv/bin/python -c "
import sys; sys.path.insert(0, '.')
from services import auth_store
u = auth_store.get_by_email('${SMOKE_EMAIL:-benmiriello@gmail.com}')
print(auth_store.create_session(u['id'], user_agent='smoke') if u else '')
" 2>/dev/null | tail -1)
[ -n "$TOKEN" ] || { echo "smoke: could not mint a session"; exit 3; }

SMOKE_ORIGIN="$ORIGIN" SMOKE_TOKEN="$TOKEN" SMOKE_CHROME="$CHROME" \
  "$NODE" scripts/smoke/composer_clear.mjs "$@"
