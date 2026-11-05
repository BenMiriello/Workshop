#!/usr/bin/env bash
#
# mobile-sidestore.sh — build the Workshop iOS app on a Mac over SSH, unsigned,
# and publish it as a SideStore (AltStore-format) source that the phone
# installs and updates from. Run on the server, from any checkout.
#
#   scripts/mobile-sidestore.sh            # build if mobile/ changed, publish
#   scripts/mobile-sidestore.sh --prebuild # regenerate the native project first
#
# SideStore signs the app on the phone with the owner's own Apple ID, so the
# Mac needs no signing setup: an account with Xcode, Node and CocoaPods on
# /opt/homebrew/bin, reachable with a key. The source is written to
# frontend/public/sidestore/ (keep it out of git) and into the served dist,
# so it is live at <origin>/sidestore/source.json with no rebuild.
#
# From ~/.config/workshop/instance.env:
#   WORKSHOP_MOBILE_MAC         ssh target, e.g. builder@100.x.y.z
#   WORKSHOP_MOBILE_MAC_KEY     private key path (default ~/.ssh/workshop_mac_build)
#   WORKSHOP_MOBILE_BUNDLE      iOS bundle id
#   WORKSHOP_PUBLIC_ORIGIN      the origin the app loads and the source lives on
#   WORKSHOP_MOBILE_LAN_ORIGIN  optional: an origin on the home network serving
#                               the same folder (scripts/sidestore-lan-serve.py);
#                               also writes source-home.json listing it, so
#                               SideStore can update while LocalDevVPN has
#                               taken the phone off the tailnet
#
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/instance.sh"
require_instance WORKSHOP_MOBILE_MAC
require_instance WORKSHOP_MOBILE_BUNDLE
require_instance WORKSHOP_PUBLIC_ORIGIN

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAC="$WORKSHOP_MOBILE_MAC"
KEY="${WORKSHOP_MOBILE_MAC_KEY:-$HOME/.ssh/workshop_mac_build}"
NAME="${WORKSHOP_MOBILE_NAME:-Workshop}"
OUT="$REPO/frontend/public/sidestore"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=10 "$MAC")
PREBUILD=0
[ "${1:-}" = "--prebuild" ] && PREBUILD=1

echo "==> Syncing mobile/, shared/ and packages/ui to $MAC"
"${SSH[@]}" "mkdir -p workshop"
RS=(rsync -az --delete -e "ssh -i $KEY -o BatchMode=yes")
"${RS[@]}" --exclude /node_modules --exclude /ios --exclude /.env.local --exclude /build-out \
  "$REPO/mobile/" "$MAC:workshop/mobile/"
"${RS[@]}" "$REPO/shared/" "$MAC:workshop/shared/"
"${RS[@]}" --exclude node_modules "$REPO/packages/ui/" "$MAC:workshop/packages/ui/"

BUILD="$(date -u +%Y%m%d%H%M)"
echo "==> Building $NAME ($WORKSHOP_MOBILE_BUNDLE) build $BUILD on the Mac"
"${SSH[@]}" bash -s -- "$PREBUILD" "$BUILD" "$NAME" "$WORKSHOP_MOBILE_BUNDLE" \
  "$WORKSHOP_PUBLIC_ORIGIN" < "$REPO/scripts/lib/mobile-sidestore-mac.sh"

mkdir -p "$OUT"
"${RS[@]}" "$MAC:workshop/mobile/build-out/Workshop.ipa" "$OUT/workshop.ipa"
"${RS[@]}" "$MAC:workshop/mobile/build-out/Info.plist" "$OUT/.Info.plist"
"${RS[@]}" "$MAC:workshop/mobile/build-out/entitlements.plist" "$OUT/.entitlements.plist"

python3 "$REPO/scripts/lib/sidestore_source.py" "$OUT" "$NAME" \
  "$WORKSHOP_PUBLIC_ORIGIN/sidestore"

if [ -n "${WORKSHOP_MOBILE_LAN_ORIGIN:-}" ]; then
  python3 "$REPO/scripts/lib/sidestore_source.py" "$OUT" "$NAME" \
    "$WORKSHOP_MOBILE_LAN_ORIGIN" source-home.json
fi

DIST="$REPO/frontend/dist/sidestore"
mkdir -p "$DIST"
cp "$OUT/workshop.ipa" "$OUT"/source*.json "$OUT/icon.png" "$DIST/"
echo "==> Published: $WORKSHOP_PUBLIC_ORIGIN/sidestore/source.json"
if [ -n "${WORKSHOP_MOBILE_LAN_ORIGIN:-}" ]; then
  echo "==> At home: $WORKSHOP_MOBILE_LAN_ORIGIN/source-home.json"
fi
