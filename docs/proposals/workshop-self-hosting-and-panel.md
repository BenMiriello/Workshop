# Workshop self-hosting and the future of control-panel

Status: proposal for operator decision · Date: 2026-07-12 · Branch: `simonsays-local`

## 1. The tension

Two supervisors run this box, with overlapping jobs:

**control-panel** (`~/Documents/control-panel`, config in
`~/.config/control-panel/services.json`) wraps systemd `--user`. Each service is a
templated unit `control-panel@<name>.service` with an EnvironmentFile
(`src/core/services/lifecycle.py` shells out to `systemctl --user`). It gives:
boot-start (enabled units), crash-restart (systemd `Restart=`), journald logs,
cgroup process-tree cleanup, static port assignment from `port_ranges`, and a
CLI (`register/edit/start/stop/ps/logs`) plus a Flask web UI. It manages ~24
registrations today, including Workshop itself (`workshop-backend` :8010,
`workshop-public` :8012).

**Workshop** (`~/Documents/workshop`) contains its own runtime layer, built and
live-verified (`docs/app-platform-runner.md`):

- `backend/services/app_runner.py` — runs a project as an app of N services from a
  `workshop.app.json` manifest: per-service build step, scoped env (allowlist +
  PORT + declared secrets from `secrets_store`), peer URL injection
  (`${core.url}`, auto `<DEP>_URL`), docker-compose `deps`, desired-state
  persisted to `.runtime/app-deploys.json`, orphan reaping.
- `backend/services/port_registry.py` — single port authority: per-kind windows
  (preview/deploy/app 8500–8799), OS bind-probe, pid-aware reconcile.
- `backend/services/app_monitor.py` — 15s supervision loop: restart-on-crash with
  exponential backoff and a crashloop park.
- Reverse proxy + nav overlay at `/base-apps/run/<slug>/proxy/<svc>/…`, an App
  panel UI, and the manifest-driven launcher (`docs/apps-are-pluggable.md`).

**Where they overlap:** process spawn/supervise, restart-on-crash, port
allocation, logs, start/stop UX. **Where they differ:** panel has the things only
PID 1 can give (boot, cgroups, journald, surviving its own manager's death);
Workshop has everything an *app platform* needs that panel lacks (manifests,
builds, secret scoping, service dependency wiring, health checks, proxy/UX,
agents that can operate all of it, and a designed remote/container path —
`docs/workshop-v1/05-deployment-isolation-safety.md`).

The overlap is not hypothetical: today the same box has three port allocators
(panel's `port_ranges`, Workshop's `port_registry`, and whatever each app
hardcodes), and two supervisors that could both claim a service. That is the
thing to resolve.

## 2. Options

### Option A — Panel stays the process layer; Workshop is just an app on it

Status quo. Workshop's runner/monitor exist but panel owns anything "real".

- **Pros:** zero migration; systemd robustness for everything; nothing to build.
- **Cons:** Workshop's core identity — "the platform that runs your apps"
  (`docs/workshop-v1/00-overview.md` §2, Deployment as a first-class concept) —
  is permanently hollow: the launcher, marketplace, builder→deploy loop, and
  RemoteRunner path all assume Workshop owns app lifecycle. Every app onboarded
  gets panel's weakest model: one flat command string, hand-assigned ports,
  hand-split multi-service registrations (`lovelace-core/-frontend/-redis/-face`),
  no builds, no health, no secret scoping. Two allocators and two sources of
  truth forever. The `panel edit --env-add` bug (see
  `docs/simonsays-local-instance.md` §Incident-3) shows panel is also not
  maintenance-free.

### Option B — Workshop becomes self-managing and absorbs panel's role

Workshop is started by **one plain systemd `--user` unit of its own** (not a
`control-panel@` unit); everything else migrates into Workshop as apps; panel
shrinks to a legacy CLI and is eventually retired.

On the chicken/egg: "self-managing" cannot mean "nothing starts Workshop."
`app_monitor` is a Python loop inside a process that itself needs supervising.
The honest resolution: **systemd remains the bootloader; panel stops being the
middleman.** Workshop's own two processes (`workshop-backend`,
`workshop-public`) get native units installed by the repo's own
`scripts/install_service.sh` / `infra/systemd/` path (which this instance
deliberately skipped at bring-up — `docs/simonsays-local-instance.md` §Bring-up
7), with `Restart=always` and linger enabled. That is "not panel-managed" in the
sense that matters: panel's registry, env files, and CLI are no longer in the
loop; there is no dependency on the control-panel codebase. What remains is a
~15-line unit file — infrastructure, not a competing manager.

- **Pros:** one source of truth for app definitions (manifests), one port
  authority, one UX; the builder→deploy→supervise loop Workshop was designed
  around actually closes; agents can operate the whole fleet through one API;
  the manifest is already the contract for the later container/SaaS runner.
- **Cons / real weaknesses to own:**
  - A backend crash suspends supervision of *all* apps until systemd restarts
    the backend. Mitigated: children are detached process groups and keep
    serving; on restart, `_load_persisted` + reconcile + monitor bring
    desired-running apps back. Window is seconds, acceptable for a single
    operator; would not be acceptable for SaaS (containers fix it there).
  - Subprocess spawn lacks cgroup cleanup and journald. `_reap_project_orphans`
    compensates for the former (crudely but effectively); logs go to
    `.runtime/app-logs/*.log`, currently **unrotated** — needs a cap/rotation
    before absorbing chatty services.
  - Infra daemons (`ollama` :11434, `comfy` :8188) are not "apps": fixed
    well-known ports outside the registry windows, GPU state, long startup,
    consumers that hardcode their URLs. Forcing them into `app_runner` buys
    nothing. They should move to plain systemd units (or stay on panel last).

### Option C — Division of labor: Workshop defines, systemd runs

Workshop remains the source of truth (manifests, UI, ports, secrets) but spawns
services via systemd instead of `subprocess.Popen` — either by generating panel
registrations, or better, a `SystemdRunner` spawn backend using
`systemd-run --user --unit=workshop-app-<slug>-<svc> …`.

- **Pros:** best-of-both mechanics: cgroup kill-tree, journald, per-unit resource
  limits, supervision that survives backend death. The `Runner` abstraction was
  explicitly designed for multiple spawn backends (subprocess vs container —
  `app_runner.py` header), so this is a third backend, not a redesign.
- **Cons:** if this means *panel* stays in the loop (Workshop writes panel
  registrations), you get two databases that can drift and the worst kind of
  split-brain — reject that variant. The `systemd-run` variant is clean but real
  work: translating build steps, scoped env, health, and restart/backoff
  semantics into transient units, and reconciling systemd's `Restart=` with
  `app_monitor`'s backoff (two restarters again unless the monitor defers).
  It is an optimization of B's spawn layer, not an alternative ownership model.

### Verdict

**B, with C's `systemd-run` backend held as a later, optional hardening of the
spawn layer.** A is a dead end for what Workshop is for; C-as-integration keeps
two sources of truth. B has one honest weakness (supervision rides in the
backend process) that is tolerable single-operator and disappears under the
already-designed RemoteRunner. Ownership answer in one line: **systemd owns
booting Workshop; Workshop owns everything else.**

## 3. Migration path (each phase reversible)

**Phase 0 — Workshop stands on its own feet.**
Install native `--user` units for `workshop-backend` and `workshop-public`
(adapt `infra/systemd/` for this instance's env — the panel env block in
`~/.config/control-panel/services.json` is the exact spec to port), `loginctl
enable-linger simonsays`, verify reboot survival, then `panel unregister
workshop-backend workshop-public`. Rollback = re-register with panel (the JSON
block is preserved in git history / a backup). No app migrates yet.

**Phase 1 — First app: swipe-save.**
Single Node service; panel entry already disabled. Register it as a Workshop
project + `workshop.app.json` (one service, `start: node server/server.js`,
health path, `expose: true`). It stress-tests the proxy story: its frontend
calls **absolute `/api` paths**, which 404 behind
`/base-apps/run/swipe-save/proxy/app/`. Do it in two steps:
1. Run it as a **standalone** deployment (own port, opened directly — the
   `standalone` mount already exists in the model), which needs zero code
   change and proves the migration mechanics end-to-end (manifest, monitor
   restart, boot recovery via desired-state).
2. Then fix the app to honor a base path (relative URLs, or read
   `WORKSHOP_PROXY_BASE`, which `_spawn_service` already injects) so the
   integrated/proxied mount works too. This fix is the template every legacy
   app with absolute paths will need.

**Phase 2 — First multi-service merge: lovelace** (§5 below). Exercises
services + deps + peer wiring. After it runs from Workshop across a reboot,
disable (don't yet unregister) the four panel entries.

**Phase 3 — The long tail, by kind.**
- App-shaped services (`intergen-core/-worker/-web`, `clipdesk`, `vb-*`,
  `training-manager`): same recipe as Phases 1–2, one at a time, on demand —
  most are currently disabled anyway; migrate when next needed, not
  speculatively.
- Infra daemons (`ollama`, `comfy`, `open-webui`): plain systemd units at their
  fixed ports. Optionally surface them read-only in Workshop later ("system
  services" card); do not force them through `app_runner`.
- `panel-web` (:9000): retire — Workshop's App panel is its replacement.

**Phase 4 — Demote panel.** When no enabled service remains, panel becomes an
uninstalled legacy tool. Before that, commit the uncommitted `lifecycle.py`
env-file fix in the control-panel repo — it guards the transition period.

Pre-work inside Workshop before Phase 2 (small, concrete): log rotation/cap for
`.runtime/app-logs/`; a way to pin a **stable port** for a standalone deployment
(port_registry `preferred=` exists, but bookmarked URLs want a guarantee, e.g. a
`port` field in the manifest honored within/outside the app window).

## 4. Deployment modes: local-personal vs hosted SaaS

Model as a **runtime profile, not a fork or build flavor**. One config value
(e.g. `WORKSHOP_MODE=local|hosted`) that selects bundles of already-seamed
behaviors, never scattered `if local:` checks:

| Axis | local (now) | hosted (later) |
|---|---|---|
| Auth | PIN + session (`auth_store`, this branch's numpad login) | real accounts, OAuth, per-tenant operator flag |
| Reachability | tailnet-only (`tailscale serve`, `WORKSHOP_CANONICAL_ORIGIN` env — already config, not code) | public origin, real TLS |
| Runner | `LocalRunner`/`app_runner` subprocesses | `RemoteRunner` OCI containers (doc 05, designed) |
| Tenancy | single operator, no guardrails (per repo CLAUDE.md) | isolation, quotas, the doc-05 capability/egress model |
| Supervision | in-backend `app_monitor` | the container platform's |

The architecture is already mostly shaped for this: the manifest is
runner-agnostic by design, origins are env-driven, and auth is one module.
The discipline required *now* is only: keep single-operator assumptions behind
those seams (auth mode checks in one place; no new hardcoded origins; nothing
that assumes apps share a filesystem with the platform). SaaS itself stays out
of scope until wanted.

## 5. What "merge lovelace into one app" means

Not one process. `lovelace-core` (Node), `lovelace-frontend` (Node/Vite), and
`lovelace-face` (Python) are different runtimes; collapsing them is a rewrite
with no payoff. **Merge = one manifest, one launcher entry, one co-managed
lifecycle** — exactly the gengui shape `app_runner` was built for:

```jsonc
// ~/Documents/lovelace/workshop.app.json
{
  "name": "lovelace",
  "services": [
    { "name": "core",     "cwd": "core",     "start": "npm run dev",
      "health": "/health", "env": { "JUDGE_ENABLED": "true",
      "REDIS_URL": "redis://127.0.0.1:6379" } },
    { "name": "face",     "cwd": ".",        "start": "bash -c 'source ai-env/bin/activate && python core/services/ai/face_service.py --port $PORT'" },
    { "name": "frontend", "cwd": "frontend", "start": "npm run dev",
      "expose": true, "depends_on": ["core"],
      "env": { "VITE_API_URL": "${core.url}" } }
  ],
  "deps": [ { "name": "redis", "compose_file": "docker-compose.yml",
              "compose_service": "redis" } ]
}
```

**Redis:** it is already a docker container (`docker start -a lovelace-redis`
under panel). The manifest's `deps` mechanism (`_bring_up_deps` → `docker
compose up -d`) is built for exactly this: add a small `docker-compose.yml` to
the lovelace repo declaring the redis service with its volume and a fixed host
port. Do **not** embed or reimplement redis. Note the boundary honestly: deps
are brought *up* but not health-monitored or restarted by `app_monitor` — docker
`restart: unless-stopped` covers that, which is the same guarantee panel gives
today. The hand-assigned ports (6183/6173/5003) go away — services get
registry-allocated ports and find each other via injected peer URLs; anything
that hardcoded `:6183` gets `${core.url}` instead. lovelace's frontend, like
swipe-save's, must tolerate either a standalone origin or a base path.

## 6. Open decisions (operator)

1. **Panel's end state:** retire fully, or keep installed as an emergency CLI
   with zero enabled services? (Proposal: retire after Phase 3; systemctl is the
   escape hatch.)
2. **Infra daemons** (`ollama`, `comfy`, `open-webui`): plain systemd units as
   proposed, or should Workshop grow a "system service" concept to see them?
3. **`systemd-run` spawn backend** (Option C's mechanics inside B): worth the
   work after the migration, or is subprocess + reaper good enough long-term
   for local mode?
4. **Stable ports** for standalone deployments: manifest-pinned port vs
   accept registry-allocated ports and rely on the proxy/launcher for entry.
5. **swipe-save base-path fix**: patch its frontend for proxied mount, or leave
   it standalone-only?
6. **lovelace compose file**: added to the lovelace repo (proposed) or kept out
   of tree?
7. **Uncommitted control-panel `lifecycle.py` fix**: review and commit before
   the migration window opens.
