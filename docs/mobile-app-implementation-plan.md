# Workshop Mobile — Implementation Plan

Companion to `docs/mobile-app-proposal.md` (the why + the cost reality). This doc is the
*how*: concrete phases, files, packages, native bits, and acceptance checks. Still not
scheduled — this is the build blueprint for when we start.

Decided constraints (operator): **iPhone only**, **Expo shell + WebView** (one web
codebase, sub-apps stay web), **free Apple account first**, **paid features (remote push +
push-driven Live Activities) designed but switched off** until the operator opts into $99/yr.

## 0. The background-execution model (the load-bearing fact)

iOS suspends your app's process within seconds of backgrounding it. A suspended app runs
NO code — no polling, no timers. Everything below is built around the sanctioned ways to
either keep running or get woken:

- **Active location session** keeps the app alive in the background (FREE). During GPS
  callbacks we can poll APIs, update a Live Activity, fire local notifications.
- **Audio record/play** keeps the app alive (FREE) — used for birds capture.
- **Local scheduled notifications** fire even if the app never runs again (FREE), but
  carry whatever we knew at schedule time.
- **Background fetch** wakes us sparsely on iOS's schedule (FREE, unreliable timing).
- **Remote push (APNs)** delivers/wakes with the app fully asleep ($99, deferred).

Design rule that falls out of this: **anything time-critical happens either (a) while a
location/audio session is active, or (b) via a pre-scheduled local notification.** Remote
push is reserved for the deferred paid phase and is never on the critical path for v1.

## 1. Current state & the pivot

`mobile/` already has an Expo SDK 51 + expo-router scaffold — BUT it's the wrong shape:
`app/(tabs)/index.tsx|projects.tsx|apps.tsx` are **native React Native screens**
(FlatLists) re-implementing Agents/Projects/Finance against the API directly. That is
exactly the **two-codebase trap** we're avoiding — every Workshop UI change would have to
be ported by hand.

**Pivot:** replace the native tab screens with a single **WebView** that loads the live
Workshop frontend. Keep the Expo project, expo-router, `lib/api.ts` (handy for native-side
calls), and the dark `colors`. The native side becomes a thin shell + capability modules,
not a parallel UI. We can keep one native "Debug/Status" screen behind the WebView for
diagnostics, but the product surface is the web app.

Backend reality we build on (already exists, no change needed for v1):
- `POST /location/update` with `LocationBody {lat,lng,accuracy_m,altitude_m,heading_deg,speed_mps,client_ts,source}`
  — the background-location module posts here with `source:"workshop-ios"`. **This lets us
  retire OwnTracks**: same sink, native producer.
- `GET /location/latest|history` — already there.
- Commute: `POST /commute/reason`, `scoring.next_scheduled()`, `scoring` status block, and
  `messages.append_deduped` — these feed the commute countdown + local-notification logic.

## 2. The capability bridge (the linchpin)

One stable contract the web app feature-detects. Present only inside the shell; absent in a
browser (→ web fallback). This is the concrete form of the `window.WorkshopNative` idea and
the existing capability-grant model.

### 2a. Web-side TypeScript contract (`frontend/src/native/bridge.ts`)

```ts
export type Capability = "location" | "audio" | "localNotify" | "liveActivity" | "secureStore";

export interface WorkshopNative {
  readonly platform: "ios";
  readonly tier: "free" | "paid";          // paid features advertised only when entitled
  has(cap: Capability): boolean;            // feature-detection per capability
  location: {
    startBackgroundTracking(opts?: { distanceM?: number }): Promise<void>;
    stopBackgroundTracking(): Promise<void>;
    getCurrent(): Promise<{ lat: number; lng: number; accuracy_m?: number }>;
  };
  audio: { startCapture(o: { maxSeconds: number }): Promise<string>; stopCapture(): Promise<void> };
  localNotify: {
    schedule(n: { id: string; title: string; body: string; fireAt: string }): Promise<void>;
    cancel(id: string): Promise<void>;
  };
  liveActivity: {                            // local-update version is FREE
    start(a: { id: string; kind: "commute"; state: object }): Promise<void>;
    update(id: string, state: object): Promise<void>;
    end(id: string): Promise<void>;
  };
  secureStore: { get(k: string): Promise<string | null>; set(k: string, v: string): Promise<void> };
}

declare global { interface Window { WorkshopNative?: WorkshopNative } }

// Helper every sub-app uses:
export const native = () => (typeof window !== "undefined" ? window.WorkshopNative : undefined);
```

Sub-apps call `native()?.location.startBackgroundTracking()` and fall back to web
geolocation when `native()` is undefined. **No sub-app becomes native.**

### 2b. Transport: WebView ↔ React Native

`react-native-webview` gives us two primitives:
- `injectedJavaScriptBeforeContentLoaded` — install a `window.WorkshopNative` stub that
  serializes each call to `window.ReactNativeWebView.postMessage(JSON)` and returns a
  Promise keyed by a `callId`.
- `onMessage` (RN side) — receive `{callId, cap, method, args}`, dispatch to the native
  module, post back `{callId, ok, result|error}`; the injected stub resolves/rejects the
  matching Promise.

Plus a **push channel** RN→web for unsolicited events (e.g. a background location fix, an
audio-capture-finished event): RN calls `webview.injectJavaScript("window.__wsNativeEvent(...)")`,
the stub dispatches to subscribers. One small correlation layer; ~150 lines, written once.

Native dispatcher lives at `mobile/native/bridge.ts` (the RN-side router) wiring each
capability to its Expo module below.

## 3. Phases (free)

### Phase 0 — Free dev loop, no $99 *(½ day)*
- `cd mobile && npx expo run:ios` onto the operator's plugged-in iPhone (free provisioning,
  accept the 7-day re-sign cycle at this stage).
- Wire `extra.apiBase` to the **tailnet origin** (currently a LAN IP) so the device reaches
  the backend off-LAN. Add an EAS project + `eas update` (free OTA) for JS iteration.
- **Acceptance:** the app launches on-device and reaches `/health`.

### Phase 1 — WebView shell replaces native screens *(1–2 days)*
- Add `react-native-webview`. Replace `app/(tabs)/*` with `app/index.tsx` = full-screen
  WebView → `apiBase` (the Workshop SPA). Keep a hidden `app/debug.tsx`.
- Handle: pull-to-refresh, back-gesture/hardware back → WebView history, external links →
  system browser, safe-area insets, dark status bar, offline screen.
- **Acceptance:** the entire Workshop UI (all apps) works inside the app, visually
  indistinguishable from Safari, with no per-app native code.

### Phase 2 — Bridge skeleton + first capability *(1–2 days)*
- Implement §2a stub injection + §2b transport + RN dispatcher.
- First capability end-to-end: `location.getCurrent()` (`expo-location`, foreground).
- Add `frontend/src/native/bridge.ts` to the web app; make `/apps/location` prefer
  `native()` and fall back to web geolocation.
- **Acceptance:** location page shows a native fix when run in the app; web fix in a browser.

### Phase 3 — The free native wins *(the meat, ~1–2 weeks)*

**3a. Background location → retire OwnTracks**
- `expo-location` `startLocationUpdatesAsync` + `expo-task-manager` background task.
- `app.json` iOS: `UIBackgroundModes:["location"]`, `NSLocationAlwaysAndWhenInUseUsageDescription`,
  `expo-location` plugin with `isIosBackgroundLocationEnabled:true`.
- Background task POSTs each fix to `POST /location/update` `{source:"workshop-ios"}`.
- Smart power: standard updates only while a commute is active; significant-change /
  geofence otherwise (mirror OwnTracks "Significant" behavior).
- **Queue + retry offline — BUILT** (`mobile/native/locationQueue.ts`): a fix that fails to
  send (network blip, backend restart) is persisted to a flat JSON file in the app document
  dir and re-sent oldest-first on the next background delivery, in chronological order;
  capped at 500 (drops oldest). `trackingStatus().queued` surfaces the backlog depth for
  on-device verification on the debug screen. So OwnTracks retirement no longer risks a
  location gap during an outage.
- **Acceptance:** with the app backgrounded, `/location/latest` updates as the phone moves;
  OwnTracks can be uninstalled. (See `project_commute_location_2026_06_06`.)

**3b. Local notifications for commute**
- `expo-notifications`, local only. On launch / location change, read commute
  `next_scheduled()` + status and schedule "leave by HH:MM" `UNNotificationRequest`s;
  reschedule whenever the app is running with fresher data.
- **Acceptance:** a leave-time notification fires on time with the app closed.

**3c. Local-update Live Activity (commute countdown)** *(hardest free item)*
- Live Activities are **not** first-class in Expo. Requires a **custom dev build** with a
  **WidgetKit + ActivityKit extension in Swift**, surfaced via an Expo **config plugin**.
  Options: community `expo-live-activity`-style module, or a hand-written config plugin +
  Swift `ActivityAttributes`/widget. Budget real Swift time here.
- Start the activity from foreground (open app / "start commute"); update it from the 3a
  background location callbacks (`Activity.update`) with next-train + arrival countdown.
- **Acceptance:** lock screen / Dynamic Island shows a live commute countdown that advances
  while backgrounded during a commute — no server, no push, no $99. (Your Uber example.)

**3d. Microphone / background audio (birds)**
- `expo-av` record; `UIBackgroundModes:["audio"]`; capture clips POST to the birds ingest.
- **Acceptance:** a backgrounded capture records and uploads a clip.

### Phase 4 — Auth *(2–3 days)*
- Real login screen (web side) → token in `expo-secure-store` via `secureStore` capability,
  sent as bearer to the backend. Slots into `backend/services/permissions.py` (operator vs
  guest), compatible with the tailnet auth gate (`project_workshop_tailnet_auth_gate`).
  Generalized identity, single user in practice — **no multi-tenant guardrails**.
- **Acceptance:** off-tailnet, the app authenticates and the backend authorizes by token.

## 4. Phase 5 — PAID, deferred (only on operator opt-in)

Designed now so it's a credentials/config flip, not a rebuild:
- Enroll Apple Developer Program ($99/yr) → escapes the 7-day treadmill (year certs) and
  unlocks the entitlements.
- **Remote push:** `expo-notifications` push tokens + APNs key; server pushes commute
  alerts via the **existing notify pipeline** (`services/commute/alerter.py`,
  `notify-commute.txt` → today Discord; add an APNs sender). The `tier` flips to `"paid"`
  so the web app stops hiding push-dependent UI.
- **Push-driven Live Activity:** add ActivityKit push tokens to the Phase-3c activity so it
  updates while the app is fully asleep (delay arrives while idle, not commuting).
- **Acceptance:** a backend event reaches the locked phone with the app killed.

## 5. Risks / watch-items

- **Live Activity native code (3c)** is the only genuinely hard, Swift-requiring piece —
  schedule it deliberately, not as an afterthought.
- **iOS background-location throttling**: standard continuous GPS drains battery; use it
  only during active commutes, significant-change otherwise. Validate real battery cost.
- **WebView ≠ Safari** for some PWA-ish APIs; the bridge covers the gaps, but audit any web
  app relying on `navigator.*` that the WebView restricts.
- **7-day re-sign** during Phases 0–4 is a real chore for an always-on tracker; it's the
  practical pressure toward Phase 5, by design.
- **OwnTracks cutover (3a)**: keep both running until the native producer is proven, then
  uninstall OwnTracks — don't create a location gap.
- Follow `frontend/CLAUDE.md` design system for any web UI added (login screen, native
  capability toggles): tokens, lucide icons, no emoji, Edit-mode for writes.
