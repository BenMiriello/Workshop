# Mobile iOS build runbook

The Workshop iOS app (`mobile/`, Expo SDK 51 / RN 0.74.5) builds and runs on a
physical iPhone. This is the durable record of how to build it, every
compatibility fix baked into the repo and why, and the routine you repeat.
First brought up 2026-07-11 on the operator's Mac (Xcode 26.6, iOS 26.5 device).

## One-command deploy

`scripts/mobile-deploy.sh` (run on the Mac, phone attached + unlocked) does the
whole thing: pull, reinstall JS deps only if `mobile/package*` changed, build
Release, install + launch on the device. It also re-signs, so the same command
covers a code update and the weekly 7-day re-sign:
```bash
~/Development/Workshop/scripts/mobile-deploy.sh            # code update / re-sign
~/Development/Workshop/scripts/mobile-deploy.sh --prebuild # only if native config changed
```
`--prebuild` wipes the signing Team; set `WORKSHOP_TEAM_ID` (10-char Apple Team
ID) so it re-signs non-interactively, or open Xcode once to re-pick the Team.
The manual routine below is the same thing by hand.

## The routine (what you actually do)

**Day-to-day (JS/TS changes only, the common case):**
1. Edit code under `mobile/`.
2. In Xcode, hit **▶ Run** (Release scheme). The build phase re-bundles the JS.
   No prebuild, no reinstall. Signing Team and the (absent) capabilities are
   preserved because the native `ios/` project isn't regenerated.

**Rebuild every ~7 days (free-tier provisioning expires, see below):**
- Plug in the iPhone, open `mobile/ios/Workshop.xcworkspace`, **▶ Run**.
  Everything is cached, so this is ~1-2 min. Re-**Trust** the developer on the
  phone if prompted (Settings, see step 4 of first-time setup).

**Only when native deps or `app.json` native config change** do you re-prebuild:
```bash
cd ~/Development/Workshop/mobile
rm -rf node_modules ios
npm install --ignore-scripts          # NOT `npm ci` (see expo-font note)
npx expo prebuild -p ios --clean
(cd ios && pod install)
open ios/Workshop.xcworkspace
```
After a prebuild you must re-set the signing **Team** in Xcode (prebuild
regenerates the project and wipes it). That is the only thing that resets.

## First-time machine setup (already done on the operator's Mac)

1. **Node** ≥ 20.19.4 (Expo CLI requires it). Installed via nvm:
   `nvm install --lts && nvm use --lts && nvm alias default 'lts/*'`.
2. **Ruby + CocoaPods**: system Ruby 2.6 is too old (SIP-locked, EOL). rbenv:
   `brew install rbenv ruby-build && rbenv install 3.3.5 && rbenv global 3.3.5`
   (+ `eval "$(rbenv init - zsh)"` in `~/.zshrc`), then `gem install cocoapods`.
3. **Xcode** must be ≥ the phone's iOS. The device is on iOS 26.5, so Xcode 16.4
   could not deploy (`connected (no DDI)` from `xcrun devicectl list devices`).
   Xcode 26.6 fixed it. Xcode 26 needs macOS ≥ 15.6 (operator went to Tahoe 26).
   After install: `sudo xcode-select -s /Applications/Xcode.app && sudo xcodebuild -license accept`.
4. **Signing** (free Personal Team): Xcode, Workshop target, Signing &
   Capabilities, Automatically manage signing, Team = the Apple ID's Personal
   Team. First launch on the phone: **Settings, General, VPN & Device
   Management, [Apple ID], Trust**.
5. **Release build needs Node on Xcode's PATH** (nvm node isn't visible to
   Xcode's build shell). One-time, per machine (gitignored, absolute path):
   ```bash
   echo "export NODE_BINARY=$(which node)" > mobile/ios/.xcode.env.local
   ```

## Compatibility fixes baked into the repo (why they exist)

All four survive future prebuilds because they live in tracked config, not in
`ios/` or `node_modules`. The root cause of the first three: **SDK 51 is from
mid-2024 and the Mac's Xcode 26 / Swift toolchain is ~2 years newer.**

1. **expo-font pinned to `~12.0.10`** (`mobile/package.json` dep + `overrides`).
   `@expo/vector-icons` declares `expo-font: *`, so npm hoisted the latest
   (55.x) to the top of `node_modules`; the native build linked that against
   SDK-51's `expo-modules-core` (1.12.x) and failed to compile
   (`RenderToImageOptions`: `Color` does not conform to `AnyArgument`). Pinning
   keeps the hoisted copy at the SDK version. Because of the override you must
   install with `npm install` (which reconciles the lockfile), never `npm ci`.

2. **expo-dev-client removed** (`mobile/package.json`, `start` script). Its
   `expo-dev-menu` uses the C macro `TARGET_IPHONE_SIMULATOR` directly in
   Swift, which Xcode 26 rejects (`Cannot find TARGET_IPHONE_SIMULATOR in
   scope`). The dev launcher/menu isn't imported anywhere and isn't needed for a
   standalone install. Trade-off: no Metro dev-launcher on device, so we run
   **Release** builds (embedded JS bundle) instead of Debug+Metro.

3. **Remote push disabled** (`expo-notifications` config plugin removed from
   `app.json` `plugins[]`; the package stays for LOCAL notifications). The
   plugin injects the `aps-environment` entitlement, which a free Personal Team
   cannot sign, and which made the Push Notifications capability reappear on
   every prebuild. Removing it stops that at the source. Re-enable for remote
   push after paid enrolment (see below).

4. **`mobile/metro.config.js`** resolves the `@shared/*` monorepo alias. The app
   imports runtime values from the repo-root `shared/native-bridge.ts` (outside
   `mobile/`). Metro doesn't read tsconfig paths or watch external folders, so
   the Release bundle failed (`Unable to resolve module @shared/native-bridge`).
   The config adds `../shared` to `watchFolders` and maps the `@shared` prefix.

## Free Personal Team vs Apple Developer Program ($99/yr)

Currently **free** (`app.json` `extra.tier: "free"`). Limits of the free tier:
- **Provisioning profiles expire after ~7 days** — the app stops launching and
  you rebuild (fast, see routine). This is the main friction.
- **No remote push** (APNs). Local notifications work.
- No TestFlight, no App Store, max 3 sideloaded app IDs at a time, no remote
  Live Activity updates.

Enrolling in the **Apple Developer Program ($99/yr)** gets:
- Provisioning good for ~1 year (**no more weekly rebuild**).
- **Remote push** (re-add the `expo-notifications` plugin + Push capability).
- **TestFlight** — install/update over-the-air, no cable or Xcode each time.
- **App Store** distribution (public or unlisted) if ever wanted.
- Up to 100 registered devices per type; remote Live Activity push updates
  (would let commute alerts push to the Lock Screen); no 3-app limit.

To flip to paid: enroll, set `extra.tier` to `"paid"`, re-add
`"expo-notifications"` to `plugins[]`, prebuild, add the Push Notifications
capability in Xcode, select the paid team for signing.

## Config that is intentionally NOT committed

- Backend origin: `native/config.ts` defaults to the public deployment; override
  per-machine via `EXPO_PUBLIC_WORKSHOP_URL` in `mobile/.env.local` (gitignored).
- `mobile/ios/.xcode.env.local` (Node path) is machine-local and gitignored.
- `mobile/ios/` itself is generated by prebuild and gitignored.
