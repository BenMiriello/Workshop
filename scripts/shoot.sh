#!/usr/bin/env bash
# Screenshot a page of the running app. See scripts/shoot.mjs for why.
#
#   bash scripts/shoot.sh --setup                  once per machine
#   bash scripts/shoot.sh /memory                  phone (390x800), the default
#   bash scripts/shoot.sh /memory 1440 900         desktop
#   bash scripts/shoot.sh /apps/birds/files 390 800 scratch/files.png
#
# Everything it installs lives under scratch/, which is gitignored.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BROWSER_DIR="$ROOT/scratch/browser"

if [ "${1:-}" = "--setup" ]; then
  mkdir -p "$BROWSER_DIR"
  # playwright-core 1.49 is the last line that runs on Node 18, which is what
  # this box has. Newer releases hard-refuse with "requires Node.js 20 or higher".
  [ -f "$BROWSER_DIR/package.json" ] || echo '{"name":"ws-browser","private":true,"version":"1.0.0"}' > "$BROWSER_DIR/package.json"
  ( cd "$BROWSER_DIR" && npm i playwright-core@1.49.1 --no-audit --no-fund >/dev/null )
  ( cd "$BROWSER_DIR" && node node_modules/playwright-core/cli.js install chromium )
  if [ ! -s "$ROOT/scratch/.tok" ]; then
    ( cd "$ROOT/backend" && ./.venv/bin/python3 -c "
import sys; sys.path.insert(0,'.')
from services import auth_store
print(auth_store.create_session(6, user_agent='agent-screenshot'))" ) > "$ROOT/scratch/.tok"
    chmod 600 "$ROOT/scratch/.tok"
  fi
  echo "shoot: ready"
  exit 0
fi

if [ ! -d "$BROWSER_DIR/node_modules/playwright-core" ]; then
  echo "shoot: not set up yet -- run: bash scripts/shoot.sh --setup" >&2
  exit 2
fi
cd "$BROWSER_DIR"
exec node "$ROOT/scripts/shoot.mjs" "$@"
