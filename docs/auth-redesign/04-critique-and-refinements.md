# Critique + refinements (pre-build review)

Written 2026-06-25 after reading the actual code (`deps_auth.py`, `deps_user.py`,
`auth_store.py`, `jwt_tokens.py`, `routes/auth.py`, `springer_auth.py`, `main.py`, and the
full personal-router inventory). The four design docs are directionally correct; these are the
concrete corrections that make the build match reality. Where this doc and the earlier docs
disagree, **this doc wins** (the earlier docs were written before the code audit).

## R1 — Reuse `is_operator` as the admin role; do NOT add a parallel `is_admin`

The code already has `users.is_operator`, `require_operator`, and the JWT `op` claim. Adding a
second near-identical `is_admin` concept is churn and a footgun (two flags that can disagree).
The operator's actual complaint is not "the flag exists" but "the operator flag lets someone
read my data." So the fix is semantic, not schema: **`is_operator` governs PLATFORM/infra
routers only, and grants NO cross-owner data access.** `require_operator` stays as the admin
gate; the data bypass is what gets deleted (see R2, R5).

## R2 — Strip operator-flagging from `create_user`; it lives ONLY in bootstrap

Today `auth_store.create_user` reads `WORKSHOP_OPERATOR_EMAIL` at registration time and flags
the matching account (or the first account) as operator. That is exactly the "anyone who
registers benmiriello@gmail.com gets my setup" hole. Fix:
- `create_user` always creates a plain member (`is_operator=0`). It never reads the env.
- The one-time `migrate_to_owned.py` is the ONLY place that sets `is_operator=1` (for the
  operator account, by email). After bootstrap, `WORKSHOP_OPERATOR_EMAIL` is referenced at no
  request-time path (grep-verifiable — red-team step 6).
- Registration of an already-existing email still 409s (unique email), so the operator account
  cannot be re-claimed.

## R3 — Per-owner FILE NAMESPACING for the flat-file stores (not "owner_id field + filter")

The personal stores are flat files, not SQL tables (confirmed): `location.jsonl` /
`location-latest.json`, `health.jsonl` / `health-latest.json`, `budget.json` /
`txn-overrides.json`, `chat/<thread>.json`, `push-tokens.json`, `commute/*`. Adding an
`owner_id` field to every record and filtering on read is both more work and less safe (a
single missing `.filter(owner==me)` leaks another owner's rows from a shared file). Instead,
**namespace by owner in the path**:

| Store | Today | After |
|---|---|---|
| location | `.runtime/location.jsonl`, `.runtime/location-latest.json` | `.runtime/users/<oid>/location.jsonl`, `…/location-latest.json` |
| health | `.runtime/health.jsonl`, `.runtime/health-latest.json` | `.runtime/users/<oid>/health.jsonl`, `…/health-latest.json` |
| budget | `.runtime/budget.json`, `.runtime/txn-overrides.json` | `.runtime/users/<oid>/budget.json`, `…/txn-overrides.json` |
| chat | `.runtime/chat/<thread>.json` | `.runtime/users/<oid>/chat/<thread>.json` |
| push | `.runtime/push-tokens.json` | `.runtime/users/<oid>/push-tokens.json` |
| commute | `.runtime/commute/*`, `commute/*.json` config | `.runtime/users/<oid>/commute/*` |
| finance cache | `.runtime/finance-cache.json` | `.runtime/users/<oid>/finance-cache.json` |

Reads open the current user's directory; there is no shared file and therefore no cross-owner
read path to get wrong. The store functions take an `owner_id` arg and resolve their own path.
Migration just `mkdir -p .runtime/users/<operator_id>/` and moves the existing files in
(idempotent: skip if already moved). Finance/Plaid is operator-owned by virtue of the Plaid
items living in the operator's config; a second account has no linked items and sees an empty
finance/budget. A real per-user Plaid link flow is future product work, explicitly out of scope
here (a non-operator hitting finance gets an empty result, never the operator's).

## R4 — Gating moves PER-ENDPOINT; ingest is split from user-read

Several routers mix user-facing reads/writes with machine ingest, so the current
include-level `dependencies=[require_token]` cannot simply become `require_user`. Per router:
- **location**: `/location/latest|history` → `require_user` (owner-scoped). `/location/update`
  → the mobile app's native producer uses a **device token** (resolves to a user) → `require_user`
  also covers it. `/location/owntracks` (third-party iOS app, HTTP Basic) → keep Basic but bind
  the credential to the owner (`require_ingest("location", owner)`).
- **health**: `/health/latest|history` → `require_user`. `/health/ingest` (Health Auto Export,
  Basic) → Basic bound to owner.
- **push**: `/push/register|status` → `require_user` (the app is the signed-in user).
- **birds**: read endpoints → `require_user` + viewer contextvar from `current_user`.
  `/birds/webhook` and `/birds/analyze` (BirdNET-Go, loopback) → ingest token, scope
  `ingest:birds`, bound to operator.
- **tools**: `/tools/*` (Letta Docker sandbox) → service token, scope `automation:tools`.
- **commute**: read/write endpoints → `require_user`. The cron-driven trigger endpoints
  (`/commute/poll|learn|stopmon|reason|alerts/*|brief|llm/decide`) → service token, scope
  `automation:commute`.
- **environment**: `require_user`, NO owner filter (weather/astro is non-personal, shared).
- **plaid/webhook**: stays Plaid-signature-verified (unchanged; not a Workshop credential).

## R5 — One `current_user` dependency, opaque sessions, NO JWT, NO loopback

Replace the JWT access/refresh pair with opaque server-side sessions (the proven
`springer_auth` model, generalized into `auth_store`): a `sessions` table + an HttpOnly cookie
for browsers/WebView, and a `device_tokens` table + opaque bearer for native producers, and a
`service_tokens` table for machines. `current_user(request)` resolves cookie → device token →
service token → 401, with **no IP/loopback/CGNAT anywhere**. Retiring JWT removes the
`WORKSHOP_JWT_SECRET` rotation footgun and gives true server-side revocation. `require_user`
= valid current_user; `require_operator` additionally checks `is_operator` (platform only).
Drop the idea (in 01/04 earlier) of "loopback as an additional requirement" for service tokens
— it reintroduces the network logic we are removing and adds nothing once a token is required.

## R6 — Cookie `Secure` must be conditional for local http dev

The dev flow is browser → `http://localhost:5173` (Vite) → proxy → backend. A `Secure` cookie
is dropped by the browser over plain http, so a hard-coded `Secure` breaks local login. Add
`WORKSHOP_COOKIE_SECURE` (default `1`; set `0` only for the local http dev proxy). `HttpOnly`
and `SameSite=Strict` are always on. In the Cloudflare deployment (https end-to-end) it is `1`.

## R7 — CSRF header check applies ONLY to cookie-authenticated mutations

`X-Workshop-Client: 1` required on mutating requests **when the caller authenticated via the
cookie** (the only ambient credential). Device-token / service-token / Basic callers carry no
ambient credential and are not CSRF-able, so the header is not required for them — otherwise we
break the webhooks and native producers.

## R8 — Delete `services/permissions.py`

It is dead code (`resolve_identity` defaults everyone to the operator; nothing enforces it).
Leaving a "permissions" module that silently authorizes everyone is a hazard for the next
reader. Grep first; the audit found only a self-reference. Remove it.

## R9 — Migration ordering inside Phase 3 (do not break the running system)

Order strictly: (1) mint + write all caller tokens to `secrets.env` and update every caller
(cron `commute_tick.sh`/`commute_notes.sh`, the backend self-calls in `commute/reason.py` and
`commute/llm.py` to `/relay/*`, the Letta `sandbox_wrappers.py` → `/tools/*`, BirdNET-Go →
`/birds/webhook`, OwnTracks/Health Basic → owner), (2) verify a real commute tick + a real
BirdNET detection + a relay call still land WITH the tokens and loopback-trust still present,
(3) THEN delete loopback/CGNAT trust + the fail-open branch from `deps_auth.py`, (4) THEN
red-team. Deleting trust before the tokens are live would brick cron and ingest.

## R10 — Durable, committed proof: a pytest red-team suite

The red-team in `03-execution-plan.md` becomes `backend/tests/test_ownership.py` (committed),
not a one-off curl, so the guarantee is regression-protected: unauthenticated → 401; loopback
no-credential → 401; second member sees empty/own-only and cannot widen via `?owner_id=`/
`?viewer_id=`/forged header; garbage/expired cookie → 401 (never fail-open); a scope-bound
service token used out of scope → 403; and a static assert that `WORKSHOP_OPERATOR_EMAIL` is
read at no request-time path.

## Build scope decision

Build Phases 0–4 fully and run the committed red-team. Then build ALL of Phase 5's artifacts
(the `frontend/dist` build, `workshop_public_app.py`, the systemd units, the `config.yml`
ingress edit, `WORKSHOP_REGISTRATION_OPEN=0`) so the deployment is one command away, but DO NOT
cut over to public: the actual `cloudflared tunnel route dns …` + enabling the tunnel is the
operator's single step (egress guard blocks it from the agent shell anyway), and the sequencing
rule forbids public exposure until 0–4 pass. Registration stays closed.
