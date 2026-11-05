# Runs ON THE MAC, fed over ssh by scripts/macos-build.sh after
# macos-sign-mac.sh, which defines sign_app.
# Args: build-number app-name bundle-id workshop-url
set -euo pipefail
BUILD="$1" NAME="$2" BUNDLE="$3" URL="$4"
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
cd ~/workshop/macos

echo "==> Page namespace check"
mkdir -p .build
CORE=Sources/Workshop/PageCore
swiftc -o .build/page-namespace-check checks/page-namespace/main.swift \
  "$CORE/PageApps.swift" "$CORE/PageError.swift" "$CORE/PageProfiles.swift"
.build/page-namespace-check

echo "==> Page commands check"
swiftc -o .build/page-commands-check checks/page-commands/main.swift Sources/Workshop/PageCommandRoute.swift
.build/page-commands-check

echo "==> Page navigation check"
swiftc -o .build/page-navigation-check checks/page-navigation/main.swift "$CORE/PageError.swift" \
  "$CORE/PageSites.swift" "$CORE/PageHops.swift" "$CORE/PageStrip.swift" "$CORE/PageNavigationRules.swift" \
  "$CORE/PageDownload.swift"
.build/page-navigation-check

echo "==> Page zoom check"
swiftc -o .build/page-zoom-check checks/page-zoom/main.swift "$CORE/PageError.swift" "$CORE/PageZoom.swift"
.build/page-zoom-check

echo "==> Page audio check"
swiftc -o .build/page-audio-check checks/page-audio/main.swift "$CORE/PageAudio.swift"
.build/page-audio-check

echo "==> Page hold check"
swiftc -o .build/page-hold-check checks/page-hold/main.swift "$CORE/PageAudio.swift" "$CORE/PageShowing.swift"
.build/page-hold-check

echo "==> Page leave check"
swiftc -o .build/page-leave-check checks/page-leave/main.swift "$CORE/PageAudio.swift" "$CORE/PageLeave.swift" \
  "$CORE/PageFailure.swift"
for c in rules replace push pop load failure; do .build/page-leave-check "$c"; done

echo "==> Page holes check"
swiftc -o .build/page-holes-check checks/page-holes/main.swift "$CORE/PageHoles.swift"
.build/page-holes-check

echo "==> Page under check"
swiftc -o .build/page-under-check checks/page-under/main.swift Sources/Workshop/ShellWebView.swift
.build/page-under-check

echo "==> Quiet window check"
clang -dynamiclib -o .build/sound-hook.dylib checks/quiet-window/hook.c -framework AudioToolbox
swiftc -o .build/quiet-window-check checks/quiet-window/main.swift Sources/Workshop/QuietWindow.swift
DYLD_INSERT_LIBRARIES="$PWD/.build/sound-hook.dylib" .build/quiet-window-check

echo "==> swift build"
swift build -c release --product Workshop
BIN="$(swift build -c release --show-bin-path)/Workshop"

APP="build-out/$NAME.app"
rm -rf build-out && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Workshop"
cp gen/bridge.js "$APP/Contents/Resources/bridge.js"
clang -dynamiclib -fobjc-arc -o "$APP/Contents/Resources/alert-log.dylib" diagnostics/alert-log.m \
  diagnostics/crash-reports.m -framework AppKit -framework AudioToolbox
ICON_KEY=""
if [ -f gen/icon.png ]; then
  SET=build-out/AppIcon.iconset
  mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z $s $s gen/icon.png --out "$SET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s * 2)) $((s * 2)) gen/icon.png --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$APP/Contents/Resources/AppIcon.icns"
  ICON_KEY="<key>CFBundleIconFile</key><string>AppIcon</string>"
fi

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>$NAME</string>
<key>CFBundleDisplayName</key><string>$NAME</string>
<key>CFBundleIdentifier</key><string>$BUNDLE</string>
<key>CFBundleExecutable</key><string>Workshop</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>$BUILD</string>
<key>CFBundleShortVersionString</key><string>1.${BUILD:0:8}.$((10#${BUILD:8:4}))</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSPrincipalClass</key><string>NSApplication</string>
<key>NSSupportsAutomaticTermination</key><false/>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsArbitraryLoads</key><true/></dict>
<key>NSMicrophoneUsageDescription</key><string>Workshop records audio for apps you have installed that ask for it, and for talking to your assistant in voice mode.</string>
<key>NSDownloadsFolderUsageDescription</key><string>Workshop saves files you choose to save to this Mac in your Downloads folder.</string>
<key>NSCameraUsageDescription</key><string>Workshop uses the camera for pages that ask for it.</string>
<key>LSEnvironment</key><dict><key>DYLD_INSERT_LIBRARIES</key><string>@executable_path/../Resources/alert-log.dylib</string></dict>
<key>WorkshopURL</key><string>$URL</string>
$ICON_KEY
</dict></plist>
EOF
sign_app "$APP"

# A running copy keeps its own bundle until it quits. macOS checks the app's
# signature against its files on disk, and once they are moved or gone
# its web view gets no microphone: dictation went silent the moment a build
# landed under a running app (2026-11-02). So the new build waits beside it as
# .next.app, and a waiter puts it in place once no copy runs. The app offers a
# relaunch when it sees the waiting build, and its Relaunch waits for the swap.
DEST=/Users/Shared/Workshop
LIVE="$DEST/$NAME.app"
mkdir -p "$DEST"
pkill -f "$DEST/.swap.sh" 2>/dev/null || true
rm -rf "$DEST/.next.app"
cp -R "$APP" "$DEST/.next.app"
chmod -R a+rX "$DEST/.next.app"
cat > "$DEST/.swap.sh" <<'SWAP'
#!/bin/bash
# Put .next.app in place of the app once no copy of it runs. A copy found
# running from the old bundle just after it was moved puts it back; a bundle
# a copy still runs from is never deleted.
DEST="$1" NAME="$2"
LIVE="$DEST/$NAME.app" NEXT="$DEST/.next.app" OLD="$DEST/.old.app"
running() { pgrep -f "$1/Contents/MacOS/" >/dev/null; }
while [ -d "$NEXT" ]; do
  if ! running "$LIVE" && ! running "$OLD"; then
    rm -rf "$OLD"
    [ -d "$LIVE" ] && mv "$LIVE" "$OLD"
    if running "$OLD"; then mv "$OLD" "$LIVE"; sleep 0.1; continue; fi
    mv "$NEXT" "$LIVE"
    running "$OLD" || rm -rf "$OLD"
    break
  fi
  sleep 0.1
done
SWAP
if pgrep -f "$LIVE/Contents/MacOS/" >/dev/null; then
  nohup bash "$DEST/.swap.sh" "$DEST" "$NAME" </dev/null >/dev/null 2>&1 &
  echo "==> Staged build $BUILD beside the running $NAME; it goes in when $NAME quits or relaunches"
else
  bash "$DEST/.swap.sh" "$DEST" "$NAME"
  echo "==> Installed $LIVE ($(du -sh "$LIVE" | cut -f1)), build $BUILD"
fi
