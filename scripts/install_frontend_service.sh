#!/usr/bin/env bash
# Install (or re-install) the Workshop Vite dev server as a systemd --user unit.
#
# This unit was running on the original box for months with no template in the
# repo, so no clone could reproduce it while CLAUDE.md instructed agents to
# restart it. Templating it is the fix.
#
# Idempotent. Substitutes this checkout's path and this machine's node bin dir
# into the template, then enables and starts it.
#
# Two tokens, not one: the node path matters here in a way it does not for the
# backend. npm has to be on PATH for the unit, and a systemd --user service does
# not read your shell profile, so an nvm-managed node is invisible unless its
# directory is written into the unit.
set -euo pipefail

SERVICE=workshop-frontend.service
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/infra/systemd/$SERVICE"
UNIT_DIR="$HOME/.config/systemd/user"
DEST="$UNIT_DIR/$SERVICE"

if [ "$(id -u)" = "0" ]; then
  echo "Refusing to run as root. Run as the user that should own the service." >&2
  exit 1
fi
[ -f "$TEMPLATE" ] || { echo "Missing unit template: $TEMPLATE" >&2; exit 1; }

NPM_BIN="$(command -v npm || true)"
if [ -z "$NPM_BIN" ]; then
  echo "npm not found on PATH. Install node, then re-run." >&2
  exit 1
fi
NODE_BIN="$(cd "$(dirname "$NPM_BIN")" && pwd)"

mkdir -p "$UNIT_DIR"
sed -e "s#@WORKSHOP_ROOT@#$REPO_ROOT#g" -e "s#@NODE_BIN@#$NODE_BIN#g" "$TEMPLATE" > "$DEST"
echo "installed $DEST (root=$REPO_ROOT, node=$NODE_BIN)"

systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE"

# Give vite a moment, then say plainly whether it is actually serving. A unit
# that is "active" while vite has already exited is the failure this catches.
for _ in $(seq 1 15); do
  if curl -fsS -o /dev/null --max-time 2 http://127.0.0.1:5173/ 2>/dev/null; then
    echo "vite is serving on 127.0.0.1:5173"
    exit 0
  fi
  sleep 1
done
echo "WARNING: the unit is installed but nothing answered on 127.0.0.1:5173 within 15s." >&2
echo "         systemctl --user status $SERVICE   journalctl --user -u $SERVICE -n 50" >&2
exit 1
