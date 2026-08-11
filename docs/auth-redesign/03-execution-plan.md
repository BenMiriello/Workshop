# Execution plan

Ordered, fail-closed, sequenced so the operator's data is never exposed mid-flight. Do NOT
jump to Phase 5 (public) early. Each phase ends green (`backend` pytest, `frontend`/`mobile`
tsc, `scripts/check_design.sh`) and is committed on `mobile-app-rebuild`. Read
`01-auth-and-ownership.md` for the design these steps implement.

## Phase 0 — Bootstrap + migration scaffolding
- Add `users.is_admin`; ensure the operator account (email = `WORKSHOP_OPERATOR_EMAIL`) exists
  with `is_admin=1`.
- New tables in `auth.db`: `sessions`, `device_tokens`, `service_tokens` (see design doc for
  columns). `WORKSHOP_SESSION_SECRET` generated into `~/.config/workshop/secrets.env` (0600).
- `backend/scripts/migrate_to_owned.py` (idempotent): adds `owner_id` to every personal store,
  backfills existing rows to `operator.id`, mints operator service/ingest tokens into
  secrets.env, updates cron/commute/owntracks/health/birdnet configs to send them.
- Acceptance: migration runs twice with no change the second time; operator account is admin;
  secrets present.

## Phase 1 — Authentication mechanism (cookie + device token), fail-closed
- Implement session cookies (HttpOnly/Secure/SameSite=Strict) + the `device_tokens` bearer
  path + the `service_tokens` path in a single `current_user` dependency; add `require_user`
  and `require_admin`. No IP anywhere in it. Add the `X-Workshop-Client` CSRF header check on
  mutations.
- Frontend: login/register set the cookie (drop localStorage tokens for web); the fetch layer
  sends `X-Workshop-Client` and credentials: 'include'. Mobile native: device-token issuance +
  Keychain storage; `native/auth.ts` sends the device token.
- Keep the box working for the operator: issue the operator's session on login as before.
- Acceptance: operator logs in via cookie and uses every app; no localStorage token present;
  `/auth/me` works via cookie; unauth `/auth/me` -> 401.

## Phase 2 — Ownership enforcement on personal routers
- Per the table in the design doc, gate each personal router with `require_user` and filter
  all reads/writes by `current_user.id`. Set the birds viewer contextvar from `current_user`
  on the main backend (stop defaulting to see-all).
- Acceptance: operator sees their data unchanged; a freshly-created second account sees EMPTY
  finance/budget/location/commute/health/birds/chat; no client param widens scope.

## Phase 3 — Internal callers + remove the IP-trust/fail-open
- Give cron, `apps/commute/scripts/tick.sh`, backend self-calls, and the Letta tool sandbox scoped
  service tokens (operator-owned); wire ingest tokens for `/location/update`, `/push/register`,
  `/birds/webhook`; bind owntracks/health Basic creds to the owner; scope `/tools/*`.
- Delete loopback + CGNAT trust and the fail-open branch from `deps_auth.py`; move every
  previously-ungated personal router behind `require_user`; retire `require_token`.
- Acceptance: with NO credential, every personal + admin + tool endpoint returns 401/403 even
  from loopback. Cron/commute/webhooks still work using their tokens (verify a real tick + a
  real BirdNET detection land).

## Phase 4 — Platform routers -> admin-only
- `agents/projects/worktrees/base_apps/autonomy/backlog/agent_runs/relay/services/events/
  redaction/tools/auto_pr` -> `require_admin`.
- Acceptance: a non-admin member gets 403 on all platform routers; operator unaffected.

## Phase 5 — Public deployment (last)
- `npm run build` -> `frontend/dist`; add `backend/workshop_public_app.py` (static + cookie-
  forwarding proxy to :8000 on :8002); add `workshop-public.service` + `cloudflared.service`
  systemd units; add the `workshop.<your-domain>` ingress rule; `WORKSHOP_REGISTRATION_OPEN=0`.
- Operator runs the `cloudflared tunnel route dns` command (egress guard blocks it from the
  agent's shell); agent does everything else.
- Acceptance: see Phase 5 acceptance in `02-cloudflare-deployment.md`, plus the red-team below.

## Red-team verification (the proof, run at the end of Phase 3 and again after Phase 5)
Each MUST fail (401/403 or empty), scripted and recorded:
1. Unauthenticated GET of `/api/finance/*`, `/api/location/latest`, `/api/budget/*`,
   `/api/health/latest` -> 401.
2. From loopback (`curl 127.0.0.1:8000/...`) with no token -> 401 (proves IP trust is gone).
3. A second member account: GET every personal endpoint -> empty / 403, never operator data.
   Attempt to widen via `?viewer_id=1`, `?owner_id=1`, forged headers -> still scoped to self.
4. Expired/garbage/absent cookie and garbage bearer -> 401, never fail-open.
5. A service/ingest token used outside its scope (e.g. the location-ingest token hitting
   `/api/finance`) -> 403.
6. Confirm `WORKSHOP_OPERATOR_EMAIL` is consulted at NO request-time path (grep; it only
   appears in the one-time migration).

## Notes / coordination
- Branch `mobile-app-rebuild`. The birds DB (`browser-stations.db`) already has the ownership
  model; coordinate with any active birds work (the standalone `springer_app` already enforces
  it, so changes there should converge, not conflict).
- This supersedes `memory:project_workshop_tailnet_auth_gate` for personal data. The
  partitioning here is the concrete form of the "partition incrementally" decision in
  `memory:project_workshop_product_auth`, now promoted to required-before-public.
- The mobile shell auth (`mobile/native/auth.ts`, `inject.ts`) changes from JWT-in-secureStore
  to: cookie for the WebView, device token for native producers. Update the mobile handoff
  (`docs/mobile-handoff-2026-06-25.md`) when Phase 1 lands.
