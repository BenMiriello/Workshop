# Implementation status — IMPLEMENTED + live-verified 2026-06-25

Phases 0–4 are built, tested, and running live on the tailnet backend. Phase 5
(public deployment) artifacts are built and locally verified but NOT cut over —
that is the operator's one step. See `04-critique-and-refinements.md` for the
design deltas this implements.

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

## Phase 5 — built, locally verified, NOT cut over

- `backend/workshop_public_app.py` (:8002): static SPA + cookie-forwarding proxy.
  Proxies only the personal+auth surface; **blocks (404)** the internal/ingest
  paths so a public visitor can't inherit the proxy's loopback trust. Verified
  locally: SPA serves, personal API 401 unauth / 403 cross-user / 200 with cookie
  end-to-end through the proxy, all blocked paths 404.
- `infra/systemd/workshop-public.service`, `infra/systemd/cloudflared.service`,
  `scripts/install_public_service.sh`, `scripts/build_workshop_public.sh`,
  `frontend/dist` (built).

## Operator steps to go public (only these are yours)

1. `bash scripts/install_public_service.sh` (installs units + adds the
   `workshop.benmiriello.com → :8002` ingress to `~/.cloudflared/config.yml`).
2. `cloudflared tunnel route dns eca1421d-9e82-4bbd-a131-3960065cf953 workshop.benmiriello.com`
   (the egress guard blocks this Cloudflare API call from an agent shell).
3. `systemctl --user enable --now workshop-public.service cloudflared.service`.
4. Optional hardening for the public origin: set `WORKSHOP_REGISTRATION_OPEN=0`
   in `~/.config/workshop/secrets.env`, then
   `systemctl --user restart workshop-backend.service`. (Safe to leave open —
   new accounts get empty partitions and can never read your data — but closed is
   the conservative default for a public signup form.)

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
