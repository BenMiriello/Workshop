# App Platform — multi-service runner, port registry, autonomous monitor

Status: **built & live-verified** · Date: 2026-06-15 · Author: the service user

This is the runtime layer that runs a *project* as an *app*: several cooperating
servers, on conflict-free ports, kept alive autonomously, viewable over the tailnet,
and importable/fresh. It generalizes the single-process `LocalRunner` from
[`workshop-v1/05-deployment-isolation-safety.md`](workshop-v1/05-deployment-isolation-safety.md)
and is designed local-now, remote-later: the manifest is the contract a future
`RemoteRunner` (containers) reads unchanged.

Test case: **gengui** (a Bun `core` API + a Vite `frontend` + a Python
`inference-worker`, plus Postgres/Redis/FalkorDB) — the exact "multiple servers as
one app" shape this targets.

## Pieces

| Concern | Module | What it does |
|---|---|---|
| **Port conflicts** | `services/port_registry.py` | The single allocation authority. Per-kind windows (preview 8500-8599, deploy 8600-8699, app 8700-8799) sharing one collision-free namespace; **OS-bind-probes** before handing out a port (so a port held by *any* process is skipped); persists to `.runtime/ports.json`; **reconciles** (frees ports whose owning process died, zombie-aware). `dev_servers` and `local_runner` now delegate here, so the three runners can never collide. |
| **Multi-service run** | `services/app_runner.py` | Reads an app manifest of N services; brings up docker deps; per service: allocates a port, runs the build, spawns the start command as a scoped-env process group, injects peer URLs. Build+spawn run on a **background thread** so the API returns immediately and status is a live state machine (`pending → building → starting → running / failed`). Persists to `.runtime/app-deploys.json`; reflects the exposed service's URL into `projects.json` surfaces. |
| **Keeping apps up** | docker, via `compose_gen` | `restart: unless-stopped` on every generated service, so docker restarts a crashed container and counts the restarts. There was a `services/app_monitor.py` that did this in Python with exponential backoff and a crashloop cap; it was retired when the container backend landed (it skipped every app whenever docker was reachable) and removed on 2026-08-11. Health history now comes from `container_runtime.health()`, which reads docker's `RestartCount`, `OOMKilled` and `ExitCode`. The port registry's `reconcile_loop()` (every 15s) is what remains of that lifespan loop. |
| **Routes** | `routes/base_apps.py` | `/base-apps/run/{slug}/{start,stop,status,manifest,logs}`, `/run/{slug}/services/{svc}/restart`, `/run` (list), `/ports`, and a reverse proxy `/run/{slug}/proxy/{service}/{path}`. |
| **UI** | `components/AppServicesPanel.tsx` | An "App" section on the project page: phase, Start/Stop, per-service status + port + health + restarts + Open/Restart/Logs, docker deps. Polls while anything is in flight. |

## The manifest — `workshop.app.json`

Lives in the project dir. Absent ⇒ auto-detected (a `deploy.config.yaml` with
`services:`, else a single service from `dev_servers.suggest_command`).

```jsonc
{
  "name": "<slug>",
  "services": [
    {
      "name": "core",
      "cwd": "core",                 // subdir, relative to project dir
      "build": "bun install",        // optional, run once before start (full host env + this service's env)
      "start": "bun src/index.ts",   // the long-running command; gets PORT (and port_env) in its env
      "port_env": "PORT",            // env var the service reads for its port (default PORT)
      "<slug>": "/<app>",           // optional http readiness path; else a TCP connect counts
      "expose": true,                // the primary UI surface (reflected as the app URL)
      "enabled": true,
      "depends_on": ["core"],        // start order + auto-injected CORE_URL
      "env": { "VITE_API_URL": "${core.url}", "DB_PORT": "5434" }  // ${svc.url}/${svc.port} resolve to peers
    }
  ],
  "deps": [                          // external dependency containers (docker compose up -d)
    { "name": "postgres", "compose_file": "core/docker-compose.yml", "compose_service": "postgres" }
  ]
}
```

Env wiring two ways: each `depends_on` dep gets an auto-injected `<DEP>_URL`; and any
`${<service>.url}` / `${<service>.port}` placeholder in an `env` value is substituted
from the peer map (ports are pre-allocated so forward references resolve). Each service
also gets `WORKSHOP_APP_SLUG`, `WORKSHOP_SERVICE_NAME`, and `WORKSHOP_PROXY_BASE`.

Env is **scoped** (allowlist + PORT, like `local_runner`): no platform secret leaks
into a deployed app. The manifest `env` supplies the non-secret config a service needs.

## Viewing a running app over the tailnet

Services bind `127.0.0.1:<port>` (not tailnet-exposed). Three ways to see one:

**Nav overlay (always present, never stranded).** The reverse proxy injects a small
floating widget into every proxied HTML page (`services/app_overlay.py`): a circle
pinned bottom-left that expands to **Back to project / Open builder chat / Reload**.
It lives in an open Shadow DOM (`all: initial`, pointer-events isolated) so the host
app's CSS can't break it. It works **both** framed (it postMessages the Workshop shell,
namespaced `workshop/v1/*`, origin-checked, handled in `AppPreview`) **and** full-screen
at the bare proxy URL (it drives `window.top`). This is why opening an app full-screen
no longer dead-ends. The proxy forces identity encoding (so HTML is injectable) and
strips CSP / X-Frame-Options on proxied responses (dev preview, so framing + the inline
overlay run).

1. **Embedded preview** (default): the App panel's **Preview** button →
   `/projects/<slug>/run`, a Workshop page that frames the running service in an iframe
   with Workshop chrome (Back to project / Reload / Full screen) — plus the injected
   overlay inside the frame.
2. **Backend reverse proxy** (what the preview/Full-screen frames):
   `https://<host>:8446/base-apps/run/<slug>/proxy/<service>/...` — through the existing
   backend tailnet origin (`:8446`, trusted CGNAT). HTTP + SSE stream through.
   **The app must be base-aware:** it serves correctly only if its assets use
   `WORKSHOP_PROXY_BASE` (a web service can pass `--base $WORKSHOP_PROXY_BASE` to Vite)
   AND its client router uses that base (e.g. `basepath: import.meta.env.BASE_URL`).
   Done that way, the SPA renders fully (verified on gengui). **Limit:** WebSockets
   (Vite HMR) do not traverse the HTTP proxy — edits need a manual reload, not hot.
3. **Dedicated origin** (for an app you can't make base-aware, or want HMR): give the
   service its own tailnet HTTPS origin. Operator-run (the platform is sandboxed from
   `tailscale serve`):
   ```bash
   tailscale serve --bg --https=8448 http://127.0.0.1:<frontend-port>
   # then open https://<host>:8448/  — full app at base=/, its own /api proxy, HMR
   ```
   Look up `<frontend-port>` in the App panel or `GET /base-apps/ports`.

## Environment / secrets registry

Apps need secrets (API keys, OAuth sets) the manifest must not contain. Following
the universal platform split (Vercel/Railway/Heroku/Replit): the manifest declares
**requirements** (key + metadata, no values, safe in the repo); a separate store
holds the **values** (gitignored, `0600`, never logged); the runner injects them at
spawn. This mirrors Replit's `ask_secrets`/`check_secrets` — the agent declares keys
and can see which are *set*, never their values.

- **Declare** (in `workshop.app.json`): an `env_requirements[]` list, each entry
  `{key, description, required, secret, example, how_to_obtain:{url,steps}, service, group}`.
  The builder writes these (it's told how, in its runtime briefing). `service` scopes a
  var to one service (default `*`); `group` bundles related keys in the UI (e.g.
  `google-oauth`).
- **Store** (`services/secrets_store.py`): values at `.runtime/secrets/<slug>.json`,
  created `0600`. `status()`/`set_keys()` return presence only — never values.
- **Fill** (UI): the project page's **Environment** panel (`EnvironmentPanel.tsx`) —
  per-key description, the how-to-obtain link, Set/Missing status, and (in Edit mode)
  masked write-only inputs. Secrets are never shown back; "Replace" sets a new value,
  "Clear" unsets.
- **Inject** (`app_runner`): `_service_env`/`_run_build` merge the service's scoped
  secret values into its process env. A **required value left unset blocks that service
  from starting** with a clear "Missing required env: X" instead of a crash-loop.
- **API**: `GET /base-apps/run/<slug>/env` (requirements + per-key `set:bool`, no
  values), `PUT …/env` ({values}), `DELETE …/env/<key>`.

`env_overview(slug)` is the presence-only view any agent or the UI consults.

## Editing the Workshop itself (the self / platform project)

The Workshop monorepo is registered as a first-class project (`workshop`,
`dir=~/workshop`, `self: true` in `projects.json`) so the operator can build the
platform — and its built-in apps (birds/commute/budget/location/finance, which
live in `frontend/src/pages/apps` + `backend/services`) — through the same
chat/builder. It is **not** a separate deployment: editing it edits the running
app. (Dogfooding, the way Claude Code/Cursor edit their own repos — point the
agent at the repo, the dev server reloads.)

What's different for a self project:
- `app_runner.is_self_project(slug)` → `manifest_dict`/`app_status` report
  `self: true`; `start_app`/`stop_app`/`restart_service` raise **409** (there's
  nothing to deploy). The App panel shows a "this is the running Workshop" notice
  instead of Start/Stop; the Environment panel is hidden.
- The builder's runtime briefing has a self branch: **frontend hot-reloads** on
  save; **backend changes need `bash scripts/restart_backend.sh`** — and since the
  chat is served *by* that backend, a mid-turn restart kills its own stream, so
  the rule is *edit first, restart last (or ask the operator)*. Never run the
  backend under `uvicorn --reload` for this reason.
- **No auto-checkpoint** runs for the self dir (`is_self_dir`) — the platform
  builder must not auto-commit the live monorepo's working tree (it would sweep in
  unrelated WIP every turn). Commit only the files you changed, when asked.

`ProjectContextProvider` honors the registry `dir`, so the self project resolves
to the monorepo root rather than `projects/<slug>`.

## Builder awareness of the runtime

The build chat (a real `claude -p` session, see `services/chat_context.py`) is given
a **runtime briefing** every turn (`chat_context.runtime_briefing`) so it never guesses
its environment — the pattern every AI builder (v0/Bolt/Lovable/Replit/Cursor) uses.
The briefing states: the working dir, the detected stack, the app's services + their
**centrally-allocated ports** + start commands (pulled from this runner's
`manifest_dict` + `app_status`), how to **run** the app (the `/base-apps/run/<slug>`
endpoints) and **see** it (`/projects/<slug>/run`), where to read each service's logs,
and the negative capability that **the platform owns ports + process lifecycle** (don't
hand-start servers or pick ports — the runner allocates, spawns, and auto-restarts).
It also detects a scaffold project that *shadows* a built-in app (e.g. a `commute`
project whose real code is in the monorepo) and says so, instead of the agent concluding
"nothing exists". Re-sent on resumed turns too, since the session never re-reads it.

## gengui status (2026-06-15)

- **frontend** (Vite/Bun): **runs reliably** via the platform — `bun install` then
  `vite` on an app-window port, healthy. This is the surface to view.
- **core** (Bun API): modeled but **disabled by default**. It needs operator-supplied
  secrets (`GEMINI_API_KEY` + a complete `GOOGLE_*` OAuth set — its env validator
  rejects a partial set) and its dep containers (Postgres/Redis/FalkorDB). The dep
  containers need `the service user` to be in the `docker` group (the socket is currently
  permission-denied). Fill the env in `workshop.app.json`, set `enabled: true`, ensure
  docker access, and the runner brings it up with its deps in order.
- **inference-worker** (torch/diffusers/RunPod GPU): disabled — not a local surface.

To enable core: `usermod -aG docker the service user` (operator), add `GEMINI_API_KEY` +
`GOOGLE_*` to the core service `env`, flip `enabled: true`, then Start app.

## Remote-later

`LocalRunner`/`app_runner` spawn subprocesses; a `RemoteRunner` would build an OCI
image per service and run containers, consuming the **same manifest** (build/start/
ports/health/deps/env). The port registry becomes a host/ingress allocator. Nothing in
a project's manifest changes when it moves to a remote host.
