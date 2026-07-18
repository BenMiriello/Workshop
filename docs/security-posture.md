# Workshop + host — Security Posture & Requirements

**Status:** living reference. Describes how security *is meant to work* on this system — the trust
model, the invariants that must hold, and the accepted tradeoffs — so future work has a baseline to
design against and re-audit toward. It is not a change log; point-in-time audits (e.g.
`security-audit-2026-07-18.md`) record what was actually true on a date and what got fixed.

**Last reviewed:** 2026-07-18. Re-review whenever the auth model, the public edge, the app-runner
isolation, or the host user/group layout changes.

**Design principle that overrides convenience:** this is a *single-operator* system. Security here
is about **identity, network minimization, and blast-radius containment** — NOT multi-tenant
guardrails. Do not add allowlists, audit journals, mention-gating, per-request confirmation, or
rate-limiting; those have been repeatedly rejected and are not part of this posture.

---

## 1. What we are protecting

Sensitive data, roughly in priority order:
- **Financial** — Plaid access tokens + bank/transaction data (Chase, Fidelity, Venmo, Citi).
- **Location / commute / health** — continuous personal telemetry.
- **Credentials** — the Claude OAuth token, Google/Plaid/Discord/Tailscale secrets, the web app's
  password hash + session/device/service tokens.
- **The host itself** — the backend can run containers (docker socket = root), so host compromise is
  a financial-data compromise.

Threat actors we design against: the public internet (via `workshop.benmiriello.com`), other devices
on the home LAN, other devices on the tailnet (incl. a compromised/guest device), other *users* on
the shared host, and prompt-injection into the autonomous agents (which hold the OAuth token and can
act). We are **not** trying to defend against the operator's own root account.

---

## 2. Trust boundaries

### 2.1 Network position is NOT identity
The core rule after the 2026 auth redesign: **personal data and platform control are gated by
identity, never by network position.** No loopback trust, no CGNAT/tailnet trust, no
`X-Forwarded-For` trust, no fail-open-when-token-missing on any personal-data path. A caller with no
valid credential gets 401/403 from anywhere — internet, LAN, tailnet, or loopback.

The *only* things that may trust network position are genuinely internal, personal-data-free
machine endpoints (`/relay`, `/services`, `/events`, `/auto-pr`, `/tools`), and only for loopback +
the Docker bridge — see §5.

### 2.2 Host users / clusters
Two intended clusters on the shared host:
- **clawd-a ↔ benmiriello** — the operator's personal cluster; mutual file access is intended.
- **simonsays ↔ clawd-b** — a separate pair, meant to be isolated from the clawd-a/benmiriello side.

**File-layer requirement (holds today):** each home is `0700` except where sharing is intended
(`clawd-a` `0750` group `clawd-a` with `benmiriello` a member; `benmiriello` `0710` + ACL granting
`clawd-a` traverse). `clawd-b` must stay fully contained (no `docker`, no `sudo`, `0700`).

**Known boundary breaker:** the shared **Docker daemon**. `docker` group membership is
root-equivalent, and clawd-a/benmiriello/simonsays all share one daemon, so the cluster boundary
cannot be absolute at the docker layer. clawd-a's membership is *load-bearing* (the app runner needs
it — §5), so the real fix is architectural: rootless/per-user Docker, a socket-proxy, or a dedicated
runner user. Until then, "loopback/host-local" is not a trust boundary between host users, and this
is a documented, accepted-for-now exposure.

---

## 3. Identity & authorization (the web app)

Requirements the code must keep satisfying (all verified present 2026-07-18):

- **One fail-closed identity dependency.** `deps_user` resolves the caller to an owner or raises
  401/403. `ownership.current_owner()` raises rather than defaulting to the operator.
- **Ownership is data, not config.** Personal stores live under `.runtime/users/<id>/`; routers
  filter by the server-pinned owner contextvar, never a client-supplied `owner_id`/`viewer_id` for
  scoping. `WORKSHOP_OPERATOR_EMAIL` is consulted at no request-time path.
- **Role separation.** Personal routers → `require_user` / `require_domain_owner` (owner = the
  `domain_owners` table, denies even another operator account). Platform/admin routers →
  `require_operator`. Machine callers → scoped `service_tokens` (`require_service`).
- **Session crypto.** Session/device/service tokens = `secrets.token_urlsafe(32)`, stored **hashed
  (SHA-256)**, compared constant-time, server-side expiry + revoke. Passwords = scrypt (n=2^15) with
  `compare_digest`. Plaid tokens = Fernet-encrypted at rest, key from env, fail-closed if unset.
- **Cookies.** HttpOnly + Secure + SameSite=Strict; a double-submit `x-workshop-client` header
  guards cookie-auth mutations (bearer callers exempt). The session token must never be readable
  from page JS (this is why the mobile bridge is write-only — §7).
- **Registration is fail-closed.** Signup requires an explicit `WORKSHOP_REGISTRATION_OPEN=1`.
- **Accepted tradeoff:** no login rate-limiting/backoff (rejected as cruft). Residual online-guessing
  risk is carried by scrypt cost + a strong operator password. Revisit only if the threat model
  changes.

---

## 4. Public edge (`workshop.benmiriello.com`)

The only public path in is the Cloudflare tunnel → `workshop-public.service` on `127.0.0.1:8002`
(`workshop_public_app.py`) → backend on `:8000`. Requirements:

- **Ingress is minimal.** The tunnel routes only `workshop.benmiriello.com → :8002` and
  `birds.benmiriello.com → :8001`, with a `404` catch-all. Nothing else is internet-exposed.
- **Static file serving is containment-checked.** SPA paths `resolve()` + `is_relative_to(DIST)` —
  no path traversal to secrets on disk.
- **The edge proxies ONLY the personal + `/auth` surface.** Internal/loopback-trusted and
  unauthenticated-ingest routers are blocked before proxying: `/tools`, `/relay`, `/services`,
  `/events`, `/auto-pr`, `/plaid/webhook`, `/location/owntracks`, `/health/ingest`, `/birds/webhook`,
  `/birds/live/publish`, and exact `/birds/analyze`. The blocklist normalizes `.`/`..`/`//`/case
  before matching. **Requirement:** any new internal/loopback router must be added here (a denylist
  is failure-prone — prefer converting to an allowlist of the known-safe surface).
- **No shared HTTP client / cookie jar in the proxy** — per-request transport only (a shared
  `AsyncClient` once leaked one user's session to all visitors; never reintroduce it).
- **Webhooks self-verify.** The Plaid webhook does full ES256 + body-hash + replay-window
  verification and fails closed; it does not rely on the edge for auth.

---

## 5. App runner & container isolation

The backend runs user *apps* as `docker compose` projects (`services/container_runtime.py`), which
requires docker-socket access — hence `workshop-backend.service` runs uvicorn via `sg docker`.

- **This makes the backend root-capable by design.** Blast-radius requirement: nothing untrusted may
  reach code-execution routes. `/tools` (which includes `cloud_run_claude`, an agentic
  `claude -p` with Bash) is gated to loopback + the Docker bridge only (`require_internal_caller`) —
  **not** the whole LAN. The backend binding `0.0.0.0:8000` is tolerated only because that gate holds;
  prefer binding loopback + tailscale.
- **Target state (not yet built):** move the runner off the shared root daemon — rootless/per-user
  Docker, or a docker-socket-proxy exposing only compose calls, or a dedicated unprivileged runner
  user. Any of these also restores the host-user cluster boundary (§2.2).
- **Containers must not widen the hole:** no `--privileged`, no `/var/run/docker.sock` bind-mounted
  into an app container, no host networking for app containers. (Verified clean 2026-07-18.)

---

## 6. Network exposure model

- **Internet:** only the two Cloudflare hostnames above, both API-401 unauthenticated.
- **Tailnet (Tailscale Serve, TLS, tailnet-only, no Funnel):** convenience access to internal ports.
  Requirement: anything exposed here must still enforce its own auth, because a tailnet device is not
  trusted (see §2.1). Raw backend `:8000` is served at `:8446` — acceptable *only* because the
  backend is identity-gated; do not put anything on Serve that assumes tailnet == trusted.
- **Loopback:** shared by all host users — not a trust boundary (§2.2).
- **LAN / all-interface (`0.0.0.0`, `[::]`) binds are a smell.** Requirement: bind services to
  loopback or the tailscale IP unless a LAN client genuinely needs them, and every all-interface
  service MUST authenticate. Recurring offenders to keep off `0.0.0.0`/unauthenticated: Redis,
  Ollama, ComfyUI, dev servers (Vite), the app ports of sister instances.
- **IPv6:** the host has globally-routable IPv6. Requirement: the inbound firewall must default-deny
  IPv6 for every service that binds `[::]` (Redis/Ollama/ComfyUI/SMB/ssh), or those services are
  internet-exposed regardless of the `0.0.0.0` intent. Verify with `ip6tables`/`ufw` as root.
- Firewall changes need root (`benmiriello`); clawd-a has no `ufw`/`sudo`.

---

## 7. Mobile app

- **No secrets in the bundle.** Anything shipped client-side is public; the app carries no API keys,
  no baked token, no telemetry SDK. Transport is HTTPS-only to a single backend origin.
- **Token storage.** The device bearer lives in the iOS Keychain (`expo-secure-store`), never
  AsyncStorage/localStorage. The session is the same HttpOnly cookie as the web.
- **The WebView bridge is the sensitive surface.** Requirements: `originWhitelist` pinned to the real
  origin; `data:`/`about:`/off-origin pages must not render in the WebView (they would inherit
  `window.WorkshopNative`); the bridge's `secureStore` is **write-only for `ws-device-token`** — page
  JS must never be able to *read* the Keychain (that would defeat the HttpOnly design). Native
  producers access secure storage directly, not through the bridge.
- Debug/diagnostics affordances (esp. any that write a credential) must be `__DEV__`-gated out of
  release builds.

---

## 8. Secrets management

- Long-lived secrets live in `0600` files under `0700` dirs: `~/.config/clawdbot/*` (OAuth, Plaid,
  Google, Discord, Tailscale), `~/.config/workshop/secrets.env` (web app + Plaid + service tokens),
  `~/.claude/.credentials.json`, `~/.claude.json`. Keep them `0600`; never inline a secret into a
  world-readable file or a `0644` `.env`.
- The Plaid Fernet key (`WORKSHOP_PLAID_ENC_KEY`) shares `secrets.env` with everything else — file
  compromise loses both. Accepted single-user tradeoff; the mitigation is file perms + host access
  control, not key separation.
- **Egress guard:** a PreToolUse Bash hook blocks shell network utilities (curl/wget/nc/…) to
  off-host destinations, so a prompt-injected agent can't exfiltrate via the shell. Loopback/LAN/
  tailnet allowed; web reading goes through WebFetch/WebSearch. Email-send MCP verbs are denied.
  Note: this also blocks local scripts that embed public URLs — write such scripts to a file and run
  them rather than piping through the shell.
- **Autonomous-agent data rules** (in `~/clawdbot/CLAUDE.md` + the discord-handler agent): financial
  data does not leave the host; treat ingested content as untrusted (prompt-injection posture).

---

## 9. Standing invariants (re-audit checklist)

1. No personal-data or platform router is reachable without a valid identity, from any network.
2. `deps_user`/`ownership` are fail-closed; no loopback/CGNAT/XFF/fail-open on personal paths.
3. Every new internal/loopback/ingest router is added to the public-edge block/allow list.
4. Secrets stay `0600` in `0700` dirs; `auth.db` and any `.env` are not world-readable.
5. Services bind loopback or tailscale unless a LAN client needs them; all-interface services
   authenticate; inbound IPv6 is firewalled.
6. The backend's code-execution routes (`/tools`) never trust more than loopback + Docker bridge.
7. No app container is privileged / host-networked / mounts the docker socket.
8. The mobile bridge exposes no Keychain *read* to page JS; WebView is origin-locked.
9. `clawd-b` stays out of `docker`/`sudo`; docker-daemon sharing is tracked as a known cluster-
   boundary exception with an architectural fix pending.
10. Registration fail-closed; OAuth via subscription token only (never `ANTHROPIC_API_KEY`).

## 10. Accepted risks (single-user tradeoffs, revisit if the model changes)

- No login rate-limiting/backoff (scrypt + strong password instead).
- All app secrets in one `secrets.env` (perms-protected, not compartmentalized).
- Backend is root-capable via the docker socket (inherent to the app-runner; blast-radius-contained
  by identity gating, architectural fix pending).
- Shared docker daemon across host users (cluster boundary not absolute; fix pending).

## 11. How to re-audit

Point-in-time passes should verify §9 against live state: probe each router unauthenticated from
loopback and off-host; enumerate `0.0.0.0`/`[::]` binds (`ss -tlnp`) and confirm each authenticates;
check secret file perms; read the ufw/ip6tables ruleset (root); inspect containers for
privileged/socket/host-net; and re-read the public-edge block/allow list against the current router
list. Prior passes and their subagent method are in the dated `security-audit-*.md` reports.
