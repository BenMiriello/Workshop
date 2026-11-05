#!/usr/bin/env bash
# Install the public-edge + cloudflared systemd --user units and add this
# instance's ingress rule to the cloudflared config.
#
# Idempotent. Does NOT create the DNS record (that hits the Cloudflare API, which
# the egress guard blocks from an agent shell) and does NOT start cloudflared —
# the operator runs the one DNS command, then enables the tunnel. See
# docs/auth-redesign/02-cloudflare-deployment.md.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_ROOT/scripts/lib/instance.sh"
require_instance WORKSHOP_PUBLIC_HOST "the hostname this instance serves"
require_instance WORKSHOP_TUNNEL_ID "tunnel id, from `cloudflared tunnel list`"

UNIT_DIR="$HOME/.config/systemd/user"
CF_CONFIG="$HOME/.cloudflared/config.yml"
PUBLIC_HOST="$WORKSHOP_PUBLIC_HOST"
TUNNEL_ID="$WORKSHOP_TUNNEL_ID"
EDGE_PORT="${WORKSHOP_EDGE_PORT:-8002}"
mkdir -p "$UNIT_DIR"

for unit in workshop-public.service cloudflared.service; do
  sed -e "s#@WORKSHOP_ROOT@#$REPO_ROOT#g" \
      -e "s#@WORKSHOP_BACKEND_PORT@#${WORKSHOP_BACKEND_PORT:-8000}#g" \
      -e "s#@WORKSHOP_EDGE_PORT@#$EDGE_PORT#g" \
      "$REPO_ROOT/infra/systemd/$unit" > "$UNIT_DIR/$unit"
  echo "installed $UNIT_DIR/$unit"
done

# Add the workshop ingress rule above the catch-all, if not already present.
if [ -f "$CF_CONFIG" ] && ! grep -q "$PUBLIC_HOST" "$CF_CONFIG"; then
  cp "$CF_CONFIG" "$CF_CONFIG.bak"
  # Insert the rule before the first catch-all (http_status:404) line.
  awk -v host="$PUBLIC_HOST" -v port="$EDGE_PORT" '
    /service: http_status:404/ && !done {
      print "  - hostname: " host
      print "    service: http://127.0.0.1:" port
      done=1
    }
    { print }
  ' "$CF_CONFIG.bak" > "$CF_CONFIG"
  echo "added $PUBLIC_HOST -> :$EDGE_PORT ingress to $CF_CONFIG (backup at $CF_CONFIG.bak)"
else
  echo "cloudflared ingress already has $PUBLIC_HOST (or config missing) — skipped"
fi

systemctl --user daemon-reload
echo
echo "Installed. To go live (operator steps):"
echo "  1. cloudflared tunnel route dns $TUNNEL_ID $PUBLIC_HOST"
echo "  2. systemctl --user enable --now workshop-public.service cloudflared.service"
echo "  3. (optional) set WORKSHOP_REGISTRATION_OPEN=0 in ~/.config/workshop/secrets.env to close public signups,"
echo "     then: systemctl --user restart workshop-backend.service"
