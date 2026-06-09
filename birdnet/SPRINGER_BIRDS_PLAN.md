# Springer Birds — standalone multi-account plan

Status: **proposal / for operator review** · Date: 2026-06-07 · Author: clawd-a

Goal (operator's words, 2026-06-07): take the existing Workshop birds app, give
it an accounts/auth layer, keep it the *same data and same app* but run it
**standalone** and **hardened** so it cannot reach (or be used to reach) the rest
of the personal system. Two users to start (operator + one invitee), invite-only,
admin-controlled. Cross-user devices with attribution and filtering. In-app
location, no OwnTracks for the second person. No chatbot in this build.

This supersedes Phase 4 of `docs/app-platform-plan.md` (which scoped Birds sharing
as a read-only guest link). The operator has upgraded the requirement to real
accounts that can also contribute detections.

The app's real name is **Springer Birds** (the README/BRINGUP "BirdNET Yard
Station" name was never agreed; rename is a cleanup task below).

---

## 0. The no-defensive-cruft rule does NOT block this

User-scope CLAUDE.md forbids multi-tenant guardrails *for the single operator's
own use*. This is the carve-out the platform plan already wrote down: Springer
Birds is **genuinely externally shared** (a second person, off the tailnet,
reachable from the internet). Accounts, invite gating, per-device ownership, and
default-deny isolation here are a **correctness and safety boundary**, not
single-user cruft. The guard against over-building is: only **two roles** (admin,
member), invites issued **out of the app** by the admin, and **no per-row ACLs
between the two trusted users** (they see each other's birds by design; filtering
is a view, not a permission wall).

---

## 1. What already exists (do not rebuild)

- **In-app geolocation already works.** `recorder.tsx` uses
  `navigator.geolocation.watchPosition` for adaptive devices and posts lat/lon per
  audio chunk to `/birds/analyze`; fixed devices pin a `location_id`. So location
  attaches to a detection when (a) the device is fixed to a place, or (b) the user
  granted browser location. **No OwnTracks dependency for anyone, including the
  second user.** OwnTracks stays operator-only and feeds the commute/location app,
  not birds.
- **Device + location model** in `backend/services/birds_analyze.py`:
  `devices(token,name,mode,fixed_location_id,...)`,
  `locations(id,name,lat,lon,address,radius_m,...)`,
  `browser_detections(...,device_token,location_id,lat,lon,status,...)`,
  plus species_area trust model and recordings. Device identity today is a
  `crypto.randomUUID` in `localStorage` with **no owner**.
- **Two data stores**:
  - `birdnet.db` — the always-on yard station (BirdNET-Go), read-only via
    `birds_store.py`. Full fidelity, no owner concept. This *is* the shared "home
    static" source both users should see.
  - `browser-stations.db` — per-device browser detections, read/write via
    `birds_analyze.py`. This is where per-user phones write.
- **Isolation analysis** already done in `docs/app-platform-plan.md` §2-4: today
  Birds and Finance share one origin and one unrestricted backend (loopback fully
  trusted). That shared origin/backend is the leak to fix before exposure.
- **Public sanitized page** (`public-site/` + `publish.sh` + R2) — anonymous
  stats-only, no audio/coords. This is a *separate* artifact and stays as-is. The
  standalone authenticated app below is the full app for the two trusted users; it
  is not the sanitized page.

---

## 2. Topology: a separate, hardened process on its own origin

The single most important hardening decision: **the public app is its own
process, importing only the birds code, exposed only via an outbound tunnel.**

```
                          internet
                             |
                   [Cloudflare Tunnel + Access]      <- authN gate, outbound-only
                             |  (cloudflared dials out; no inbound firewall hole)
                             v
[springer-birds service]  FastAPI on 127.0.0.1:8001
   imports ONLY: routes/birds.py + a new auth layer
   imports services.birds_store (RO yard DB) + services.birds_analyze (RW browser DB)
   does NOT import finance/agents/plaid/chat/budget — zero code path to them
   every request requires a valid user session (no loopback-trust shortcut)
             |                                  |
   read-only |                                  | read/write
             v                                  v
        birdnet.db (yard, shared "Home")   browser-stations.db (+ owner column)
```

- The existing Workshop `/apps/birds` page keeps working unchanged for the operator
  on the tailnet (full-trust, no login). The standalone service is the public face.
- Both processes read/write the **same** `browser-stations.db` and read the **same**
  `birdnet.db`. SQLite WAL + `busy_timeout` (already set) handles two writers. This
  is what makes it literally "the same data and same app."
- Blast radius of a Springer Birds compromise = bird data only. There is no import
  of, route to, or token for Finance/agents from this process. This is the concrete
  meaning of "unable to be hacked": even a full RCE in the bird app cannot read a
  bank balance or drive an agent, because that code and those creds are not in the
  process.

Backend shape: `backend/springer_app.py` (new) builds a small FastAPI app that
mounts the auth router + the birds router behind a `require_session` dependency.
Run as its own systemd user unit on :8001. Do **not** add it to `backend/main.py`.

---

## 3. Auth + exposure (the key decision — two coherent bundles)

### Bundle A (recommended): Cloudflare Tunnel + Cloudflare Access

- **Exposure**: `cloudflared` tunnel from the box to a hostname (e.g.
  `birds.<domain>`). Outbound-only, no inbound port, TLS + DDoS handled by CF. We
  already use Cloudflare (R2 for the public page).
- **AuthN**: Cloudflare Access in front of the hostname, email-OTP, with an
  **allowlist of exactly the two emails**. The app trusts the verified
  `Cf-Access-Authenticated-User-Email` header (validated against the Access JWT,
  not taken on faith).
- **Invites = admin edits the Access policy in the CF dashboard.** This matches the
  operator's spec exactly: only the admin issues invites, rarely, *out of the app*.
- **App side is tiny**: on first authenticated visit, auto-create a `users` row
  keyed by verified email; role from a 2-line config (`admin` = operator email,
  else `member`). No password handling, no session table, no signup flow to secure.
- Free tier (CF Zero Trust covers <=50 users). Most hardened, least code.
- Tradeoffs: login is an email OTP per session; identity is coupled to Cloudflare.

### Bundle B (fallback): app-native auth behind any tunnel

- **Exposure**: Cloudflare Tunnel or Tailscale Funnel (Funnel has no built-in
  auth, so it forces app-native auth anyway).
- **AuthN**: invite tokens minted by a CLI on the box (`scripts/springer_invite.py`
  prints a one-time signup URL — out of app, admin-only). Invitee sets name +
  password; password hashed with argon2id; server-side `sessions` table; cookies
  `HttpOnly; Secure; SameSite=Strict`. CSRF token on state-changing routes. Login
  rate-limit (legitimate here — this is a public login surface, not single-user
  cruft).
- Fully self-contained, no per-session email OTP, no Cloudflare coupling. More code
  to own and keep secure.

**Recommendation: Bundle A.** It is strictly less attack surface (no password DB,
no signup endpoint, no session store to leak), and "admin adds an email, rarely,
in the dashboard" is the operator's invite model verbatim. Pick B only if you want
zero Cloudflare dependency in the auth path.

Either way the app keeps a `users` table for **authZ + device ownership**; the
difference is only who performs authN.

---

## 4. Data model changes (shared `browser-stations.db`)

Additive, back-compatible (same `ALTER TABLE IF NOT EXISTS`-style migration the
module already uses). The private Workshop app keeps working; legacy rows get a
sensible owner.

```
users(id, email UNIQUE, name, role TEXT DEFAULT 'member', created_at)
invites(token, email, created_by, created_at, used_at, expires_at)   # Bundle B only
sessions(token, user_id, created_at, expires_at, user_agent)         # Bundle B only

devices: + owner_user_id INTEGER        # who owns this phone/device
         + shared INTEGER DEFAULT 0     # the home static station = 1
browser_detections: + owner_user_id INTEGER   # denormalized from device at insert
                                              # (fast owner filtering; NULL=legacy)
```

- **Legacy migration**: existing devices/detections get `owner_user_id` = the
  operator's user id (they are the operator's history). `shared` defaults 0.
- **The yard `birdnet.db`** has no owner column and needs none: it is the shared
  "Home" station by definition. In the unified view it renders as owner = "Home",
  always visible to both, never filtered out by an owner filter unless the user
  explicitly narrows to a single person.

---

## 5. Cross-user devices, attribution, filtering

- A browser device is **claimed by the session user on first use**: write routes
  (`/birds/analyze`, `/birds/device`) set/verify `owner_user_id = session.user`.
  You cannot post as someone else's device without their session, which closes the
  current "any token can post as any device" gap (the anti-spoof requirement).
- **Shared home device**: admin sets `shared=1` on the always-on station. Shared
  devices are readable by everyone and (optionally) recordable-to by anyone; their
  detections show owner = "Home".
- **Visibility is full across the two trusted members** (operator's spec: each
  person's devices + detections are visible to the other). The security boundary is
  logged-in vs not, and write-ownership — not read walls between members.
- **Filtering** is a view layer on the existing endpoints. Add an `owner` scope
  param resolved against the session, composable with the `location_id` / `device`
  / area filters that already exist:
  - `owner=me` -> my devices only
  - `owner=all` -> everyone + Home (default)
  - `owner=<user_id>` -> a specific person
  - `shared=1` -> Home station only
  - e.g. "just mine in this area" = `owner=me&location_id=N`; "both ours in an
    area" = `owner=all&location_id=N`.
  Implemented by joining `device.owner_user_id` (or the denormalized
  `browser_detections.owner_user_id`) in the summary/species/activity/bird
  queries, threaded as an optional `user_id`/`scope` arg into the `birds_analyze`
  functions so the private app and standalone app share one code path.

---

## 6. Location handling (resolved)

In-app browser geolocation is the mechanism for both users; nothing new is needed
for the second person. Detection gets a location when the device is fixed to a
place, or the user grants `watchPosition`. **OwnTracks is not integrated, required,
or offered to the second user** (operator-only, separate app). A device with no
fixed location and no granted GPS produces detections with no place (the existing
`needs_location` path already handles this).

---

## 7. No chatbot

The standalone mount excludes `ChatDock` and any `/chat` wiring entirely, even
though the Workshop birds page may have chat. The standalone process does not
import `chat.py` / `chat_context.py` at all (also reinforces §2 isolation: no chat
side-channel into other capabilities).

---

## 8. Hardening checklist ("can't be hacked")

- Separate process; imports only birds + auth. No finance/agent/plaid/chat code or
  creds in the process (primary control).
- Outbound-only tunnel; no inbound firewall hole.
- Every route requires a session; no loopback/IP trust on the public app.
- Device writes verified against the session user; no token-only posting.
- Yard DB opened read-only (`mode=ro`, already the case in `birds_store`).
- Audio clips are served only to authenticated members (full fidelity for the two
  trusted users); the anonymous public R2 page remains the only audio-free surface.
- Bundle A: no password/session store to leak. Bundle B: argon2id, HttpOnly+Secure
  +SameSite cookies, CSRF, login rate-limit, single-use time-boxed invite tokens.
- Standalone frontend bundle contains **only** the birds app + auth screens — no
  finance/agent code shipped to the browser.

---

## 9. Sequencing (each step independently shippable)

1. **Schema + ownership backfill** in `birds_analyze.py` (users table, device
   owner/shared columns, detection owner denorm, legacy backfill to operator).
   Thread an optional `user_id`/`scope` arg through the read functions. Private app
   behavior unchanged.
2. **Standalone backend** `springer_app.py`: auth layer (Bundle A or B) +
   `require_session` + birds router + device-ownership enforcement on writes. Runs
   on :8001 under a systemd user unit. Local-only first (no tunnel yet).
3. **Standalone frontend mount**: birds-only build with a login/landing screen and
   the owner filter control; no ChatDock. Reuses `@workshop/ui` primitives.
4. **Filtering UI**: owner scope selector (mine / both / Home / by person) wired to
   the new params; works with existing location/area filters.
5. **Exposure**: operator stands up `cloudflared` + (Bundle A) Cloudflare Access
   policy with the two emails, or (Bundle B) deploys invite CLI + funnel. Smoke
   test from off-tailnet.
6. **Rename cleanup**: "BirdNET Yard Station" -> "Springer Birds" in README,
   BRINGUP, frontend title, public-site.

Steps 1-4 are mine and need no egress/dashboard. Step 5 is operator dashboard +
interactive auth (cloudflared login, Access policy, or funnel) — I cannot do those.

---

## 10. Operator decisions needed

1. **Auth/exposure bundle: A (Cloudflare Access) or B (app-native password)?**
   Recommend A.
2. **Hostname/domain** for the public app (a Cloudflare-managed domain, or a
   `*.pages.dev`/tunnel-provided name?).
3. **Second user's email + name** (and confirm operator email = admin) — needed for
   the allowlist (A) or the first invite (B). Provide when ready.
4. **Can the second user record/contribute** (write detections from their phone),
   or **view-only** to start? The plan supports contribute; say if you want
   read-only first.
5. **Audio to the second user**: OK that an authenticated member hears clips
   (full-fidelity app), or restrict clips to admin?

---

## What I will NOT do without you

- `cloudflared` tunnel login / Cloudflare Access policy edits (dashboard +
  interactive auth).
- Tailscale Funnel enable (interactive).
- Anything touching `backend/services/birds_store.py` route boundaries or the
  finance/agent code.
- Issue real invites or hardcode the second user's identity (waiting on #3).
