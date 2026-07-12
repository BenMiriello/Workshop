#!/usr/bin/env bash
#
# mobile-deploy.sh — pull latest and (re)build the Workshop iOS app onto the
# attached iPhone. Run ON THE MAC, from your terminal (so nvm's node is on PATH),
# with the phone plugged in, unlocked, and trusted.
#
# NOTE: most Workshop updates DON'T need this. The app is a WebView around
# workshop.benmiriello.com, so backend/frontend changes appear on next app open.
# You only rebuild when the native shell changes (mobile/native/*, app.json,
# native deps, or the app's bundled JS) — or when the free-tier 7-day signing
# expires and the app stops launching.
#
# Usage:
#   scripts/mobile-deploy.sh              # JS/shell update or 7-day re-sign
#   scripts/mobile-deploy.sh --prebuild   # ONLY when a native dep / app.json ios
#                                          # block / plugins[] changed
#
# --prebuild regenerates the native project, which wipes the signing Team. Set
# WORKSHOP_TEAM_ID to your 10-char Apple Team ID (Xcode > Settings > Accounts >
# your team, or `xcrun security find-identity -v -p codesigning`) so the build
# can re-sign non-interactively; otherwise open Xcode once to re-pick the Team.
#
set -euo pipefail

REPO="${WORKSHOP_REPO:-$HOME/Development/Workshop}"
MOBILE="$REPO/mobile"
SCHEME="Workshop"
CONFIG="Release"
BUNDLE_ID="com.benmiriello.workshop"

DO_PREBUILD=0
[ "${1:-}" = "--prebuild" ] && DO_PREBUILD=1

echo "==> Pulling latest ($REPO)"
BEFORE=$(git -C "$REPO" rev-parse HEAD)
git -C "$REPO" pull --rebase
AFTER=$(git -C "$REPO" rev-parse HEAD)

cd "$MOBILE"

# Reinstall JS deps only if they changed (or node_modules is missing). The
# committed lockfile already encodes the expo-font override, so `npm ci` is
# clean and won't dirty the tree. --ignore-scripts skips react-native-screens'
# broken prepare hook (native code still builds via CocoaPods).
if [ ! -d node_modules ] || git -C "$REPO" diff --name-only "$BEFORE" "$AFTER" | grep -q '^mobile/package'; then
  echo "==> Installing JS deps (npm ci)"
  npm ci --ignore-scripts
else
  echo "==> JS deps unchanged, skipping install"
fi

if [ "$DO_PREBUILD" = "1" ]; then
  echo "==> Prebuilding native project (--prebuild)"
  rm -rf ios
  npx expo prebuild -p ios --clean
  ( cd ios && pod install )
fi

# The Xcode bundler build phase needs an absolute node path (nvm's node isn't on
# Xcode's PATH). Recreate the local override if prebuild wiped ios/ or it's absent.
if [ ! -f ios/.xcode.env.local ]; then
  echo "==> Writing ios/.xcode.env.local (NODE_BINARY)"
  echo "export NODE_BINARY=$(command -v node)" > ios/.xcode.env.local
fi

echo "==> Locating attached iPhone"
UDID=$(xcrun xctrace list devices 2>&1 \
  | grep -v Simulator \
  | grep -Eo '[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}' \
  | head -1)
if [ -z "$UDID" ]; then
  echo "!! No attached iPhone found. Plug it in, unlock it, tap Trust, retry." >&2
  exit 1
fi
echo "    device UDID: $UDID"

# Pass the Team only if provided (needed after a --prebuild reset; otherwise the
# existing signed project already has it).
TEAM_ARGS=()
if [ -n "${WORKSHOP_TEAM_ID:-}" ]; then
  TEAM_ARGS=(DEVELOPMENT_TEAM="$WORKSHOP_TEAM_ID" CODE_SIGN_STYLE=Automatic)
fi

echo "==> Building ($CONFIG) — this re-signs, refreshing the 7-day profile"
xcrun xcodebuild \
  -workspace ios/Workshop.xcworkspace \
  -scheme "$SCHEME" \
  -configuration "$CONFIG" \
  -destination "id=$UDID" \
  -derivedDataPath ios/build \
  -allowProvisioningUpdates \
  "${TEAM_ARGS[@]}" \
  build

APP="ios/build/Build/Products/$CONFIG-iphoneos/$SCHEME.app"
if [ ! -d "$APP" ]; then
  echo "!! Build succeeded but $APP not found; check derivedDataPath." >&2
  exit 1
fi

echo "==> Installing to device"
xcrun devicectl device install app --device "$UDID" "$APP"
echo "==> Launching"
xcrun devicectl device process launch --device "$UDID" "$BUNDLE_ID" || \
  echo "   (launch skipped — open the app from the home screen)"

echo "==> Done."
echo "   If iOS says 'Untrusted Developer', trust it once under Settings >"
echo "   General > VPN & Device Management > [your Apple ID], then reopen the app."
