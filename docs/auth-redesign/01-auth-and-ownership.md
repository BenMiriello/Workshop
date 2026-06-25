# Auth + data-ownership design

The concrete target. Read `00-overview.md` first for the guarantees this satisfies.

## 1. Authentication mechanism

Two credential types, both resolving to a `current_user` server-side, both fail-closed:

### Browser / WebView: signed session cookie
- On login/register, the server creates a session row (`auth.db`: `sessions(id, user_id,
  created_at, expires_at, revoked, user_agent)`) and sets an opaque token in an
  **HttpOnly, Secure, SameSite=Strict** cookie scoped to the origin. This mirrors the proven
  `services/springer_auth.py` model and is XSS-safe (JS cannot read it, so a script injection
  cannot exfiltrate the session).
- Replaces the current localStorage access/refresh JWT for the web surface. localStorage
  tokens are readable by any injected script; for a publicly-exposed app holding bank data,
  that is not acceptable. The cookie is the browser credential of record.
- The mobile WebView is a browser: the same cookie works there. The native shell stops
  storing the access/refresh JWT in `secureStore` for the WebView.
- CSRF: SameSite=Strict blocks cross-site sends; additionally require a custom header
  (`X-Workshop-Client: 1`) on all mutating `/api` requests (a value a cross-site form cannot
  set), rejected if absent. (Cheap, standard double-check.)

### Native background producers: per-device token
- The RN background tasks (location, audio) run outside the WebView and cannot use the cookie.
  At login the app requests a **device token**: a random opaque token stored server-side
  (`device_tokens(token_hash, user_id, label, created_at, last_seen, revoked)`) and held in the
  iOS Keychain. The native producer sends it as `Authorization: Bearer <device-token>`.
- It resolves to the same `current_user` and is revocable per device (Settings shows devices;
  "sign out everywhere" revokes all sessions AND device tokens).

### `current_user` resolution (one code path, fail-closed)
`deps_user.current_user(request)`:
1. valid session cookie -> that user; else
2. valid `Authorization: Bearer <device-token>` -> that user; else
3. valid service token (see section 4) -> the bound service identity; else
4. raise 401.
No IP, no loopback, no CGNAT anywhere in this path. `require_user = Depends(current_user)`;
`require_admin` additionally checks `user.is_admin`.

JWT note: the current short-lived access/refresh JWT (`services/jwt_tokens.py`) can be retired
in favor of opaque server-side sessions (simpler revocation, no secret-rotation footgun), OR
kept strictly for the device-token-to-access-token exchange. Default recommendation: opaque
sessions for the browser, opaque device tokens for native, drop JWT. One mechanism, fully
revocable.

## 2. Authorization + ownership model

### Roles
- `is_admin` (boolean on `users`): may manage PLATFORM/infra (agents, projects, worktrees,
  base-apps, autonomy, redaction, tools, relay, services, events admin). The operator is the
  only admin today.
- Ownership is orthogonal to admin. **Admin does NOT grant read access to another user's
  personal data.** There is no "operator sees all" for personal apps.

### Ownership
- Every personal datum carries `owner_id` (FK to `users.id`).
- Personal routers (`finance`, `budget`, `chat`, `commute`, `location`, `health`, `birds`,
  `environment`, `push`) depend on `require_user` and filter every read/write to
  `current_user.id`. The server pins the scope; clients cannot pass an id to widen it.
- Reuse the birds pattern as the reference implementation: a request-scoped contextvar set
  from `current_user` that wins over any client-supplied id (`birds_analyze.set_request_viewer`
  + `_resolve_viewer` already do exactly this; activate it on the main backend by setting the
  viewer from `current_user` in a dependency, instead of leaving it `None`).

### Per-store partitioning work (the inventory -> the change)
| Store | Backing | Change |
|---|---|---|
| location | `.runtime/location.jsonl` + `location-latest.json` | Add `owner_id` to each record; per-owner latest. Gate router with `require_user`; filter by owner. (Currently ungated.) |
| commute | `.runtime/commute/*.jsonl` (places/journeys/messages/decisions) | Namespace per owner (subdir or `owner_id` field); gate + filter. (Currently ungated.) |
| health | `.runtime/health.jsonl` + `health-latest.json` | `owner_id` per record; gate + filter. (Currently ungated; reads have no auth at all.) |
| budget | `.runtime/budget.json`, `.runtime/txn-overrides.json` | Key by owner (per-owner file or `owner_id`); gate + filter. |
| finance | Plaid live + `finance_cache` | Plaid items must belong to an owner: add `owner_id` to the Plaid item/link records; filter balances/txns by owner. |
| chat | `.runtime/chat/<thread>.json` | Add `owner_id` to thread; list/read/write filter by owner. |
| birds | `~/birdnet/data/browser-stations.db` | Model EXISTS (`owner_user_id`, `viewer_id` contextvar, friends). Activate it on the main mount: set viewer from `current_user`; stop defaulting to `None` (= see-all). |
| environment | `environment.db` | Low sensitivity (weather/astro), but tie to owner or mark explicitly shared-non-personal. |

Migration assigns all existing rows `owner_id = operator.id` (see `03-execution-plan.md`).

### Platform routers
`agents/projects/worktrees/base_apps/autonomy/backlog/agent_runs/relay/services/events/
redaction/tools/auto_pr` -> `require_admin` (operator-only for now). They are NOT personal
data but they ARE powerful (drive agents, read project files), so they must not be open. The
multi-tenant story for the platform is future work; until then, admin-only and fail-closed.

## 3. What gets deleted / changed in `deps_auth.py`

- Remove the loopback + CGNAT (`100.64.0.0/10`) trust entirely from the personal/admin path.
- Remove the fail-open branch (missing token must DENY, not allow).
- `require_token` as it exists is retired; routers move to `require_user` / `require_admin`.
- Keep ONE narrow, explicit machine path: the service token (section 4), which is an
  identity, not an IP rule.

## 4. Internal / machine callers (least privilege)

Today every internal caller rides loopback with no credential (cron, `commute_tick.sh`,
backend self-calls, the Letta tool sandbox via `host.docker.internal`, `/birds/webhook`),
and two ingest paths use their own HTTP Basic (`owntracks`, `health/ingest`). Redesign:

- **Service tokens bound to a user + scope.** A `service_tokens(token_hash, user_id, scope,
  label, revoked)` table. A token resolves to its `user_id` (so its writes are owned) and is
  limited to a `scope` (e.g. `ingest:location`, `ingest:birds`, `platform:agents`,
  `automation:commute`). The token is NOT a master key: it can only touch its scope and only
  owns/reads its bound user's data.
- **cron / commute_tick / self-calls / Letta sandbox:** each gets a scoped service token
  (operator-owned) in `secrets.env`, sent as `Authorization: Bearer <svc-token>` or
  `X-Workshop-Service`. Loopback may be an ADDITIONAL requirement for these (defense in depth)
  but never sufficient alone.
- **Ingest webhooks** (`/birds/webhook`, `/location/owntracks`, `/location/update`,
  `/push/register`, `/health/ingest`): each must present an ingest credential that maps to the
  owner. `owntracks`/`health` already have Basic auth -> bind those credentials to the owner
  id. `/location/update` and `/push/register` and `/birds/webhook` currently have NO auth ->
  add an ingest token bound to the owner. `/tools/*` (Letta) currently ungated -> scope it.
- **Result:** no endpoint trusts the network. Every write is attributable to an owner; no
  credential can read an arbitrary user's data.

## 5. Bootstrap / porting (the only special-case, one time)

A single idempotent script (`backend/scripts/migrate_to_owned.py`, run once):
1. Ensure the operator account exists (email = `WORKSHOP_OPERATOR_EMAIL`, today
   benmiriello@gmail.com), `is_admin = 1`.
2. Add `owner_id` columns/fields to every personal store (migrations) and backfill all
   existing rows to `operator.id`.
3. Mint the operator's service + ingest tokens, write them to `~/.config/workshop/secrets.env`
   (0600), and update the cron/commute/owntracks/health/birdnet configs to send them.
4. Generate `WORKSHOP_SESSION_SECRET` (cookie signing) into secrets.env.

After this runs, `WORKSHOP_OPERATOR_EMAIL` is consulted nowhere at request time. Ownership is
data, not configuration.

## 6. Why this satisfies "only I can ever see my info"

- Reaching the box (loopback, tailnet, public, anything) grants nothing; you must present the
  operator's cookie or device token, which only you hold.
- Every personal query is filtered to the authenticated owner server-side; there is no param
  or header to widen it, and admin does not bypass it.
- A second account (yours or anyone's) owns a separate, empty partition and can never read
  yours. So even with open registration, your data is invisible to others (this is what makes
  the public deployment safe).
- Machine callers are scoped, owned, and revocable; none can read across owners.
- No fail-open, no IP trust, no email-based runtime privilege: the bypasses that exist today
  are gone.
