# Workshop Mobile (Expo)

The Workshop as an iOS app: a thin Expo/React Native shell hosting the Workshop
web app in a WebView, with native capabilities (background location, local
notifications, microphone, Live Activities) bridged into the web via
`window.WorkshopNative`. One web codebase — sub-apps stay web and light up native
features when present.

Design + rationale: `../docs/mobile-app-proposal.md` and
`../docs/mobile-app-implementation-plan.md`.

## Architecture

```
Expo app (iOS)
└─ app/index.tsx ............ full-screen WebView -> WEB_URL (the Workshop SPA)
   ├─ injects window.WorkshopNative (native/inject.ts) before content loads
   └─ routes WebView messages -> native/bridge.ts -> capability modules
native/
  config.ts ................. webUrl / apiBase / tier from app.json `extra`
  inject.ts ................. the injected JS client (generated from @shared/native-bridge)
  bridge.ts ................. RN-side dispatcher (routes requests -> modules)
  location.ts ............... expo-location + TaskManager background task -> POST /location/update
  notify.ts ................. expo-notifications (local scheduling)
  audio.ts ................. expo-av recording (+ optional upload)
  secureStore.ts ........... expo-secure-store
  auth.ts .................. bearer token for off-tailnet API calls
  liveActivity.ts .......... optional native Live Activity module (graceful no-op until built)
  pages.ts ................. native page views (WKWebViews above the WebView, one data store per profile; iOS 17+)
  readSelection.ts ......... page side of "Read aloud" in the system selection menu (Mac app too)
  appearance.ts ............ page background -> top inset color and status bar text (light/dark)
modules/workshop-live-activity/  local Expo module: ActivityKit start/update/end (app target)
modules/workshop-pages/ .... local Expo module behind pages.ts (contract: ../shared/native-pages.ts)
modules/workshop-selection-menu/  local Expo view around the WebView: adds "Read aloud" to the selection menu, system items kept
targets/WorkshopWidgets/ ... WidgetKit extension sources (lock screen / Dynamic Island UI)
targets/WorkshopKeyboard/ .. keyboard extension: recent clips, tap to type one in
plugins/withLiveActivity.js  config plugin: NSSupportsLiveActivities
plugins/withKeyboardExtension.js  config plugin: adds the keyboard target and the app group at every prebuild
```

The shared wire contract lives in `../shared/native-bridge.ts` (imported by both
this app and the web frontend via the `@shared/*` alias), so method names and
payload shapes can never drift between the two sides.

## Prerequisites

- macOS + Xcode (iOS builds are macOS-only).
- An iPhone, the Tailscale app installed and logged in (so the device reaches the
  backend on the tailnet).
- Node + the Expo CLI (`npx expo`).

## First run (free, no Apple Developer Program)

```sh
cd mobile
npm install                 # or: npx expo install   (reconciles SDK 51 versions)
```

Point the app at your backend in `app.json` `extra`:
- Dev on LAN:  `webUrl` = `http://<mac-lan-ip>:5173`, `apiBase` = `http://<mac-lan-ip>:8000`
- Prod (tailnet): `webUrl` = `https://<host>.<tailnet>:8446`,
  `apiBase` = `https://<host>.<tailnet>:8446/api`

Because this app uses native modules (location, notifications, Live Activities),
it needs a **dev build**, not Expo Go:

```sh
npx expo run:ios            # builds + installs to a connected iPhone (free provisioning)
```

Free provisioning signatures expire after 7 days — re-run `expo run:ios` to
refresh. Background location, mic, and local notifications all work on the free
tier. Remote push is the deferred paid feature (see below).

Off-tailnet only: paste the backend service token once in the in-app Diagnostics
screen (it's stored in the Keychain). On the tailnet nothing is needed — the
backend trusts the peer.

## Live Activity setup (one manual Xcode step)

The Live Activity (one generic activity any app can start through
`liveActivities()` in `@workshop/ui`; contract in
`shared/native-live-activity.ts`) needs a Widget Extension target, which a
config plugin can't create reliably. The Swift sources are ready; add the target
once:

1. `npx expo prebuild` (generates `ios/`).
2. Open `ios/workshopmobile.xcworkspace` in Xcode.
3. File > New > Target > **Widget Extension** (enable "Include Live Activity").
   Name it `WorkshopWidgets`, set deployment target to **iOS 16.2**.
4. Replace the generated files with / add to the target:
   `targets/WorkshopWidgets/WorkshopWidgetsBundle.swift`,
   `WorkshopActivityWidget.swift`, and `Info.plist`.
5. Add `modules/workshop-live-activity/ios/WorkshopActivityAttributes.swift` to
   **both** the app target and the `WorkshopWidgets` target membership (the shared type).
6. Rebuild (`npx expo run:ios`).

`scripts/mobile-sidestore.sh` does not do this: it regenerates `ios/` with
`expo prebuild --clean` whenever `modules/` or `plugins/` change, so a SideStore
build has no widget extension, and an activity it starts shows nowhere.
Until the target is in the build, `liveActivity.isSupported` (Diagnostics
screen) still reports whether the system allows Live Activities, not whether
this build can draw one.

## Workshop keyboard

`targets/WorkshopKeyboard` is a custom keyboard listing the clipboard's recent
clips; a tap types one into the field. `plugins/withKeyboardExtension.js` adds
the target at every prebuild, with an app group shared by the app and the
keyboard (`group.<bundle id>`), and the SideStore build signs each bundle with
its entitlements file so SideStore registers the group. On the phone, once:
Settings > General > Keyboard > Keyboards > Add New Keyboard > Workshop, then
tap it and turn on Allow Full Access. Open the app once after that (signed in)
so it leaves its sign-in for the keyboard.

## Going paid later (remote push)

Enrol in the Apple Developer Program ($99/yr), then:
- set `extra.tier` to `"paid"` in `app.json` (the web app stops hiding push UI),
- configure the backend `APNS_*` env (see `backend/services/push.py`) + restart,
- add the Push Notifications capability + APNs key in Xcode.

The device-token registry (`POST /push/register`) and the alerter's best-effort
`send_push` call already exist; enabling APNs flips them on with no code change.
