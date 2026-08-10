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
modules/workshop-live-activity/  local Expo module: ActivityKit start/update/end (app target)
targets/WorkshopWidgets/ ... WidgetKit extension sources (lock screen / Dynamic Island UI)
plugins/withLiveActivity.js  config plugin: NSSupportsLiveActivities
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

The lock-screen / Dynamic Island commute countdown needs a Widget Extension
target, which a config plugin can't create reliably. The Swift sources are ready;
add the target once:

1. `npx expo prebuild` (generates `ios/`).
2. Open `ios/workshopmobile.xcworkspace` in Xcode.
3. File > New > Target > **Widget Extension** (enable "Include Live Activity").
   Name it `WorkshopWidgets`, set deployment target to **iOS 16.2**.
4. Replace the generated files with / add to the target:
   `targets/WorkshopWidgets/WorkshopWidgetsBundle.swift`,
   `CommuteLiveActivity.swift`, and `Info.plist`.
5. Add `modules/workshop-live-activity/ios/CommuteActivityAttributes.swift` to
   **both** the app target and the `WorkshopWidgets` target membership (the shared type).
6. Rebuild (`npx expo run:ios`).

Until this is done, `liveActivity.*` is a graceful no-op and everything else
works. Verify on-device from the Diagnostics screen (`liveActivity.isSupported`).

## Going paid later (remote push)

Enrol in the Apple Developer Program ($99/yr), then:
- set `extra.tier` to `"paid"` in `app.json` (the web app stops hiding push UI),
- configure the backend `APNS_*` env (see `backend/services/push.py`) + restart,
- add the Push Notifications capability + APNs key in Xcode.

The device-token registry (`POST /push/register`) and the alerter's best-effort
`send_push` call already exist; enabling APNs flips them on with no code change.
