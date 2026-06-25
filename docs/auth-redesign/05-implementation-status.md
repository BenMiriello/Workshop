# Implementation status — IMPLEMENTED + live-verified 2026-06-25

Phases 0–4 are built, tested, and running live on the tailnet backend. Phase 5
(public deployment) is deployed on this box — the public edge is enabled and
running on :8002, registration is closed, the cloudflared ingress is configured —
and the only remaining action is the operator running one egress-blocked DNS
command to publish `workshop.benmiriello.com` (see "The one step left" below).
See `04-critique-and-refinements.md` for the design deltas this implements.

## What is live now (tailnet backend :8000, restarted onto the new code)

- **Authentication** is opaque server-side sessions in an HttpOnly + Secure +
  SameSite=Strict cookie (browser/WebView) and revocable per-device tokens
  (native producers). One fail-closed `current_user` dependency: cookie → device
  token → service token → 401. **No loopback/CGNAT/JWT/fail-open trust anywhere.**
  (`backend/deps_user.py`, `services/auth_store.py`, `routes/auth.py`.)
- **Ownership is enforced every request.** Per-user stores (location, health,
  push, chat) are physically partitioned under `.runtime/users/<owner_id>/` via a
  fail-closed owner contextvar (`services/ownership.py`). Single-dataset domains
  (finance, budget, commute) are gated by `require_domain_owner`, where the owner
  is recorded DATA (`auth_store.domain_owners`), not the `is_operator` role.
- **`is_operator` grants platform/admin only**, never another user's personal
  data. `WORKSHOP_OPERATOR_EMAIL` is consulted at NO request-time path (only the
  one-time migration; enforced by a committed test).
- **Internal callers**: the commute cron sends an operator-owned
  `automation:commute` service token; internal infra (relay/services/events) is
  loopback-only; the Letta tool sandbox is internal-network-only.

## Live red-team results (run against :8000 from loopback)

| Check | Result |
|---|---|
| Unauthenticated GET finance/budget/location/health/commute/chat/agents/birds/push | **401** (loopback grants nothing) |
| Garbage bearer / forged cookie | **401** (never fail-open) |
| Second member account → operator's finance/budget/commute | **403** |
| Second member account → its own location/chat | **200, empty** (own partition) |
| Second member account → platform (/agents) | **403** |
| Operator-owned commute service token → /commute | **200** |
| Non-owner service token → operator domain | **403** |
| `WORKSHOP_OPERATOR_EMAIL` in request-time code | **none** (grep) |

The committed suite `backend/tests/test_ownership.py` (14 checks) encodes all of
the above; full backend suite: **123 passing**.

## Phase 5 — public edge DEPLOYED on this box (2026-06-25), tunnel pending DNS

Done and running locally; the only thing left is the one egress-blocked DNS
command (operator-only).

- `workshop-public.service` is **enabled + active on :8002**, serving a freshly
  rebuilt `frontend/dist` (rebuilt because the bundle predated the final
  `auth.ts` rewrite — the old bundle would have shipped the dead localStorage-JWT
  client). `backend/workshop_public_app.py` = static SPA + cookie-forwarding
  proxy; proxies only the personal+auth surface and **404s** every internal/ingest
  path so a public visitor can't inherit the proxy's loopback trust.
- Verified **through the public proxy on :8002**: SPA root + deep-link fallback
  200; `/api/auth/me` and personal APIs 401 unauth; all 10 blocked internal/ingest
  paths 404; a fresh account registered through the proxy got a working
  `workshop_session` cookie, read its own empty partition (200), and was **403 on
  the operator's `/api/budget`** (full segmentation proven at the edge). Probe
  account purged afterward; only the operator partition (`users/6`) remains.
- **Registration closed** (`WORKSHOP_REGISTRATION_OPEN=0` in `secrets.env`,
  backend restarted) — `POST /api/auth/register` → 403 "registration is closed";
  login unaffected. Matches the plan's public-origin posture.
- `cloudflared.service` unit installed and `workshop.benmiriello.com → :8002`
  ingress added to `~/.cloudflared/config.yml` (backup at `config.yml.bak`), above
  the 404 catch-all, leaving the existing `birds.benmiriello.com → :8001` rule
  intact. The tunnel is **not started** — see below.

## The one step left (operator-only — egress guard blocks it from the agent shell)

```
cloudflared tunnel route dns eca1421d-9e82-4bbd-a131-3960065cf953 workshop.benmiriello.com
systemctl --user enable --now cloudflared.service
```

The first command creates the public DNS CNAME (Cloudflare API call — blocked by
the egress guard, and not worked around). The second starts the tunnel; the
moment it connects, `https://workshop.benmiriello.com` is live against the
already-running :8002 edge.

Heads-up: that same tunnel config also serves `birds.benmiriello.com → :8001`,
and the birds app on :8001 is currently **down**, so starting the tunnel will make
`birds.benmiriello.com` return 502 until that app is started again (separate from
Workshop). That is why the tunnel was left for the operator rather than started
automatically.

## Notes / follow-ups

- The operator must re-login on the Workshop web app once (the old localStorage
  JWT is dead; the new cookie flow replaces it). Their data was migrated into
  `.runtime/users/6/` and is intact.
- Cookie `Secure` is on by default; the tailnet (TS Serve https) and Cloudflare
  (https) both satisfy it. For a plain-http local dev proxy set
  `WORKSHOP_COOKIE_SECURE=0`.
- **Mobile follow-up (deferred, needs a Mac):** the native audio producer posts
  to `/birds/analyze`, which is now internal-network-only (for the loopback yard
  mic). For the phone (remote) to use it, that endpoint would need to also accept
  a device token (`require_user_or_internal`), or mobile audio should route
  through a device-token-gated ingest. Location ingest already works (the device
  token satisfies `require_user` on `/location/update`).
- No `WORKSHOP_SESSION_SECRET` is needed: sessions are opaque server-side tokens,
  not signed cookies. (The earlier plan's session-secret requirement is obviated.)
