# Runs ON THE MAC, fed over ssh by scripts/mobile-sidestore.sh.
# Args: prebuild(0|1) build-number app-name bundle-id server-url
set -euo pipefail
PREBUILD="$1" BUILD="$2" NAME="$3" BUNDLE="$4" SERVER="$5"
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export LANG=en_US.UTF-8
cd ~/workshop/mobile

cat > .env.local <<EOF
EXPO_PUBLIC_WORKSHOP_URL=$SERVER
WORKSHOP_APP_NAME=$NAME
WORKSHOP_IOS_BUNDLE_ID=$BUNDLE
EOF
export EXPO_PUBLIC_WORKSHOP_URL="$SERVER" WORKSHOP_APP_NAME="$NAME"
export WORKSHOP_IOS_BUNDLE_ID="$BUNDLE"

STAMP=$(cat package.json package-lock.json app.json app.config.js \
  $(find modules plugins -type f 2>/dev/null | sort) | shasum | cut -c1-16)
if [ ! -d node_modules ] || [ "$(cat .deps-stamp 2>/dev/null)" != "$STAMP" ]; then
  echo "==> npm install"
  npm install --ignore-scripts --no-audit --no-fund
fi
if [ "$PREBUILD" = 1 ] || [ ! -d ios ] || [ "$(cat .deps-stamp 2>/dev/null)" != "$STAMP" ]; then
  echo "==> expo prebuild"
  rm -rf ios
  CI=1 npx expo prebuild -p ios --clean
fi
echo "$STAMP" > .deps-stamp
echo "export NODE_BINARY=$(command -v node)" > ios/.xcode.env.local

WS=$(ls -d ios/*.xcworkspace | head -1)
SCHEME=$(basename "$WS" .xcworkspace)
echo "==> xcodebuild $SCHEME (Release, unsigned)"
xcodebuild -workspace "$WS" -scheme "$SCHEME" -configuration Release \
  -sdk iphoneos -destination generic/platform=iOS -derivedDataPath ios/build \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" \
  -quiet build

APP=$(ls -d ios/build/Build/Products/Release-iphoneos/*.app | head -1)
# SideStore offers an update only when the version string changes, so each
# build gets its own: major.date.time from the build stamp. Extensions carry
# the app's.
MAJOR=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$APP/Info.plist" | cut -d. -f1)
for PLIST in "$APP/Info.plist" "$APP"/PlugIns/*.appex/Info.plist; do
  [ -f "$PLIST" ] || continue
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD" "$PLIST"
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $MAJOR.${BUILD:0:8}.$((10#${BUILD:8:4}))" "$PLIST"
done
rm -rf build-out && mkdir -p build-out/Payload
cp -R "$APP" build-out/Payload/
OUT_APP=$(ls -d build-out/Payload/*.app)
# An ad hoc signature (no identity): Mac sideloaders such as PlayCover refuse
# an app with none, and SideStore replaces it with the owner's on install.
# Each bundle is signed with its target's entitlements file (ios/<name>/
# <name>.entitlements), extensions before the app: SideStore registers the
# app groups a signature names. The source must list every entitlement of the
# app and its extensions, or SideStore refuses the install.
printf '<?xml version="1.0"?><plist version="1.0"><dict/></plist>' > build-out/entitlements.plist
codesign --force --deep --sign - --timestamp=none "$OUT_APP"
sign_with_entitlements() {
  local bundle="$1" file="$2"
  [ -f "$file" ] || return 0
  codesign --force --sign - --timestamp=none --entitlements "$file" "$bundle"
  /usr/libexec/PlistBuddy -c "Merge $file" build-out/entitlements.plist >/dev/null
}
for EX in "$OUT_APP"/PlugIns/*.appex; do
  [ -d "$EX" ] || continue
  EXNAME=$(basename "$EX" .appex)
  sign_with_entitlements "$EX" "ios/$EXNAME/$EXNAME.entitlements"
done
sign_with_entitlements "$OUT_APP" "ios/$SCHEME/$SCHEME.entitlements"
cp "$APP/Info.plist" build-out/Info.plist
(cd build-out && zip -qry Workshop.ipa Payload)
echo "==> Built $(du -h build-out/Workshop.ipa | cut -f1) ipa"
