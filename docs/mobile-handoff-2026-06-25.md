# Mobile app handoff — continue on Mac (2026-06-25)

Temporary handoff doc for the agent picking up the Workshop iOS app on the operator's
Mac. Branch: **`mobile-app-rebuild`** (forked from `birds-redesign` at this point; all the
work below is committed here). Delete this
file once the Mac bring-up is done.

## What this is

Workshop is shipping as an **iPhone app** = an **Expo + WebView shell** (`mobile/`) that
loads the existing Workshop SPA (`frontend/`) and exposes native powers to the web through
a `window.WorkshopNative` capability bridge (`shared/native-bridge.ts`). One web codebase;
sub-apps stay web and ride inside the shell. iPhone only. Free Apple account first; the
$99 push phase is a designed-but-off seam.

Full background: `docs/mobile-app-proposal.md` (why + cost), `docs/mobile-app-implementation-plan.md`
(the build blueprint, kept current), `mobile/README.md` (the shell + the one Xcode step).

## State as of this handoff (all committed on `mobile-app-rebuild`, all green)

Verified before handoff: `mobile/` tsc 0, `frontend/` tsc 0, design gate clean, backend
**107 tests pass**. Frontend mobile widths checked headless at 390px (no horizontal overflow
on login/apps/<slug>/<slug>/settings).

Built and working (software side):
- **Shell** (`mobile/`): WebView loads the SPA; native bridge dispatcher (`native/bridge.ts`)
  + injected client (`native/inject.ts`); background location (`native/location.ts`) posting
  to `/location/update` (replaces OwnTracks) with a **durable offline queue**
  (`native/locationQueue.ts`, re-sends failed fixes in order); background audio
  (`native/audio.ts`); local notifications (`native/notify.ts`); secure storage
  (`native/secureStore.ts`); Live Activity module (`modules/workshop-live-activity` +
  `targets/WorkshopWidgets/`, no-ops until the Xcode step below).
- **Product auth** (live on the Linux backend): real accounts. Backend
  `services/auth_store.py` (SQLite users + scrypt), `services/jwt_tokens.py` (access +
  rotating refresh JWT), `routes/auth.py` (register/login/refresh/logout/me/password),
  `deps_user.py`. Web `frontend/src/lib/auth.ts` + `auth/AuthProvider` + `auth/Guard`
  (login wall) + `pages/Login.tsx` + Account section in Settings (change password, sign
  out everywhere). The shell's `native/auth.ts` reads the web-stored tokens from the
  Keychain (`workshop.ws-auth-*`) with proactive refresh, so background producers post as
  the logged-in user.
- **Native "This device" section** in Settings (feature-detected, shell-only): location
  tracking toggle + mode + permission + offline-queue depth, notifications enable, Live
  Activity readiness, free/paid tier.
- **iOS safe-area** activated (`viewport-fit=cover` in `frontend/index.html`).

## Operator must do once, before/at first run

1. **Register the operator account.** The login wall is live; the user DB is empty. On
   first load, "Create account" with **<operator-email>** (that email is wired as the
   operator via `WORKSHOP_OPERATOR_EMAIL` in `~/.config/workshop/secrets.env` on the Linux
   box; only the operator account reaches finance/location etc.). Any other email registers
   as a no-access member.
2. **Phone on the tailnet.** The iPhone needs the Tailscale app (logged into the operator's
   tailnet) so it can reach the Linux backend off-LAN.

## Mac bring-up steps (the actual remaining work)

The Linux box runs the backend (systemd `workshop-backend.service` on :8000, exposed via
Tailscale Serve). The Mac only builds/runs the iOS client.

1. `git checkout mobile-app-rebuild && git pull`.
2. Point the app at the tailnet (edit `mobile/app.json` → `expo.extra`), replacing the LAN
   IPs currently there:
   - `webUrl`: `https://<host>.<tailnet>:8443`  (the SPA, via Tailscale Serve → vite)
   - `apiBase`: `https://<host>.<tailnet>:8446`  (the backend, for native posts; no `/api` suffix — the backend strips `/api` itself)
   - `tier`: leave `"free"`.
3. `cd mobile && npm install` (done once on Linux; redo on the Mac).
4. `npx expo run:ios` to the plugged-in iPhone (free provisioning; accept the 7-day re-sign
   for now). Acceptance: the app launches and shows the **login wall**; after the operator
   registers, the full Workshop loads in the WebView.
5. **Live Activity (one manual Xcode step):** add the Widget Extension target as documented
   in `mobile/README.md` (`plugins/withLiveActivity.js` + `targets/WorkshopWidgets/`). Until
   done, `liveActivity` gracefully no-ops and everything else works.
6. Smoke-test the native wins on device: background location updates `/location/latest`
   (then OwnTracks can be uninstalled); a leave-by commute notification fires with the app
   closed; the Device section in Settings shows tracking status + queue depth.

## Deferred (do NOT do unless the operator asks)

- **$99 Apple Developer Program / remote push** — Phase 5, a config flip (`services/push.py`
  + APNs key, `extra.tier="paid"`). Designed, off.
- **Per-user data partitioning** — auth identifies users but the apps' data is still
  single-owner; partition app-by-app later, per the operator's "incremental" decision (see
  memory `project_workshop_product_auth`). This is backend product infra, not mobile.

## Gotchas / facts the Mac agent needs

- **Backend lives on Linux, not the Mac.** Don't try to run it on the Mac. If the backend
  needs a restart for a backend change, that happens on the Linux box
  (`scripts/restart_backend.sh`, preserves secret env). The Mac builds only the client.
- **Auth is owned by the web app.** The shell does NOT bake a token. Login happens in the
  WebView; tokens persist to the iOS Keychain via the secureStore bridge.
- **Branch.** `mobile-app-rebuild` was forked from `birds-redesign`, so it carries that
  branch's birds + autonomy history as its base; do mobile work here, not on
  `birds-redesign` (which other agents still use for birds). Some birds files (e.g.
  `frontend/src/pages/apps/<slug>/LiveFeed.tsx`) had uncommitted WIP on the Linux box that is
  NOT on this branch. Stage only your own files; keep commits isolated.
- **Design system is law** for any `frontend/` or `mobile/` change: `frontend/CLAUDE.md`
  (primitives in `src/ui`, tokens, lucide icons not emoji, no em-dashes, run
  `scripts/check_design.sh` + `tsc --noEmit`).
- **Prod auth config** (when deploying beyond the tailnet): set `WORKSHOP_JWT_SECRET` (else a
  dev secret persists to `.runtime/jwt-secret`) and `WORKSHOP_REGISTRATION_OPEN=0` to close
  signups.

## Pointers

- Memory (on the Linux box, not in the repo): `project_workshop_mobile_app_plan`,
  `project_workshop_product_auth` — read these for the full decision history.
- Plan/why docs: `docs/mobile-app-implementation-plan.md`, `docs/mobile-app-proposal.md`,
  `mobile/README.md`.
