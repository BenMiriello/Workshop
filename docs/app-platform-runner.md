# App Platform — multi-service runner, port registry, autonomous monitor

Status: **built & live-verified** · Date: 2026-06-15 · Author: clawd-a

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
| **Autonomous monitor** | `services/app_monitor.py` | A lifespan loop (every 15s): for an app whose **desired state** is `running`, any service whose process died is restarted (no rebuild) with exponential backoff (5s→300s) and a crashloop cap (6 attempts → parked `crashlooping`). Reconciles the port registry each pass. |
| **Routes** | `routes/base_apps.py` | `/base-apps/run/{slug}/{start,stop,status,manifest,logs}`, `/run/{slug}/services/{svc}/restart`, `/run` (list), `/ports`, and a reverse proxy `/run/{slug}/proxy/{service}/{path}`. |
| **UI** | `components/AppServicesPanel.tsx` | An "App" section on the project page: phase, Start/Stop, per-service status + port + health + restarts + Open/Restart/Logs, docker deps. Polls while anything is in flight. |

## The manifest — `workshop.app.json`

Lives in the project dir. Absent ⇒ auto-detected (a `deploy.config.yaml` with
`services:`, else a single service from `dev_servers.suggest_command`).

```jsonc
{
  "name": "gengui",
  "services": [
    {
      "name": "core",
      "cwd": "core",                 // subdir, relative to project dir
      "build": "bun install",        // optional, run once before start (full host env + this service's env)
      "start": "bun src/index.ts",   // the long-running command; gets PORT (and port_env) in its env
      "port_env": "PORT",            // env var the service reads for its port (default PORT)
      "health": "/health",           // optional http readiness path; else a TCP connect counts
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
  containers need `clawd-a` to be in the `docker` group (the socket is currently
  permission-denied). Fill the env in `workshop.app.json`, set `enabled: true`, ensure
  docker access, and the runner brings it up with its deps in order.
- **inference-worker** (torch/diffusers/RunPod GPU): disabled — not a local surface.

To enable core: `usermod -aG docker clawd-a` (operator), add `GEMINI_API_KEY` +
`GOOGLE_*` to the core service `env`, flip `enabled: true`, then Start app.

## Remote-later

`LocalRunner`/`app_runner` spawn subprocesses; a `RemoteRunner` would build an OCI
image per service and run containers, consuming the **same manifest** (build/start/
ports/health/deps/env). The port registry becomes a host/ingress allocator. Nothing in
a project's manifest changes when it moves to a remote host.
