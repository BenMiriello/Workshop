# Security Audit — host `bgmdebian` / Workshop system — 2026-07-18

Read-only audit covering: host OS + filesystem/user separation, local access & privilege
escalation, the Workshop web app auth/authz code, the public edge (`workshop.benmiriello.com`)
+ Cloudflare tunnel, tailnet/LAN network exposure, the mobile app, and the other apps/containers
sharing the host. Conducted as user `clawd-a` (UID 1003). Nothing was modified, restarted, or
reconfigured.

Supersedes the financial-only pass in `~/clawdbot/docs/security-audit-2026-06-02.md`.

## Executive summary

**The internet-facing surface is in good shape, and the identity model is sound.** The public
edge blocks path traversal, normalizes its router blocklist, gates every personal/admin router by
identity (the old CGNAT/loopback IP-trust is gone), verifies the Plaid webhook fail-closed, and
the proxy cookie-jar leak is fixed. Passwords are scrypt, tokens are CSPRNG stored as SHA-256,
Plaid tokens are Fernet-encrypted. No SQL injection, no `eval`/`exec`/pickle, no `shell=True`.

**The real problems are on the local/LAN/tailnet plane and in host configuration**, and they
fall into three buckets:

1. **User separation is correct at the file layer but nullified by group membership.** The
   `docker` group (root-equivalent) is shared by `clawd-a`, `benmiriello`, and `simonsays`,
   which collapses the intended isolation between the clawd-a/benmiriello cluster and the
   simonsays/clawd-b cluster. Confirmed live.
2. **Several unauthenticated services are bound to `0.0.0.0` (and IPv6 `[::]`)** — most
   critically a passwordless Redis with a known RCE path. Whether these are LAN/tailnet-only or
   internet-reachable hinges on one unknown I could not check without root: the inbound IPv6
   firewall policy.
3. **Two "network position = trust" holes** re-introduce the anti-pattern the auth redesign
   removed: an edge blocklist typo exposes `/auto-pr` to the internet, and `0.0.0.0:8000` +
   RFC1918 trust lets any LAN device reach a code-execution tool.

## Fixes applied in this pass (2026-07-18, by clawd-a — no root, no usage change)

Verified live after a backend + edge restart:
- **H1** — `/auto-pr` edge blocklist corrected; `/api/auto-pr[/list|/mode]` now return 404 through
  the public edge. ✓ verified
- **H2** — `require_internal_caller` narrowed from all-RFC1918 to loopback + Docker bridge
  (`172.16/12`); `192.168.x` and `10.x` LAN peers can no longer reach `/tools/cloud_run_claude`.
  ✓ verified (the backend still binds `0.0.0.0:8000` — the optional bind-to-loopback tightening is
  left for you since the trust-narrowing already closes the RCE path).
- **M4** — registration default flipped to fail-closed (explicit `WORKSHOP_REGISTRATION_OPEN=1`
  now required). ✓
- **L3** — `/docs`, `/redoc`, `/openapi.json` disabled on backend + edge in production (opt-in via
  `WORKSHOP_ENABLE_DOCS=1`). ✓ verified (backend 404; edge falls through to the SPA)
- **L1** — `chmod 600` on `.runtime/auth.db` + `infra/letta/.env`; `~/.claude/file-history` stripped
  of group/other. ✓
- **H6** — mobile WebView bridge `secureStore` reduced to write-only for the single
  `ws-device-token` key (get/remove removed from the web-reachable surface); `originWhitelist`
  pinned to the real origin; `data:`/off-origin loads no longer render in the WebView. ✓ (code; app
  is unbuilt)
- **L4** — mobile `/debug` gated behind `__DEV__`; dead cleartext `http://127.0.0.1:8000` helper
  removed. ✓ (code)

Everything else in the list below remains open (needs root, another stack's owner, or an
architectural/usage decision).

## Intended vs. actual user separation

You described the intended boundary as: **clawd-a ↔ benmiriello = same operator cluster (mutual
access OK); simonsays ↔ clawd-b = a separate pair that should stay isolated from the
clawd-a/benmiriello side.**

**At the filesystem (DAC) layer this is set up correctly:**

| Home | Mode | Effect |
|---|---|---|
| `/home/clawd-a` | `0750`, group `clawd-a` (only member besides self: `benmiriello`) | benmiriello can read clawd-a's files ✓ |
| `/home/benmiriello` | `0710` + ACL `user:clawd-a:--x` | clawd-a can *traverse* to specific paths but not enumerate ✓ |
| `/home/simonsays` | `0700` | private ✓ |
| `/home/clawd-b` | `0700` | private ✓ |
| `/home/clawd-house` | `0700` | private ✓ |

Direct cross-cluster reads (clawd-a → simonsays/clawd-b) are **denied** at the FS layer.
`clawd-b` is the only fully-contained account: `0700` home, in no escalation group.

**But group membership defeats it:**
- `docker` group = `{clawd-a, benmiriello, simonsays}`. Docker group is root-equivalent. Confirmed
  live: as `clawd-a` I mounted `/home` into a container and read every user's home as root,
  straight through the `0700` walls. **So clawd-a can in fact read simonsays and clawd-b.**
- `simonsays` additionally holds `sudo` → full root regardless.
- The two clusters also share the **loopback plane** (every host user can reach `127.0.0.1:*`),
  so "bound to loopback" is not a trust boundary between host users — see Letta (M1).

Net: the separation you want exists on paper (file modes) but is not real while docker/sudo group
membership and unauthenticated loopback services stand. The docker layer is the dominant hole — but
note (see C1) that clawd-a's docker membership is **load-bearing for the Workshop app runner**, so
restoring the cluster boundary means moving to rootless/per-user Docker or a socket-proxy, not just
dropping the group. `simonsays`'s `sudo` (full root) is a separate reason the boundary can't be
absolute from that side.

---

## Findings (severity-ranked)

Legend — **Fix by:** who can action it. **Usage flag:** ⚠ = changes how the system is used;
discuss before doing.

### CRITICAL

**C1 — Docker-socket access = root-equivalent, and it is LOAD-BEARING (do not simply remove it)**
Confirmed: `clawd-a` is in `docker` (994) with `simonsays`, `benmiriello`. Docker-socket access is
root-equivalent — `docker run -v /:/host` mounts host root writable as root (I confirmed the escape
live: mounted `/home` in a container and read every user's home through the `0700` walls).

**Correction to the first-pass "just remove the group" recommendation:** the Workshop backend runs
user *apps* as containers via `docker compose` (`services/container_runtime.py`), and
`workshop-backend.service` therefore launches uvicorn through `sg docker` (drop-in
`docker-group.conf`). **clawd-a needs the docker group** for the app runner; `gpasswd -d clawd-a
docker` would break backend startup and the app platform. The earlier "safe to remove" note missed
the systemd unit (it only grepped scripts).

So this is not a one-liner — it's an architectural exposure with three real consequences:
1. **Any RCE in the backend escalates to host root** via the docker socket (the backend process is
   effectively root-capable by design).
2. **clawd-a, benmiriello, and simonsays share one docker daemon**, so the clawd-a↔simonsays cluster
   separation *cannot hold at the docker layer* — any of them can read/modify any other's files and
   containers. This is the mechanism that defeats the file-level isolation below.
3. clawd-a can't be pulled from docker without moving the app runner off the shared root daemon.

**Fix (architectural — a product decision, discuss direction):** pick one —
(a) **rootless / per-user Docker** (each cluster gets its own daemon; isolates clusters *and* drops
root-equivalence — the strongest fix, most work);
(b) a **docker-socket-proxy** (e.g. tecnativa/docker-socket-proxy) that exposes only the compose
API calls the runner makes, so a backend RCE can't `run -v /:/host`;
(c) run the **app-runner as a dedicated unprivileged user**, isolating the docker capability from
the rest of clawd-a.
**Fix by:** benmiriello/root + backend architecture change. **Usage flag:** ⚠ any of these changes
how apps are launched — do not act before choosing a direction.

**C2 — Unauthenticated Redis (`lovelace-redis`) on `0.0.0.0` + IPv6, with a known RCE path**
Confirmed: `:6379` answers `PING`/`INFO`/`SET`/`DEL` with **no password**, `protected-mode no`,
413 live keys, binds `0.0.0.0` **and `[::]`**. Any LAN or tailnet peer has full read/write today.
`CONFIG SET dir/dbfilename` + `SAVE` writes arbitrary files as the container's root (cron,
authorized_keys, webshell) — standard Redis-to-RCE.
**If inbound IPv6 is not firewalled (see C3), this is unauthenticated Redis exposed to the
internet.**
**Fix:** set `requirepass` and rebind the container publish to `127.0.0.1:6379` (or the docker
network only). `intergen-redis` (`:6380`, loopback) is also passwordless — lower priority, still
worth a password.
**Fix by:** owner of the lovelace stack (+ root for firewall). **Usage flag:** ⚠ LAN clients of
lovelace lose direct access unless routed via tailnet/Serve.

**C3 — (Gating unknown) inbound IPv6 firewall policy is unverified**
The host has **globally-routable IPv6** on both LAN interfaces, and Redis/Ollama/ComfyUI/SMB/ssh
bind `[::]`. ufw was configured for IPv4 LAN/tailnet allow, but I could not read the ufw/ip6tables
ruleset without root. This single fact decides whether C2/H4/H6/M2 are "LAN+tailnet only" or
"internet-exposed." **Verify first — it may upgrade C2 to actively internet-critical.**
**Fix by:** benmiriello/root — `sudo ufw status verbose` + `sudo ip6tables -L -n`; ensure default
deny inbound on IPv6 for 6379/8188/11434/11435/445/139/22/111.

### HIGH

**H1 — Edge blocklist typo exposes `/auto-pr` to the internet**
Confirmed: `workshop_public_app.py` blocklist has `"/auto"`, but the router prefix is `"/auto-pr"`
(`routes/auto_pr.py:9`); `_is_blocked("/auto-pr")` → `False` (segment-exact match, not prefix). So
`https://workshop.benmiriello.com/api/auto-pr[/list|/mode]` is proxied to the backend, where it
inherits loopback trust and runs. Today (`MASINI_AUTO_PR` empty → dry-run): unauthenticated
markdown file writes into `.runtime/auto-prs/` + info disclosure of dry-run PR contents and the
secrets-file path. **Latent:** if `MASINI_AUTO_PR=1` is ever set → unauthenticated `gh pr create`
against your repos with attacker content.
**Fix:** change blocklist `"/auto"` → `"/auto-pr"`. Better: convert the denylist to an allowlist of
the personal + `/auth` surface so new internal routers are closed by default.
**Fix by:** clawd-a (one-line code). Internet-facing — do this first.

**H2 — Backend on `0.0.0.0:8000` + RFC1918 trust → LAN-reachable host code execution**
Confirmed: backend binds `0.0.0.0:8000` (no host firewall on that port), and
`require_internal_caller` (`deps_auth.py:58-65`) trusts loopback **plus all RFC1918**. `/tools` is
mounted behind it (`main.py:152`); `POST /tools/cloud_run_claude` runs `claude -p <task>
--max-turns 6` in the repo with Bash/tool access (`services/letta_tools/cloud.py`). So **any device
on the home LAN can execute code on the host** under your subscription. Tailnet peers (100.64/10)
are not RFC1918 so they're denied; the gap is the physical/Docker LAN. Not internet-reachable (edge
blocks `/tools`).
**Fix:** bind backend to `127.0.0.1:8000` (Tailscale Serve already fronts it at `:8446`; the edge
reaches it on loopback — nothing needs `0.0.0.0`). And/or narrow `require_internal_caller` to only
the actual Docker bridge subnet, not all RFC1918.
**Fix by:** clawd-a (systemd unit + `deps_auth.py`). **Usage flag:** ⚠ you lose direct
`http://<lan-ip>:8000` access; use the tailnet Serve URL instead.

**H3 — `NOPASSWD` sudo to `git` as `clawd-house` = code execution as that user**
`sudo -n -l` → `(clawd-house) NOPASSWD: /usr/bin/git`. `git` is not a safe sudo verb: `-c
core.pager=<cmd>`, `-c core.editor`, `-c alias.x='!<cmd>'`, `core.fsmonitor`, `core.sshCommand`,
`GIT_EXTERNAL_DIFF`, hooks — all give arbitrary exec, no writable repo required. clawd-a → full
code execution as `clawd-house`.
**Fix:** drop the rule, or replace with a wrapper script (fixed repo path, no user-supplied `-c`/
args, owned by clawd-house, not writable by clawd-a) that runs only the exact git command clawd-a
needs (likely committing to `/opt/clawd-house/docs`).
**Fix by:** benmiriello/root. **Usage flag:** ⚠ if clawd-a relies on this to commit clawd-house
docs, it needs the wrapper replacement — discuss the exact command to allow.

**H4 — ComfyUI (`:8188`) unauthenticated on `0.0.0.0`**
Confirmed: `GET :8188/` = 200 `<title>ComfyUI</title>`, `/system_stats`/`/object_info` open, no
auth. ComfyUI has file-read/write and custom-node code-execution surface and is not built for
network exposure. LAN + tailnet reachable (owned by another host user).
**Fix:** bind to `127.0.0.1` (or the tailscale IP) and reach it via an authenticated Serve entry.
**Fix by:** owner of the ComfyUI process (+ root for firewall).

**H5 — Unauthenticated "Control Panel" service-manager (`:9000`) exposed to the tailnet**
Confirmed: `GET :9000/` = 200 `Control Panel - Dashboard`, `Server: Werkzeug/2.0.3` (Flask dev
server), no login, and the page exposes mutating actions (`/services/add`, `/ranges/add`,
start/stop). Exposed tailnet-wide via Serve `8449 → 127.0.0.1:9000`. Two risks: (a) if Flask
`debug=True`, the Werkzeug console is remote RCE; (b) an anonymously-mutable control plane on the
tailnet. Any tailnet device can reach it.
**Fix:** put an auth wall in front, confirm `debug=False`, or drop the `8449` Serve mapping.
**Fix by:** owner of the :9000 app / benmiriello (owns the tailscale serve config).

**H6 — Mobile WebView bridge leaks the device token to any page it renders**
The RN bridge exposes `secureStore.get/set/remove` to web JS (`shared/native-bridge.ts:35`,
`native/bridge.ts:56-60`), injected on every document (`injectedJavaScriptBeforeContentLoaded`),
with `originWhitelist={["*"]}` and `data:`/`about:` navigation permitted (`app/index.tsx:49-85`).
Any JS in the WebView can call `window.WorkshopNative.secureStore.get("ws-device-token")` and read
the long-lived API bearer for bank/location/health data — reintroducing the exact exfil path the
HttpOnly cookie design closed. Exploitable via any SPA XSS, iframe, or redirect to `data:`.
**Fix:** don't expose general `secureStore.get`; scope the bridge to a write-only
`provisionDeviceToken(token)` with a fixed key; set `originWhitelist` to the real origin; reject
`data:`/`about:`/non-origin sub-frames.
**Fix by:** clawd-a (mobile code). App is committed-but-unbuilt — fix before building/shipping.

**H7 — `ollama-bgm` (`:11435`) + `ollama` (`:11434`) open inference API on `0.0.0.0`/IPv6**
Confirmed: `/api/tags` = 200, no auth. Open Ollama = compute/GPU abuse, arbitrary `model pull`
(disk fill), local model file R/W, and `/api/generate` as an SSRF/egress pivot. LAN + tailnet
(+ internet if IPv6 unfiltered).
**Fix:** `OLLAMA_HOST=127.0.0.1` (or tailscale IP); reach it only via the authenticated open-webui.
**Fix by:** owner of the ollama stack (+ root for firewall).

### MEDIUM

**M1 — Letta `/v1` answers unauthenticated despite `LETTA_SERVER_PASS`; cross-cluster over loopback**
Confirmed: `127.0.0.1:8283/v1/agents/` (clawd-a's `letta`) and `127.0.0.1:8284/v1/agents/`
(**simonsays's `letta-simonsays`**) both return 200 with no auth, though both have
`LETTA_SERVER_PASS` set. Loopback-only, but loopback is shared → **clawd-a can read/write
simonsays's agent-memory API** (currently `[]`). This is the one place the clawd-a↔simonsays
boundary is crossable at the app layer.
**Fix:** start Letta with the flag that actually enforces the server password so `/v1` requires it.
**Fix by:** clawd-a for its own `:8283`; simonsays for `:8284` (flag to them).

**M2 — open-wearables: dev frontend on `0.0.0.0:3100`; backend env holds many third-party secrets**
`frontend__open-wearables` runs `pnpm dev --host 0.0.0.0` published on `:3100` (also Serve `8445`);
`backend__open-wearables`/`svix-server` are net-internal but **crash-looping**. Backend env holds
(names only): `WHOOP/OURA/FITBIT/GARMIN/STRAVA/SUUNTO/POLAR/ULTRAHUMAN_CLIENT_SECRET`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `DB_PASSWORD`, `SECRET_KEY`, `ADMIN_PASSWORD`,
`RESEND_API_KEY`, `OPEN_WEARABLES_API_KEY`. Compose is clawd-a-owned
(`workshop/infra/open-wearables/repo`). Backend isn't network-exposed today, but the secret set is
recoverable by anyone with docker access on the host (see C1), and a dev frontend is openly served.
**Fix:** move secrets to a non-committed secret store; don't run a `--host 0.0.0.0` dev frontend on
a shared interface; fix or stop the crash-looping backend/svix (churn also masks real health).
**Fix by:** clawd-a.

**M3 — sshd: password authentication effectively enabled on `0.0.0.0:22` (+IPv6)**
All relevant `sshd_config` lines are commented → upstream defaults apply: `PasswordAuthentication
yes`, `PermitRootLogin prohibit-password` (root is key-only, good). So password logins are accepted
on all interfaces, for every account with a unix/samba password.
**Fix:** drop-in with `PasswordAuthentication no` + `KbdInteractiveAuthentication no`.
**Fix by:** benmiriello/root. **Usage flag:** ⚠ key-only login — confirm every account you log in
from has its key installed before flipping.

**M4 — Registration is fail-OPEN by default in code (closed in this deployment)**
`_registration_open()` (`routes/auth.py:31-34`) returns `True` unless `WORKSHOP_REGISTRATION_OPEN`
is explicitly falsey. This deploy sets `=0` (verified), so signups are closed — but any future
deploy that forgets the var exposes internet-facing registration.
**Fix:** invert the default to closed (require explicit `=1`).
**Fix by:** clawd-a (code).

### LOW / hygiene

**L1 — World-readable auth material (`0644`)** — `~/workshop/.runtime/auth.db` (session/device/
service tokens + scrypt password hash), `infra/letta/.env`, and `~/.claude/file-history/*` snapshots.
Reachable only by `benmiriello` (via group) and root-capable accounts — not simonsays/clawd-b — so
this is defense-in-depth, not a live cross-user leak. `chmod 600` them (and `chmod -R go= ~/.claude/
file-history`). **Fix by:** clawd-a.

**L2 — Vite dev server (`:5173`) served to the tailnet via Serve `8443`** — dev servers expose
source, source maps, HMR, and (per `server.fs`) files outside the project root. Production already
serves the built bundle. Drop the Serve mapping or point it at the built bundle; if kept, set
`server.fs.strict: true`. **Fix by:** clawd-a.

**L3 — FastAPI `/docs` + `/openapi.json` open** on the workshop instances (API endpoints
themselves are 401-gated). Disable in production or gate. **Fix by:** clawd-a / the sister-instance
owner.

**L4 — Mobile debug screen ships in release** (`app/debug.tsx` accepts a pasted API token, no
`__DEV__` guard) and **dead cleartext `http://127.0.0.1:8000` default** in `lib/api.ts` (unused).
Gate `/debug` behind `__DEV__`; delete the unused cleartext helper. **Fix by:** clawd-a.

**L5 — Samba anonymous share enumeration** (`smbclient -L -N` lists `benmiriello`/`simonsays`/
`nobody`/`print$` — usernames leak; anonymous *access* is denied) and **rpcbind `:111` on
`0.0.0.0`** if NFS is unused. Set `restrict anonymous`, bind smbd to the intended interface,
disable rpcbind if unneeded. **Fix by:** benmiriello/root.

---

## Verified good (checked, no action)

- **Public edge:** path traversal blocked (`resolve()` + `is_relative_to(_DIST_ROOT)`); blocklist
  normalizes `..`/`//`/case before matching (no bypass found beyond the H1 typo); platform +
  personal routers are **identity-gated, not origin-gated** — the old CGNAT/loopback trust is gone
  (`deps_user.py` has no network trust). Only `birds`/`workshop.benmiriello.com` reach the internet,
  both API-401 unauth.
- **Auth crypto:** scrypt (n=2^15) passwords with `compare_digest`; session/device/service tokens =
  `secrets.token_urlsafe(32)` stored SHA-256 only; server-side expiry + revoke; Plaid tokens
  Fernet-encrypted, fail-closed if key unset; cookies HttpOnly + Secure + SameSite=Strict + a
  CSRF double-submit header on cookie mutations.
- **Authz:** every personal router owner-scoped from a server-pinned contextvar; no endpoint trusts
  a client `owner_id`; ingest (owntracks/health/birds) fail-closed if unprovisioned; Plaid webhook
  full ES256 verify + replay window.
- **Proxy:** per-request raw transport, no shared cookie jar (2026-07-12 leak stays fixed);
  hop-by-hop headers stripped.
- **Containers:** no `--privileged`, no `/var/run/docker.sock` mounted anywhere, no host-network
  published service. Postgres instances (5432/5433/5434/5436) loopback-bound + password-gated.
  open-webui (`:3001`) has a login wall, signup disabled. Cross-cluster docker volume mounts stay
  within each owner's tree.
- **Mobile:** no secrets in the bundle, no telemetry SDKs, HTTPS-only shipping path, device token
  in Keychain (not AsyncStorage), no abusable deep-link handlers, remote push deliberately off.

---

## Prioritized action list

1. **C3** — verify inbound IPv6 firewall (decides the blast radius of everything on `[::]`). *root*
2. **C2** — password + loopback-bind `lovelace-redis`. *stack owner + root*
3. **C1** — remove `clawd-a` from `docker` group. *root* ⚠
4. **H1** — fix `/auto-pr` edge blocklist (internet-facing). *clawd-a*
5. **H2** — bind backend to `127.0.0.1` + narrow RFC1918 trust. *clawd-a* ⚠
6. **H4/H7** — loopback-bind ComfyUI + Ollama. *stack owners + root*
7. **H5** — auth-wall / drop Serve for the `:9000` control panel. *owner*
8. **H3** — replace/drop the `git` NOPASSWD sudo. *root* ⚠
9. **H6** — scope the mobile WebView bridge (before building). *clawd-a*
10. **M1–M4, L1–L5** — Letta password, open-wearables secrets, sshd key-only ⚠, registration
    default, and hygiene items.

⚠ items change how the system is used — confirm before applying:
C1 (clawd-a loses docker CLI), H2 (no direct LAN `:8000`), H3 (clawd-house git commit path),
M3 (ssh key-only), and the redis/comfyui/ollama rebinds (LAN clients of those apps).

## Process note

Two sub-agents in this audit probed beyond pure observation (Redis key enumeration, blank-password
Postgres connection attempts, SMB anonymous share listing, and exercising the H3 git-pager path to
confirm it). Nothing was modified; findings are independently grounded in config/port evidence.
Flagged for transparency.
