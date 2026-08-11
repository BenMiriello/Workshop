# workshop.<your-domain> deployment

Ships AFTER the auth + ownership redesign (`01-auth-and-ownership.md`). Exposing the current
backend would publish the operator's financial data; do not reorder.

## What exists

- A cloudflared tunnel `<tunnel-id>` with cert at
  `~/.cloudflared/cert.pem` (authorized for the <your-domain> zone) and ingress in
  `~/.cloudflared/config.yml` currently routing `birds.<your-domain> -> 127.0.0.1:8001`.
  The tunnel and the springer app are NOT running right now (start them as part of this).
- The model to copy is `apps/birds/springer/app.py`: a small process that serves a built SPA
  bundle and gates the API.
- Cloudflare terminates TLS at the edge; the tunnel carries it encrypted to localhost, so
  `Secure` cookies work and nothing on this box needs a cert.

## Architecture (post-redesign, fail-closed)

```
phone/browser --https--> Cloudflare edge --tunnel--> cloudflared (localhost)
  workshop.<your-domain> -> 127.0.0.1:8002  (workshop_public_app)
      "/" + assets        -> serve frontend/dist (the built Workshop SPA)
      /api/* , /auth/*     -> reverse-proxy to 127.0.0.1:8000 (the main backend)
                              preserving Cookie / Set-Cookie / X-Workshop-Client
```

Because the redesigned backend authenticates the **user** (session cookie / device token) and
no longer trusts any IP, the public app is just a static server + transparent proxy: the
cookie flows through and the backend enforces identity + ownership. The public app holds no
auth logic of its own (nothing to get wrong there), and the main backend is the single source
of enforcement.

Why a separate :8002 process and not point the tunnel straight at :8000: the backend has many
root-level routers (`/location`, `/commute`, `/health`, ...) and does not serve the SPA; the
thin app gives a clean public root (SPA + `/api` + `/auth`, nothing else) and keeps the public
surface small and auditable. It is the same shape as birds/springer.

## Build

`cd frontend && npm run build` -> `frontend/dist` (full Workshop). The SPA already speaks to
`/api` on its own origin and carries the login wall; no build-time origin config needed.
Add a `scripts/build_workshop_public.sh` that builds and (re)starts the public app, mirroring
the birds `dist-springer` rebuild step.

## cloudflared

1. Add a second ingress rule to `~/.cloudflared/config.yml` (same tunnel), ABOVE the catch-all:
   ```yaml
   ingress:
     - hostname: workshop.<your-domain>
       service: http://127.0.0.1:8002
     - hostname: birds.<your-domain>
       service: http://127.0.0.1:8001
     - service: http_status:404
   ```
2. Create the DNS record (proxied CNAME to the tunnel):
   `cloudflared tunnel route dns <tunnel-id> workshop.<your-domain>`
3. Run the tunnel durably: a `cloudflared.service` systemd --user unit
   (`cloudflared tunnel run`), enabled, mirroring `workshop-backend.service`.

### Operator steps (Cloudflare)
- The DNS-route command in step 2 hits the Cloudflare API. The egress guard on this box blocks
  shell tools from reaching external hosts, so the agent likely cannot run it; **the operator
  runs that one command** (and confirms the tunnel comes up). Everything else (config.yml,
  the public app, systemd units, build) is local and the agent does it.
- No Cloudflare dashboard work is expected: the tunnel + cert already exist for the zone.

## Durable services to add (mirror workshop-backend.service)

- `workshop-public.service` -> `uvicorn workshop_public_app:app --host 127.0.0.1 --port 8002`
- `cloudflared.service` -> `cloudflared tunnel run` (uses `~/.cloudflared/config.yml`)
Both `--user`, enabled, restart-on-failure. Install via a `scripts/install_service.sh` addition.

## Config / hardening for the public surface

- `WORKSHOP_REGISTRATION_OPEN=0` initially (closed signups). With ownership partitioning in
  place it is SAFE to open later (new accounts get empty partitions, never the operator's
  data), but start closed.
- `WORKSHOP_SESSION_SECRET` set (fail closed if missing).
- Cookies: `Secure; HttpOnly; SameSite=Strict`, host-only (no hardcoded domain).
- Keep `/birds/webhook`, `/health/ingest`, `/location/owntracks`, `/tools/*` UNREACHABLE from
  the public origin: the public app proxies only `/api/*` and `/auth/*`; ingest/tool paths are
  not exposed through :8002 (they stay loopback + their own ingest tokens).
- Rate-limit at Cloudflare (edge) if desired; not a substitute for the auth above.

## Acceptance

- `https://workshop.<your-domain>` serves the login wall; operator signs in and sees the
  full Workshop; data matches the tailnet view.
- An unauthenticated request to `…/api/finance/*` (or location/budget/health) returns 401.
- A second account sees an empty workspace, never the operator's data (verified in
  `03-execution-plan.md` red-team step).
- The phone's mobile browser works against this origin today (no Mac needed); the native app
  later points `webUrl`/`apiBase` at it.
