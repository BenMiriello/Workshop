# Connecting GitHub: the manifest flow plus the OAuth web flow

Date: 2026-09-16. Status: accepted, supersedes one leg of
`app-separation-and-github-2026-08-20.md` §3.2.

## Why this exists

`app-separation-and-github-2026-08-20.md` §3 decided the GitHub auth model and
its handoff carries a standing instruction: do not re-research it, and if you
disagree, argue with the reasoning rather than starting over. This document does
exactly that. It keeps §3.2's central decision (register the GitHub App with the
**App Manifest flow**, so the operator never copies a client id or a secret) and
replaces one downstream leg with a measured correction.

## What was measured, 2026-09-16

1. **The App Manifest cannot enable the device flow.** The manifest accepts
   exactly `name`, `url`, `hook_attributes`, `redirect_url`, `callback_urls`,
   `setup_url`, `description`, `public`, `default_events`,
   `default_permissions`, `request_oauth_on_install`, `setup_on_update`. There
   is no device-flow key. Device flow is a checkbox in the created App's own
   settings ("Enable Device Flow", under Identifying and authorizing users).

   So the shipped plan (manifest flow, then device flow) does not actually reach
   "two clicks, zero copy-paste": it lands the operator back on the forge to
   tick a box, which is the manual step the design set out to remove.

2. **The manifest CAN set `callback_urls`.** That is what the OAuth web flow
   needs, and the manifest conversion endpoint returns `client_id` **and**
   `client_secret`, which is what the web flow's token exchange needs. Both
   arrive without the operator ever seeing either value.

3. **Repo creation still needs a user token, exactly as §3 said.** That fact is
   unchanged and remains the thing that decides the architecture: creating a
   repository in a personal account is a user-token-only operation, so a
   server-to-server installation token cannot do it. A GitHub App **user access
   token** obtained through the OAuth web flow is a user token and does satisfy
   it.

## The decision

Connecting is two operator actions and no copy-paste:

1. **Register.** Workshop renders a form POSTing a generated manifest to the
   forge's new-app endpoint with an unguessable `state`, with `callback_urls`
   and `redirect_url` built from this installation's declared public origin. The
   forge walks the operator through creating the App and redirects back with a
   temporary code. The backend exchanges it at the manifest conversion endpoint
   and stores `client_id`, `client_secret` and the App's id in the owner's
   existing encrypted store.

2. **Authorize.** Workshop sends the browser to the forge's OAuth authorize
   endpoint with `client_id`, `redirect_uri`, a `state` nonce and PKCE. The
   forge redirects back with a code; the backend exchanges it for a **user
   access token** and stores it as the connected account.

The device flow and the pasted fine-grained token both remain, demoted to
fallbacks. They are what a self-hosted installation with no reachable public
origin still has, and the device flow becomes available exactly as it does today
once a client id exists.

## What does not change

- **Org scopes stay refused at entry.** The `ORG_SCOPES` rejection in
  `services/github_identity.py` applies to every path, including this one. The
  boundary against an excluded organisation lives in the credential, not only in
  `installation.reject_blocked`. That is ticket [SEC-G1] and it is not reopened.
- **No secret value is ever returned, logged, or put in an error.** The new
  `client_secret` joins the token under the same rule: written once, never read
  back over HTTP, absent from `status()`.
- **The credential store stays where it is**, per owner, under that owner's
  runtime partition, envelope-encrypted when a KEK is configured. §3.3's
  Fernet-plus-systemd-credentials proposal is neither adopted nor rejected here;
  it is out of scope for connecting an account.
- **The system still works with the forge entirely absent.** No flow blocks on
  it.

## The one new installation fact

The flow needs this installation's browser-reachable origin to build the
redirect. Three modules already resolve that by walking
`WORKSHOP_CANONICAL_ORIGIN`, `WORKSHOP_PUBLIC_ORIGIN`, `WORKSHOP_LOCAL_ORIGIN`
in order (`services/operator_notify.py`, `routes/voice_stream.py`), which is the
same list spelled twice. It becomes one accessor in `services/installation.py`
with those two call sites moved onto it, because a value spelled twice is a
value that will be spelled wrong in one of them.

An installation with no declared origin cannot offer the manifest flow. It says
so and offers the two fallbacks, rather than rendering a button that fails for
reasons nobody can see.
