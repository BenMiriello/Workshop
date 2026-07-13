#!/usr/bin/env bash
#
# mobile-build.sh — build + install a Workshop iOS app VARIANT onto the attached
# iPhone. Two variants coexist on one device (distinct bundle ids), same source.
# Run on the Mac, phone plugged in + unlocked, nvm node active.
#
#   scripts/mobile-build.sh workshop2   # the simonsays instance (tailnet :8447)
#   scripts/mobile-build.sh workshop    # our main app (workshop.benmiriello.com)
#
# Each run: sets the variant identity, writes .env.local, does a clean prebuild
# (identity is stamped at prebuild time), writes the Release Node path, then
# either builds+installs headlessly (if WORKSHOP_TEAM_ID is set) or opens Xcode
# for you to set Team + Release + Run. The variant flip is why building both from
# one folder needs this — it manages the env + workspace name for you.
#
# One-time, to build fully headless (no Xcode clicking): set your 10-char Apple
# Team id, e.g. in ~/.zshrc:  export WORKSHOP_TEAM_ID=XXXXXXXXXX
# Find it with:  xcrun security find-identity -v -p codesigning   (the (TEAMID)).
#
set -euo pipefail

VARIANT="${1:-}"
case "$VARIANT" in
  workshop|main|1)
    APP_NAME="Workshop"; APP_SLUG="workshop-mobile"; APP_SCHEME="workshop"
    BUNDLE_ID="com.benmiriello.workshop"
    SERVER_URL="https://workshop.benmiriello.com" ;;
  workshop2|simonsays|2)
    APP_NAME="workshop2"; APP_SLUG="workshop2"; APP_SCHEME="workshop2"
    BUNDLE_ID="com.benmiriello.workshop2"
    SERVER_URL="https://debian.tail38e62e.ts.net:8447" ;;
  *)
    echo "usage: $0 {workshop|workshop2}" >&2; exit 2 ;;
esac

REPO="${WORKSHOP_REPO:-$HOME/Development/Workshop}"
MOBILE="$REPO/mobile"
cd "$MOBILE"

echo "==> Variant '$VARIANT': $APP_NAME  ($BUNDLE_ID)  ->  $SERVER_URL"

# 0. Free-tier push fix: a free Apple Personal Team can't sign the push
#    entitlement, and expo-notifications' config plugin re-adds it every prebuild.
#    Strip it from app.json if present (idempotent; no-op on a clean checkout).
if grep -q '"expo-notifications"' app.json 2>/dev/null; then
  echo "==> Removing expo-notifications config plugin from app.json (push fix)"
  sed -i '' '/"expo-notifications"/d' app.json
fi

# 1. Deps (only if missing; fast otherwise).
[ -d node_modules ] || npm install --ignore-scripts

# 2. Per-variant env: .env.local carries the bundle-time origin; the exports feed
#    app.config.js at prebuild time so the native project gets this identity.
cat > .env.local <<EOF
EXPO_PUBLIC_WORKSHOP_URL=$SERVER_URL
WORKSHOP_APP_NAME=$APP_NAME
WORKSHOP_APP_SLUG=$APP_SLUG
WORKSHOP_APP_SCHEME=$APP_SCHEME
WORKSHOP_IOS_BUNDLE_ID=$BUNDLE_ID
EOF
export EXPO_PUBLIC_WORKSHOP_URL="$SERVER_URL"
export WORKSHOP_APP_NAME="$APP_NAME" WORKSHOP_APP_SLUG="$APP_SLUG"
export WORKSHOP_APP_SCHEME="$APP_SCHEME" WORKSHOP_IOS_BUNDLE_ID="$BUNDLE_ID"

# 3. Regenerate the native project with this identity.
rm -rf ios
npx expo prebuild -p ios --clean

# 4. Node path for the Release JS bundler (nvm node isn't on Xcode's build PATH).
echo "export NODE_BINARY=$(command -v node)" > ios/.xcode.env.local

# 5. Resolve the generated workspace + scheme (their names track the app name).
WS=$(ls -d ios/*.xcworkspace | head -1)
SCHEME=$(basename "$WS" .xcworkspace)
BUILT_ID=$(grep -m1 PRODUCT_BUNDLE_IDENTIFIER ios/*.xcodeproj/project.pbxproj | grep -o 'com\.[A-Za-z0-9._]*' || true)
echo "==> Prebuilt $WS (scheme $SCHEME); bundle id in project: ${BUILT_ID:-?} (want $BUNDLE_ID)"
if [ "${BUILT_ID:-}" != "$BUNDLE_ID" ]; then
  echo "!! Bundle id mismatch — the identity env didn't apply. Stop and check." >&2
  exit 1
fi

# 6. Build + install (headless if a Team id is set, else hand off to Xcode).
if [ -n "${WORKSHOP_TEAM_ID:-}" ]; then
  UDID=$(xcrun xctrace list devices 2>&1 | grep -v Simulator \
    | grep -Eo '[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}' | head -1)
  [ -z "$UDID" ] && { echo "!! No attached iPhone found." >&2; exit 1; }
  echo "==> Building Release + installing to $UDID"
  xcrun xcodebuild -workspace "$WS" -scheme "$SCHEME" -configuration Release \
    -destination "id=$UDID" -derivedDataPath ios/build -allowProvisioningUpdates \
    DEVELOPMENT_TEAM="$WORKSHOP_TEAM_ID" CODE_SIGN_STYLE=Automatic build
  APP=$(ls -d ios/build/Build/Products/Release-iphoneos/*.app | head -1)
  xcrun devicectl device install app --device "$UDID" "$APP"
  echo "==> Installed '$APP_NAME'. If iOS says 'Untrusted Developer', trust it under"
  echo "   Settings > General > VPN & Device Management, then open the app."
else
  echo "==> No WORKSHOP_TEAM_ID set — opening Xcode. Then:"
  echo "    1) $SCHEME target > Signing & Capabilities > Team = your Apple ID"
  echo "    2) Product > Scheme > Edit Scheme > Run > Build Configuration = Release"
  echo "    3) pick your iPhone > Run"
  open "$WS"
fi
