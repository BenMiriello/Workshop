#!/usr/bin/env bash
# Install the public-edge + cloudflared systemd --user units and add the
# workshop.benmiriello.com ingress rule to the cloudflared config.
#
# Idempotent. Does NOT create the DNS record (that hits the Cloudflare API, which
# the egress guard blocks from an agent shell) and does NOT start cloudflared —
# the operator runs the one DNS command, then enables the tunnel. See
# docs/auth-redesign/02-cloudflare-deployment.md.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
CF_CONFIG="$HOME/.cloudflared/config.yml"
TUNNEL_ID="eca1421d-9e82-4bbd-a131-3960065cf953"
mkdir -p "$UNIT_DIR"

for unit in workshop-public.service cloudflared.service; do
  sed "s#@WORKSHOP_ROOT@#$REPO_ROOT#g" "$REPO_ROOT/infra/systemd/$unit" > "$UNIT_DIR/$unit"
  echo "installed $UNIT_DIR/$unit"
done

# Add the workshop ingress rule above the catch-all, if not already present.
if [ -f "$CF_CONFIG" ] && ! grep -q "workshop.benmiriello.com" "$CF_CONFIG"; then
  cp "$CF_CONFIG" "$CF_CONFIG.bak"
  # Insert the rule before the first catch-all (http_status:404) line.
  awk '
    /service: http_status:404/ && !done {
      print "  - hostname: workshop.benmiriello.com"
      print "    service: http://127.0.0.1:8002"
      done=1
    }
    { print }
  ' "$CF_CONFIG.bak" > "$CF_CONFIG"
  echo "added workshop.benmiriello.com -> :8002 ingress to $CF_CONFIG (backup at $CF_CONFIG.bak)"
else
  echo "cloudflared ingress already has workshop.benmiriello.com (or config missing) — skipped"
fi

systemctl --user daemon-reload
echo
echo "Installed. To go live (operator steps):"
echo "  1. cloudflared tunnel route dns $TUNNEL_ID workshop.benmiriello.com"
echo "  2. systemctl --user enable --now workshop-public.service cloudflared.service"
echo "  3. (optional) set WORKSHOP_REGISTRATION_OPEN=0 in ~/.config/workshop/secrets.env to close public signups,"
echo "     then: systemctl --user restart workshop-backend.service"
