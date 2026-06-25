# Workshop auth + data-ownership redesign — overview & threat model

Status: design (2026-06-25). Drives the `workshop.benmiriello.com` public deployment, which
MUST NOT ship until this is implemented. Companion docs: `01-auth-and-ownership.md` (the
design), `02-cloudflare-deployment.md` (the public surface), `03-execution-plan.md` (ordered
steps + acceptance). Supersedes the IP-trust model in
`memory:project_workshop_tailnet_auth_gate` for all personal data.

## Why this exists

The operator's personal data (bank/Plaid balances and transactions, location history,
commute, health, budget, bird detections) is currently protected by **network position, not
identity**. Anything reaching the backend from loopback or the Tailscale CGNAT range is
trusted with zero user check; three personal routers (`location`, `commute`, `health`) have
no auth dependency at all. The product-auth login added recently sits *on top of* that and
does not actually scope data: every logged-in account sees the same single global dataset.

That is unacceptable, and it becomes dangerous the moment the app is exposed publicly. The
operator's requirement, verbatim, is the spec:

> proper auth regardless of tailnet or not, and ONLY I should be able to EVER see my own
> personal info. It must not be possible for anyone to impersonate me, get around the system,
> or get my info.

## Non-negotiable security guarantees (the acceptance bar)

1. **Identity, not network, grants access.** No request reads or writes personal data without
   an authenticated user. Loopback, tailnet, CGNAT, and "same machine" grant **nothing** for
   personal data.
2. **Ownership is enforced server-side, every request.** Every personal datum has an
   `owner_id`. Every personal read/write is filtered to `current_user.id`. No query param,
   header, cookie value, or flag can widen that scope (the birds viewer contextvar already
   demonstrates the "server pins it, client can't override" pattern).
3. **Fail closed.** Missing secret, missing session, unparseable token, unknown caller: deny
   (401/403). There is no fail-open path anywhere. (Today `deps_auth` fails OPEN when the
   service-token file is absent: removed.)
4. **No impersonation.** Sessions are cryptographically bound and revocable. The
   operator-by-email mechanism is a **one-time bootstrap** to assign ownership of ported data;
   it carries **no runtime privilege** to read anyone's data after that.
5. **Least privilege for machines.** Internal callers (cron, self-calls, the Letta tool
   sandbox, ingest webhooks) authenticate with an explicit credential bound to a **specific**
   user identity and scoped to **specific** endpoints. There is no master key that can read an
   arbitrary user's data.

## Current-state vulnerabilities being removed (grounded in the code)

- `deps_auth.require_token` trusts `ip.is_loopback` OR `100.64.0.0/10` and returns before any
  user check (`backend/deps_auth.py`). A Cloudflare tunnel terminates on loopback, so naively
  exposing the backend would trust **every public visitor**.
- `location`, `commute`, `health` routers are included with **no gate** (`main.py`); only the
  network boundary protects bank-adjacent location/health history today.
- `require_token` **fails open** when `WORKSHOP_API_TOKEN` is unset and the token file is
  unreadable (`deps_auth.py`).
- No personal store except birds has an `owner_id`; finance/budget/chat/location/commute/health
  are single global files. The birds ownership model (`owner_user_id` + `viewer_id` contextvar
  + friends) exists but is **dormant on the main backend** (only `springer_app` activates it).
- `services/permissions.py` is a consulted helper that is **not enforced anywhere**;
  `resolve_identity` defaults everyone to the operator. The guest path is dead code.

## Target architecture (one paragraph)

Authentication is a signed, httpOnly, Secure session cookie for the browser/WebView and a
revocable per-device token for native background producers; both resolve to a `current_user`
on **every** request, fail closed. Every personal router depends on `require_user` and scopes
its store by `current_user.id`; platform/infra routers depend on `require_admin` (the operator
is the only admin for now). Internal machine callers carry a service token **bound to a
specific user** and limited to ingest/automation endpoints. IP-based trust is deleted from the
personal path; a narrow loopback+token carve-out remains only for genuinely internal
machine-to-machine endpoints, and is never sufficient on its own. The operator's existing data
is assigned to the operator account by a one-time migration, after which nothing is special
about that account except that it owns that data.

## The one allowed piece of custom handling (porting)

Because we are porting a running single-owner system, exactly one bootstrap step is permitted:
a one-time migration that (a) ensures the operator account exists and (b) stamps `owner_id =
operator.id` onto all pre-existing personal rows/files. This is idempotent, lives in a script,
and is documented in `03-execution-plan.md`. It is the **only** place the operator email is
consulted. No ongoing behavior may branch on "is this the operator" to grant data access.

## Sequencing rule (do not violate)

Public exposure (`workshop.benmiriello.com`) ships **last**. Implement auth + ownership +
internal-caller credentials + removal of IP trust FIRST, verify the guarantees above, and only
then open the tunnel. Exposing the current backend publicly before this work is complete would
publish the operator's financial data.
