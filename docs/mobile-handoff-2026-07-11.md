# Mobile iOS bring-up — handoff (2026-07-11)

Resume doc for continuing the Workshop iOS app bring-up on the operator's Mac.
Supersedes the auth section of `docs/mobile-handoff-2026-06-25.md` (that one
predates the cookie/device-token auth redesign).

## TL;DR — where we are

Bringing up the Workshop iOS app (Expo + WebView shell in `mobile/`) on the
operator's Mac. **Every JS/tooling hurdle is cleared**; we're at the Xcode
build + install-to-device step. Last blocker: `expo run:ios --device` won't see
the connected iPhone even though `xctrace` does — so we're pivoting to building
straight from Xcode. Possible iOS-26.5-vs-Xcode-version gap to resolve.

## Who runs what (two machines, one branch)

- **Linux box** (this repo, where the agent works): runs the backend
  (`workshop-backend.service` :8000), the public edge (`workshop-public` :8002),
  and `cloudflared`. The agent commits + pushes from here. It **cannot** run
  Mac/Xcode commands, and its shell egress guard blocks external hosts (so no
  `npm install` against the registry here) — but `git push`/`pull` to GitHub work.
- **Mac** (`<mac-checkout>`): builds the iOS client only. The operator
  runs Xcode / expo / npm / pod here.
- **Sync**: both are clones on branch `mobile-app-rebuild`; GitHub is the hub.
  `git config pull.rebase true` is set on both. Habit: `git pull --rebase` before
  `git push`. Overlap is rare (agent touches backend/frontend, operator touches
  `mobile/`), so conflicts are unlikely; if a push is rejected, `git pull --rebase`
  then push.

## What's done on the Mac (bring-up log)

1. **Ruby**: system Ruby 2.6 is too old for CocoaPods (ffi needs ≥3.0) and can't
   be updated in place (SIP, EOL). Installed a real Ruby via `rbenv`:
   `brew install rbenv ruby-build && rbenv install 3.3.5 && rbenv global 3.3.5`
   (+ `eval "$(rbenv init - zsh)"` in `your shell rc file`).
2. **Homebrew perms**: `/opt/homebrew/share` wasn't writable →
   `sudo chown -R $(whoami) /opt/homebrew/share`.
3. **CocoaPods 1.17.0**: `gem install cocoapods` under the rbenv Ruby.
4. **JS deps**: `npm ci --ignore-scripts` — `react-native-screens` ships a broken
   `prepare` (`bob build && husky install`) that fails for consumers; `--ignore-scripts`
   skips it (the native code still builds via CocoaPods).
5. **Version alignment**: `npx expo install --fix` bumped to SDK-51 exact versions:
   `react-native` 0.74.0→**0.74.5**, `react-native-screens` 3.31.0→3.31.1,
   `react-native-safe-area-context` 4.10.0→4.10.5. This fixed the Podfile error
   `unknown keyword: :privacy_file_aggregation_enabled` (that keyword exists in
   0.74.5's pods helper, not 0.74.0's). Committed as `a12541e`.
6. **Prebuild + pods clean**: `rm -rf ios && npx expo prebuild` + `pod install` now
   succeed.

## Current blocker + the next move

- `xcrun xctrace list devices` shows **BGM iPhone (26.5)**
  (UDID `00008140-000C14160062201C`), but `npx expo run:ios --device` returns
  `No iOS devices available in Simulator.app` — expo CLI device-detection flakiness.
- **Next: build from Xcode directly** (pods are installed):
  ```bash
  cd <mac-checkout>/mobile
  open ios/*.xcworkspace
  ```
  In Xcode: pick **BGM iPhone** as the run destination → select the app target →
  **Signing & Capabilities** → Automatically manage signing → **Team = Personal
  Team** (Apple ID already added to Xcode) → set a unique bundle id if it complains
  → **▶ Run**.
- Diagnostics to capture: `xcodebuild -version` and `xcrun devicectl list devices`.
- **Watch the iOS/Xcode gap**: the phone is on **iOS 26.5**. If the installed Xcode
  is older than that iOS, it cannot deploy ("iOS 26.5 not supported by this version
  of Xcode" / missing Device Support) → update Xcode via the App Store /
  developer.apple.com. This is the most likely next real error.
- Already done: Developer Mode ON on the iPhone; Apple ID added to Xcode.

## Mobile config facts (current state of the code)

- **Single backend origin, no host committed.** `mobile/native/config.ts` resolves:
  `EXPO_PUBLIC_WORKSHOP_URL` (env; put in `mobile/.env.local`, gitignored) >
  `app.json` `extra.serverUrl` > default `https://workshop.<your-domain>`.
  `/api` is proxied to the backend at every origin (public edge in prod, vite in
  dev), so one URL serves both the WebView and the native producers (which POST
  under `/api/...`). Default = public deployment, so the phone doesn't even need
  Tailscale; set `EXPO_PUBLIC_WORKSHOP_URL=https://<host>.<tailnet>:8443`
  in `.env.local` to use the tailnet instead.
- **Auth**: the WebView logs in via the session cookie (same-origin), the frontend
  provisions a revocable device token, and the native producers (location/audio)
  send it as a Bearer. `mobile/native/auth.ts` key = `workshop.ws-device-token`.
  **This on-device handshake has never run on a real phone** — if background
  location posts return 401 on device, look here first.
- Operator account: **<operator-email>** — register/login at the login wall on
  first launch.
- `tier: "free"` ($99 Apple Developer Program / remote push is a deferred seam).
- Live Activity: an optional one-time Xcode target step (`plugins/withLiveActivity.js`
  regenerates on prebuild); it no-ops until then, nothing else depends on it.

## Server side (verified ready for the app)

`workshop-backend` (:8000), `workshop-public` edge (:8002), and `cloudflared` are
all active. Through the edge: SPA `200`, `/api/auth/me` `401`, login endpoint live,
`/api/auth/device-token` `401` unauth, `/api/<app>/update` `401` unauth. The
login wall is live; the app will load it on launch.

## Broader Workshop state (context, not blocking mobile)

- **Auth + ownership redesign LIVE** — per-user data, fail-closed, no network
  trust. An adversarial Opus review found + fixed a CRITICAL public-edge path
  traversal, then edge/webhook hardening. See `docs/auth-redesign/`.
- **Per-user Plaid** self-contained in Workshop (`services/finance_plaid.py` +
  `services/plaid_store.py`, tokens Fernet-encrypted); operator's 4 banks migrated;
  finance/budget are per-user.
- **Context meters**: circular ring in the in-app chat (real count via
  `GET /chat/threads/{id}/context`) + a Claude Code CLI status line
  (`~/.claude/statusline-context.py`, renders `Context: ██░░ N%`).
- **Voice mode** built 2026-07-10 (background work): `/voice` routes, `VoiceMode.tsx`,
  local STT/TTS. See `docs/voice-mode/`.

## Open decisions (operator's call — not started)

- **Login backoff / 2FA** on the public login — OPEN. The adversarial review's one
  residual risk (online password guessing). Brushes the no-rate-limiting rule, so
  left for the operator to approve.
- **Secret rotation** — DECIDED: **no** (operator said don't rotate).
- **Plaid OAuth `redirect_uri` wiring** — deferred; only needed to link *new*
  OAuth banks (Chase-type) in-app. Existing banks work. Requires a hosted resume
  page + registering the URL in the Plaid dashboard (Developers → API → Allowed
  redirect URIs).

## Resume checklist (Mac)

```bash
cd <mac-checkout>/mobile
git pull --rebase
open ios/*.xcworkspace          # build from Xcode; pick BGM iPhone; set Team; Run
# if ios/ is missing/stale:  rm -rf ios && npx expo prebuild -p ios
```
