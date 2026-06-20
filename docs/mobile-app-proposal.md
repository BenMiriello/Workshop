# Workshop as a Mobile App — Research & Proposal

Status: research + proposal only. Not scheduled for build. Written 2026-06-17.

## 1. What you actually want

Restating the goals so the proposal can be measured against them:

1. **Workshop itself becomes a real mobile app** (iPhone first). The apps *inside* it
   (birds, commute, budget, location, finance, future apps) ride along.
2. **Do not rewrite every sub-app as a native app.** New apps should stay web. One
   codebase, not two.
3. **It's a login app.** Generalized auth (anyone *could* sign in; you happen to be the
   only user). This is normal product auth, not the multi-tenant guardrail cruft we don't do.
4. **Real native capabilities**, specifically:
   - Continuous **background location** (replace/augment OwnTracks).
   - **Push notifications** (replace the commute notify-file -> Discord fallback).
   - **Live Activities** — the lock-screen / Dynamic Island "live status" thing
     (Uber ride progress, podcast scrubber). For commute: next train + arrival countdown.
   - **Microphone / background audio** (birds-style capture from the phone).
5. **Cheap.** Ideally no recurring App Store fee; install it some other way if possible.

The headline finding: goals 1-4 are very achievable and your instinct ("wrap the web
apps in a container") is exactly the right architecture. Goal 5 is where reality bites —
the specific native powers you want (push, background modes, Live Activities) are gated
behind Apple's $99/yr paid program. There is no free path to *those* on iOS. Details below.

## 2. The core decision: PWA vs. native wrapper vs. full rewrite

Three ways to "make a web app a mobile app":

| Approach | One codebase? | Background location | Push | Live Activities | Mic in background | Cost |
|---|---|---|---|---|---|---|
| **PWA** (Add to Home Screen) | Yes | No (unreliable, no true background) | Weak (16.4+, flaky, must install first) | **No** | No | Free |
| **Native wrapper (Expo / Capacitor)** | **Yes** | **Yes** (free) | **Yes** ($99) | **Yes** (local free / push $99) | **Yes** (free) | **mostly free**; $99/yr only for push |
| **Full React Native rewrite** | No (two codebases) | Yes | Yes | Yes | Yes | $99/yr + huge effort |

**PWA is out.** It's the free dream, but it categorically can't do Live Activities, can't
do reliable background location, and its push is unreliable on iOS. It fails goal 4
outright. (Sources: MagicBell, Brainhub, MobiLoud PWA-on-iOS guides — all 2025/2026,
all agree Live Activities and true background work are not available to PWAs.)

**Full rewrite is out.** It violates goal 2 and 3 (two codebases) for no benefit we need.

**A native wrapper is the answer.** It does exactly what you described: hosts your
existing web app in a thin native shell (a WebView) and exposes native device APIs to your
web JavaScript through a bridge. Your React frontend is the app; the sub-apps stay web.
Two flavors: **Capacitor** (purpose-built web-app wrapper) or **Expo** (React Native app
hosting a WebView). We use **Expo** — it's already the monorepo's `mobile/` toolchain and
the operator's prior experience. See §3 for how Expo keeps the one-web-codebase model.
(Sources: Capacitor docs; Expo docs.)

> Capacitor is Ionic's successor to Cordova. It is the mature, current standard for
> "ship my web app as a native app." Not a hack.

## 3. Proposed architecture

**Shell choice: Expo** (already the house toolchain — `mobile/` in the monorepo is
Expo per the repo CLAUDE.md, and the operator has used Expo before and prefers it).
Expo is React Native, not a web wrapper, so we keep the one-web-codebase model by making
the Expo app's main screen a **WebView pointing at the Workshop**, with Expo's native
modules bridged into the web via postMessage. Capacitor would also work (it's the more
"web-app-native" of the two), but Expo is chosen for familiarity + free EAS OTA updates.
The architecture below is identical either way; only the shell/bridge mechanics differ.

```
┌─────────────────────────────────────────────────────────┐
│  iOS app binary (Expo / React Native shell, built in Xcode│
│  or EAS; one screen = a WebView)                          │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │  WebView (react-native-webview)                    │  │
│  │   → loads the Workshop SPA (the SAME frontend       │  │
│  │     that runs at the tailnet today)                 │  │
│  │                                                    │  │
│  │   Workshop shell  ─┬─ /apps/commute   (web)         │  │
│  │                    ├─ /apps/birds     (web)         │  │
│  │                    ├─ /apps/budget    (web)         │  │
│  │                    └─ /apps/<new>     (web)         │  │
│  │                                                    │  │
│  │   window.WorkshopNative  ← JS capability bridge     │  │
│  └───────────────┬────────────────────────────────────┘  │
│                  │ postMessage bridge ↔ RN native modules │
│  Native modules: │                                       │
│   • expo-location (background)  → POST /api/location      │  ← FREE
│   • expo-av (microphone / background audio)              │  ← FREE
│   • expo-notifications (LOCAL, on-device scheduled)      │  ← FREE
│   • Live Activities (local-update, no push)             │  ← FREE
│   • Secure storage (expo-secure-store, auth token)      │  ← FREE
│   • Push notifications (APNs / EAS push)                 │  ← PAID, deferred
│   • Push-driven Live Activities                         │  ← PAID, deferred
└─────────────────────────────────────────────────────────┘
              │ network (tailnet / public origin)
              ▼
   Existing Workshop backend (uvicorn :8000), unchanged APIs
```

### How sub-apps get native powers without becoming native

This is the important part and it fits what we already built.

We already mount sub-apps inside the Workshop frontend by importing their source
(the `@commute` alias model — see `project_commute_location_2026_06_06`). So there is
already **one frontend that contains all the apps.** The native shell (Expo) just loads
*that* frontend in a WebView. Nothing per-app changes to "become mobile."

For native features, the shell exposes a single global capability object, e.g.
`window.WorkshopNative`, with a small, stable API:

```js
// Any sub-app, plain web code:
const loc = window.WorkshopNative?.location;   // present only inside the app
if (loc) {
  loc.startBackgroundTracking();   // native plugin under the hood
} else {
  // running in a normal browser → fall back to OwnTracks / web geolocation
}
```

Because each capability is **feature-detected**, the exact same web code runs:
- in a desktop browser (capability absent → web fallback or no-op),
- in the installed mobile app (capability present → native plugin fires).

That's the whole trick. No app is "the mobile version." There is one web codebase; the
shell lights up extra powers when present. This is the natural extension of the
"location-as-capability" idea already in `docs/app-platform-plan.md` and the directional
capability-grant model — a sub-app *requests* the location/push/audio capability, the
shell *grants* it.

### Loading model: remote URL vs. bundled assets

Capacitor can either ship the web build inside the binary, or point the WebView at a
remote URL.

- **Remote URL → the tailnet origin.** The app is a thin shell; it loads the live
  Workshop UI over the network. Pro: always current, zero rebuild to ship UI changes,
  trivially "one deploy." Con: needs connectivity to reach the backend; Apple dislikes
  this for *store* apps (irrelevant for self-install). **Recommended for v1**, given a
  single user already on the tailnet.
- **Bundled assets + OTA updates.** Ship the SPA in the binary; push new web bundles
  over-the-air later (Capgo / Ionic Appflow live-update) without re-submitting. Better
  offline story, store-friendly. **Recommended once you want offline or distribute it.**

Native plugin code (location, push, Live Activities) ships in the binary either way and
only changes on a real rebuild — but that code is small and stable, so rebuilds are rare.

## 4. The cost reality (goal 5) — corrected, sharper

My first pass overstated this. The truth is narrower, and it changes the plan: **most of
what you want is free.** Verified against Apple docs + Expo docs (sources at bottom).

Two different things get called "the Apple fee," and only one matters here:

1. **App Store listing / review** — not needed. We self-install onto your own iPhone
   straight from Xcode/Expo. No store, no review, no public listing, ever.
2. **Apple Developer Program ($99/yr)** — grants a few portal-issued *entitlements*.
   It turns out only **one** capability on your list actually needs it.

### What works on a FREE Apple account

- **Background location (continuous, while the app is suspended).** Background modes are
  **not** gated by a paid entitlement — they're just an `Info.plist` key
  (`UIBackgroundModes` → `location`). Apple's docs are explicit. **Free.**
- **Microphone / background audio.** Same mechanism (`UIBackgroundModes` → `audio`). **Free.**
- **Local notifications** — on-device scheduled alerts (e.g. commute "leave in 10 min"
  computed locally). **Free.**
- **Local-update Live Activities** — the lock-screen / Dynamic Island widget, updated by
  *your app* (foreground or background task) rather than a server push. **Free.**
- **Secure token storage, the WebView, the whole Workshop UI.** **Free.**

### What genuinely requires the $99 — and is therefore DEFERRED

- **Remote push notifications.** Hard-confirmed: a free "Personal Team" *cannot* enable
  the Push Notifications capability — it's greyed out in Xcode; the `aps-environment`
  entitlement is portal-issued, membership-only. Expo's own docs say the same: generating
  iOS push credentials requires a paid membership. Expo is **not** a loophole — the gate
  is Apple's, not the toolchain's. (What "Expo worked free before" actually was: Expo Go,
  the sandbox app Expo signs with *their* paid account, with your JS running inside it.
  A standalone installable build follows Apple's rules.)
- **Push-driven Live Activities** (server pushes a live train countdown to the lock screen
  while the app is asleep) — needs push, so paid.

### The real free-tier catch: the 7-day treadmill

Free-account signatures **expire after 7 days.** The app stops launching until you plug
into your Mac and re-run/re-sign (AltStore can automate this while your Mac is reachable).
For most apps: annoying. For an always-on background tracker: the actual pain point — more
than the feature gating. This is the thing that, in practice, eventually pushes you to pay.

### Decision (operator, 2026-06-17): Expo-first, free, defer the paid features

Build and run free on Expo with a Mac + free Apple account. Ship everything in the FREE
column. **Design** the push + push-driven-Live-Activity features so the seams exist, but
**leave them switched off** until the operator decides the app is worth $99/yr. When that
day comes, flipping to paid is a credentials change, not a rearchitecture:
- push notifications light up the already-built notification paths (server side already
  has the commute notify pipeline);
- push-driven Live Activities upgrade the already-built local Live Activity.

No Android (operator: iPhone only). Mac + Xcode: confirmed available.

## 5. How each capability maps onto what we already run

This is where it gets nice — the mobile app mostly *replaces fragile glue we already maintain*:

| Want | Today | With the app |
|---|---|---|
| Background location | OwnTracks app posts to `/api/location/owntracks`, "Significant mode", needs the right tailnet + exact hostname (two outages already) | Native background-geolocation plugin posts to the **same endpoint**. We can **retire OwnTracks** entirely. |
| Notifications | commute writes `.runtime/commute/notify-commute.txt`, masini tails it -> Discord | Direct APNs push to your phone. Discord fallback stays as backup. |
| Live commute status | commute "Now" feed inside the app | **Live Activity**: lock-screen + Dynamic Island showing next train / arrival countdown (your exact Uber example). |
| Bird audio | Pi Zero in the yard streaming to the Brain | Phone mic as an ad-hoc capture device for the birds pipeline. |

So the mobile shell isn't just a wrapper — it's a cleaner replacement for OwnTracks and
the notify-file relay.

## 6. Auth (goal 3)

Generalized, single-user-in-practice login. Recommended shape:
- Standard email/password or passkey -> session token, token kept in Capacitor secure
  storage (Keychain on iOS), sent as a bearer to the existing backend.
- The backend's grants/permissions framework (`backend/services/permissions.py`,
  default-permissive) already models "operator vs guest." Mobile auth slots in as the
  identity source feeding that. No new multi-tenant machinery — just a real login screen
  in front of the existing identity model.
- This stays compatible with the tailnet auth gate (`project_workshop_tailnet_auth_gate`):
  CGNAT-trusted on the tailnet, token-authed when the app is off-tailnet.

## 7. Plan — Expo-first, free, paid features deferred

**Chosen path: Expo shell + WebView loading the tailnet origin, iOS only, FREE Apple
account.** PWA rejected; full rewrite rejected; Android skipped (operator preference).
Paid-only features (remote push, push-driven Live Activities) are **designed but switched
off** until the operator decides to pay. Mac + Xcode confirmed available.

Phases (when/if we do this — not scheduled yet):

0. **Free dev loop, no $99.** `npx expo run:ios` / Xcode install to the operator's own
   plugged-in iPhone (7-day re-sign cycle accepted at this stage). EAS Update (free OTA)
   for web-bundle iteration. This is the whole "try it before paying" gate.
1. **Capability bridge in the web layer (no native yet).** Define `window.WorkshopNative`
   contract + feature-detection + web fallbacks. Pure web work, testable in a browser,
   useful even before any shell exists. Wire it into the existing capability-grant model.
   Mark each capability `free` vs `paid-deferred` so the UI can hide/disable the deferred ones.
2. **Expo shell + WebView.** Wrap the frontend, load the tailnet origin, prove WebView +
   the postMessage bridge with one capability (foreground geolocation).
3. **The FREE native wins.** Background geolocation -> **retire OwnTracks**. Background
   mic for birds. **Local** notifications for commute (compute "leave now" on-device).
   **Local-update** Live Activity for the commute countdown (refreshed by a background task).
4. **Auth.** Real login screen -> token in secure store -> existing grants/permissions model.
5. **(PAID, deferred — only when the operator opts in.)** Enroll $99/yr, flip on remote
   push (server already has the commute notify pipeline) and upgrade the local Live
   Activity to push-driven. Plus: escape the 7-day treadmill (year-long certs). Pure
   credentials/config change on top of phases 0-4, not a rearchitecture.

No open blockers — Mac confirmed, Expo confirmed, free-first confirmed. The only future
decision is the phase-5 opt-in to $99/yr, made after the operator has lived with phases 0-4.

## Sources

- [PWA iOS limitations & Safari support (MagicBell, 2026)](https://www.magicbell.com/blog/pwa-ios-limitations-safari-support-complete-guide)
- [PWA on iOS — status & limitations (Brainhub, 2025)](https://brainhub.eu/library/pwa-on-ios)
- [Do PWAs work on iOS? (MobiLoud, 2026)](https://www.mobiloud.com/blog/progressive-web-apps-ios)
- [Capacitor — wrapping a web app, plugins](https://capacitorjs.com/docs/apis/background-runner)
- [Capacitor + Next.js native build (Capgo)](https://capgo.app/blog/building-a-native-mobile-app-with-nextjs-and-capacitor/)
- [@capgo/background-geolocation plugin](https://www.npmjs.com/package/@capgo/background-geolocation)
- [Transistorsoft Capacitor background geolocation](https://github.com/transistorsoft/capacitor-background-geolocation)
- [Capacitor Live Activities community plugin (awesome-capacitor)](https://github.com/riderx/awesome-capacitor)
- [Apple: push notifications require paid membership (Developer Forums)](https://developer.apple.com/forums/thread/77135)
- [Starting/updating Live Activities with ActivityKit push (Apple docs)](https://developer.apple.com/documentation/activitykit/starting-and-updating-live-activities-with-activitykit-push-notifications)
- [Free sideloading tools compared, 2026 (Scarlet iOS)](https://scarletios.com/free-sideloading-tools-compared-2026-edition/)
- [How iOS sideloading works in 2025 — dev certs, AltStore, EU exception (dev.to)](https://dev.to/1_king_0b1e1f8bfe6d1/how-ios-sideloading-actually-works-in-2025-dev-certs-altstore-and-the-eu-exception-1m2h)
- [Apple Developer Program membership comparison](https://developer.apple.com/support/compare-memberships/)
- [Apple: handling location updates in the background (UIBackgroundModes, not entitlement-gated)](https://developer.apple.com/documentation/corelocation/handling-location-updates-in-the-background)
- [Apple: UIBackgroundModes Info.plist key](https://developer.apple.com/documentation/bundleresources/information-property-list/uibackgroundmodes)
- [Expo: push notifications require a paid Apple Developer membership](https://docs.expo.dev/push-notifications/push-notifications-setup/)
- [Expo: iOS capabilities & EAS entitlement sync](https://docs.expo.dev/build-reference/ios-capabilities/)
- [Free Apple Personal Team can't enable Push/iCloud (Xcode provisioning errors)](https://gordonbeeming.com/blog/2025-10-15/solving-xcode-provisioning-profile-and-capability-errors)
