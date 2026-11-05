#!/usr/bin/env bash
#
# macos-build.sh — build Workshop for Mac (macos/) on the Mac over SSH and
# install it at /Users/Shared/Workshop/Workshop.app. Run on the server, from
# any checkout. No Apple ID: the app is signed with a self-signed identity in
# the build account (scripts/lib/macos-sign-mac.sh) and, built on the Mac
# itself, carries no quarantine flag.
#
# Only committed HEAD is built: macos/, the sources it bundles and the scripts
# run on the Mac are exported from HEAD, so another session's uncommitted
# edits in a shared working tree never ship.
#
# A running copy notices the new build and offers to relaunch.
#
# From ~/.config/workshop/instance.env (shared with mobile-sidestore.sh):
#   WORKSHOP_MOBILE_MAC         ssh target, e.g. builder@100.x.y.z
#   WORKSHOP_MOBILE_MAC_KEY     private key path (default ~/.ssh/workshop_mac_build)
#   WORKSHOP_PUBLIC_ORIGIN      the Workshop address the app opens
#   WORKSHOP_MACOS_BUNDLE       optional bundle id (default WORKSHOP_MOBILE_BUNDLE.mac)
#
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/instance.sh"
require_instance WORKSHOP_MOBILE_MAC
require_instance WORKSHOP_PUBLIC_ORIGIN

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAC="$WORKSHOP_MOBILE_MAC"
KEY="${WORKSHOP_MOBILE_MAC_KEY:-$HOME/.ssh/workshop_mac_build}"
NAME="${WORKSHOP_MOBILE_NAME:-Workshop}"
BUNDLE="${WORKSHOP_MACOS_BUNDLE:-${WORKSHOP_MOBILE_BUNDLE:-home.workshop}.mac}"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=10 "$MAC")
RS=(rsync -az --delete -e "ssh -i $KEY -o BatchMode=yes")
GEN="$REPO/macos/.build/gen"

mkdir -p "$REPO/tmp"
SRC="$(mktemp -d "$REPO/tmp/macos-build.XXXXXX")"
trap 'rm -rf "$SRC"' EXIT
HEAD_SHA="$(git -C "$REPO" rev-parse --short HEAD)"
echo "==> Exporting HEAD $HEAD_SHA"
git -C "$REPO" archive HEAD macos mobile/native mobile/modules/workshop-pages/ios/core shared \
  scripts/lib/macos-sign-mac.sh scripts/lib/macos-build-mac.sh | tar -x -C "$SRC"
DIRTY="$(git -C "$REPO" status --porcelain -- macos mobile/native mobile/modules/workshop-pages/ios/core \
  shared scripts/lib/macos-sign-mac.sh scripts/lib/macos-build-mac.sh)"
[ -z "$DIRTY" ] || printf '==> Not in this build (uncommitted):\n%s\n' "$DIRTY"

echo "==> Bridge client"
mkdir -p "$GEN"
"$REPO/node_modules/.bin/esbuild" "$SRC/macos/bridge/client.ts" --bundle --platform=node \
  --tsconfig="$SRC/macos/bridge/tsconfig.json" --log-level=warning --outfile="$GEN/client.cjs"
node "$GEN/client.cjs" > "$GEN/bridge.js"
ICON="$REPO/frontend/public/sidestore/icon.png"
[ -f "$ICON" ] && cp "$ICON" "$GEN/icon.png"

echo "==> Syncing macos/ and the shared page views to $MAC"
"${SSH[@]}" "mkdir -p workshop/macos"
"${RS[@]}" --exclude /.build/ --exclude /build-out/ --exclude /Sources/Workshop/PageCore/ \
  "$SRC/macos/" "$MAC:workshop/macos/"
# The page views' platform-neutral Swift, shared with the iOS module.
"${RS[@]}" "$SRC/mobile/modules/workshop-pages/ios/core/" "$MAC:workshop/macos/Sources/Workshop/PageCore/"
"${RS[@]}" "$GEN/" "$MAC:workshop/macos/gen/"

BUILD="$(date -u +%Y%m%d%H%M)"
echo "==> Building $NAME ($BUNDLE) build $BUILD from $HEAD_SHA on the Mac"
cat "$SRC/scripts/lib/macos-sign-mac.sh" "$SRC/scripts/lib/macos-build-mac.sh" \
  | "${SSH[@]}" bash -s -- "$BUILD" "$NAME" "$BUNDLE" "$WORKSHOP_PUBLIC_ORIGIN"
