# Deployment surface inventory, measured 2026-08-24

Status: INVENTORY, not a design. Written so the hosting/deployment redesign can be a
complete replacement rather than an additive layer on top of what is here. Nothing in
this document argues for keeping anything.

Method. Every claim below is one of two kinds and is labelled as such:

- **Measured**: read out of the tree, out of `.runtime/`, out of `/proc`, out of the
  database, or produced by a grep whose result is quoted.
- **Inferred**: a conclusion drawn from measurements. Marked inline.

Caveats on the measurement environment, stated up front so nothing here is
over-claimed:

- The audit shell runs in a bwrap sandbox with `--tmpfs /tmp` and `/run` bound only at
  `/run/user/1003/{systemd,bus}`. The rootless docker socket at
  `/run/user/1003/docker.sock` is therefore invisible from the audit shell. Absence of
  the socket **in the audit shell** is not evidence about the host. Docker on the host
  is proven live by a different route (see 1.4).
- Postgres has row-level security on `app`, `project`, `run`, `worktree`. Connecting as
  `workshop_app` with no tenant context returns 0 rows. Row **counts** below are
  `pg_stat_user_tables.n_live_tup` planner estimates, not `SELECT count(*)`.
- `/workspace` is a bind of `/home/clawd-a/workshop`, which is why stored paths read
  `/home/clawd-a/workshop/...`.

---

## Part 1. The inventory

### 1.1 `backend/services/app_runner.py` (1330 lines), the app runner

The one module that turns a directory in the tree into running processes. Everything
else in this document is either upstream of it (registries, manifests) or an unrelated
parallel system (the Deploy record subsystem, section 1.5).

Key constants, measured:

| Line | Symbol | Value |
|---|---|---|
| `app_runner.py:55` | `WORKSHOP_ROOT` | `paths.workshop_root()` |
| `app_runner.py:87` | `LOG_ROOT` | `paths.env_path("WORKSHOP_APP_LOG_ROOT", "app-logs")` to `.runtime/app-logs/` |
| `app_runner.py:89` | `STATE_STORE` | `paths.env_path("WORKSHOP_APP_STATE", "app-deploys.json")` to `.runtime/app-deploys.json` |
| `app_runner.py:91` | `MANIFEST_NAMES` | `("workshop.app.json", "workshop.app.jsonc")` |
| `app_runner.py:95` | `BUILD_TIMEOUT` | `WORKSHOP_APP_BUILD_TIMEOUT`, default `1800` seconds |
| `app_runner.py:49` | `PROJECTS_REGISTRY` | aliased from `local_runner.PROJECTS_REGISTRY`, resolves to `.runtime/projects.json` |

None of `WORKSHOP_APP_LOG_ROOT`, `WORKSHOP_APP_STATE`, `WORKSHOP_PORT_REGISTRY`,
`WORKSHOP_COMPOSE_ROOT`, `WORKSHOP_DEPLOY_LOG_ROOT`, `WORKSHOP_DEPLOY_RUNTIME` is set in
the running backend's environment (measured: full `env | grep WORKSHOP_` is 30 vars,
none of these). Every path resolves under `$WORKSHOP_ROOT/.runtime/`.

Non-test importers of `app_runner`, measured by grep:

- `backend/routes/base_apps.py` (the HTTP surface)
- `backend/services/app_files.py:210` (uses `_read_manifest_file` only, to resolve where
  an app's files live)
- `backend/services/chat_context.py:1198-1235` (uses `manifest_dict`, `app_status`,
  `env_overview` to build the L2 build-provider prompt text)
- `backend/services/worktrees.py:72` (imports `_read_manifest_file` and `AppError`)
- `scripts/preview_config.py`
- `scripts/restart_app.sh` calls the HTTP surface, not the module.

Two importers reach into private functions (`_read_manifest_file`). Measured, and worth
noting because the manifest reader is already load-bearing outside the runner.

#### 1.1.1 The FULL manifest schema, as actually parsed today

This is what `workshop.app.json` may declare, field by field, taken from the parsers at
`app_runner.py:177-375`. Not the docstring. Anything not in this table is silently
ignored by the reader.

**Top level** (`resolve_manifest`, `app_runner.py:348-375`):

| Field | Type | Default | Parse behaviour |
|---|---|---|---|
| `name` | string | the slug | Display only. Never used for addressing. |
| `services` | list of objects | REQUIRED | Missing, not a list, or empty gives `AppError(400, "manifest has no services list")`. Non-dict entries are dropped silently. |
| `deps` | list of objects | `[]` | External infra brought up by docker compose. |
| `env_requirements` | list of objects | `[]` | Declared env keys, no values. |

Implicit rule at `app_runner.py:371-372`: if there is exactly one service and none is
marked `expose`, that service is forced to `expose = true`.

**`services[]`** (`_spec_from_raw`, `app_runner.py:198-215`):

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | string | `service-<idx>` | Also the compose service name, the log filename component, the port-registry owner suffix, and the proxy path segment. |
| `start` | string | REQUIRED | Empty gives `AppError(400)`. Run as `/bin/bash -c <start>`. |
| `cwd` | string | `"."` | Relative to the project dir. Escape is refused at `app_runner.py:540` and `compose_gen.py:168`. |
| `build` | string or null | `null` | Run as `/bin/bash -c`, blocking, capped at `BUILD_TIMEOUT`. |
| `port_env` | string | `"PORT"` | A second env var name to also receive the port. `PORT` is always set regardless. |
| `health` | string or null | `null` | HTTP path. Subprocess backend probes it; container backend ignores it entirely (see 1.1.4). |
| `expose` | bool | `false` | The app's single public surface. Only the FIRST exposed service is published (`compose_gen.py:234`). |
| `enabled` | bool | `true` | A disabled service is never built, spawned, or emitted into compose. |
| `_disabled_reason` | string | `null` | Note the leading underscore. Parsed from `_disabled_reason` (`app_runner.py:209`) but surfaced to the API as `disabled_reason` (`app_runner.py:1272`). Two names for one field. |
| `depends_on` | list of strings | `[]` | Drives topological start order (`_topo_order`, `app_runner.py:378`), auto-injected `<DEP>_URL` env, and compose `depends_on`. Unknown or cyclic deps are tolerated, never an error. |
| `env` | map string to string | `{}` | Non-secret config. Supports `${<service>.url}` and `${<service>.port}` placeholders. |

**`deps[]`** (`app_runner.py:356-364`):

| Field | Type | Default |
|---|---|---|
| `name` | string | `"dep"` |
| `compose_file` | string | `"docker-compose.yml"`, resolved relative to the project dir |
| `compose_service` | string | falls back to `name` |

A dep is a pointer INTO a compose file the project already has. Workshop does not
declare postgres or redis itself; it copies a service block out of the project's own
compose file and sanitizes it (`compose_gen._sanitize_dep_block`, `compose_gen.py:90`,
strips `ports`, `container_name`, `networks`).

**`env_requirements[]`** (`_env_requirements_from_raw`, `app_runner.py:218-243`):

| Field | Type | Default | Notes |
|---|---|---|---|
| `key` | string | REQUIRED | Entry without a key is skipped silently. |
| `description` | string | `""` | |
| `required` | bool | `true` | A required key with no stored value HARD-BLOCKS the spawn of that service (`app_runner.py:751-760`, `:929-935`). |
| `secret` | bool | `true` | `true` means write-only in the UI. |
| `example` | string | `""` | Format hint, never a real value. |
| `how_to_obtain` | string OR `{url, steps}` | `""` | An object is flattened to `"<steps> <url>"`. |
| `service` | string | `"*"` | Which service receives the value. `""`, `"*"` and `null` all mean all. |
| `group` | string | `""` | UI bundling hint only. |

**Auto-detection when no manifest file exists** (`_autodetect`, `app_runner.py:246-271`).
Two fallbacks, in order:

1. `deploy.config.yaml` with a `services:` map is parsed into ServiceSpecs
   (`_services_from_deploy_config`, `app_runner.py:274`), reading `path`,
   `start_command`, `build_command`, `depends_on`, `healthcheck`. `expose` is guessed by
   substring: a service whose name contains `frontend`, `web`, or `ui`
   (`app_runner.py:311`). If PyYAML is absent it falls back to `_mini_yaml`
   (`app_runner.py:319`), a hand-rolled indentation parser.
2. Else a single service from `local_runner.resolve_command` /
   `dev_servers.suggest_command`.

Measured: exactly one `deploy.config.yaml` exists in the tree
(`/workspace/projects/gengui/deploy.config.yaml`, 677 bytes, mtime 2026-06-13). Its
schema is a DIFFERENT schema again: `services.<n>.platform: lightsail|vercel` and
`environments.{dev,prod}.manual_approval`. Nothing under `backend/` reads `platform:` or
`environments:` (grep: zero hits). Only the `services:` block is read, and only as a
manifest fallback.

**Manifest files that actually exist on disk** (measured, excluding `node_modules`,
`.venv`, and the 10 duplicate template copies under `branches/development/*/`):

| Path | Size | mtime | Content |
|---|---|---|---|
| `/workspace/projects/gengui/workshop.app.json` | 4362 | 2026-07-05 | 3 services (core disabled, frontend enabled plus expose, inference-worker disabled), 3 deps, 6 env_requirements |
| `/workspace/projects/notes-app/workshop.app.json` | 645 | 2026-07-13 | 1 service `api`, uvicorn, enabled plus expose, 1 optional env requirement |
| `/workspace/projects/_templates/fastapi-capability-provider/workshop.app.json` | 356 | 2026-07-11 | the notes-app shape with `{{slug}}` |
| `/workspace/apps/birds/workshop.app.json` | 554 | 2026-08-23 | `{"name": "birds"}` plus a comment. **Declares no services at all.** It exists only so `app_files._resolve_app` points the Files tab at `apps/birds`. It is not runnable and was never intended to be. |

No `workshop.app.jsonc` exists anywhere. The `.jsonc` half of `MANIFEST_NAMES` and its
comment-stripping branch (`app_runner.py:185-189`) have never had an input.

#### 1.1.2 Persisted state

`_STATE` is an in-memory dict mirrored to `.runtime/app-deploys.json` on every
transition (`_persist_state`, `app_runner.py:417`), and loaded once at import
(`_load_persisted`, `app_runner.py:429`, called at `app_runner.py:1330`).

Per-app record keys, measured from the live file plus the writers: `slug`, `desired`
(`running` or `stopped`), `phase` (`starting`, `up`, `partial`, `stopped`, `failed`),
`backend` (`container` or `subprocess`), `manifest_detected`, `compose_path`, `error`,
`deps[]`, `services{}`.

Per-service state (`_init_service_state`, `app_runner.py:687`): `name`, `status`, `pid`,
`port`, `url`, `health`, `expose`, `error`, `restarts`, `started_at`. The container path
adds `healthy`, `oom_killed`, `exit_code`.

Service status vocabulary, measured at `app_runner.py:690`: `pending`, `building`,
`starting`, `running`, `failed`, `stopped`, `disabled`. The container mapping
(`_CSTATE`, `app_runner.py:832`) adds `crashlooping` for docker `restarting`.

**Live content of `.runtime/app-deploys.json`** (1432 bytes, mtime **2026-07-05
21:48**), measured: exactly one app, `gengui`, `backend: "container"`, `phase: "up"`,
`manifest_detected: false`, service `frontend` `running` on port 8799 with
`healthy: true` but `pid: null` and `started_at: null`, services `core` and
`inference-worker` `disabled`, all three deps `ok: false`.

That is the entire persisted runner state. One app, frozen since 5 July.

Measured inconsistency: `manifest_detected: false` is recorded for gengui even though
`projects/gengui/workshop.app.json` has existed since 2026-07-05 19:52, three hours
before that state was written. Inference: either the field means the opposite of its name
or detection did not fire. Either way it is not a field a new design should carry forward
without re-deriving what it is for.

#### 1.1.3 The subprocess backend

`_bring_up` (`app_runner.py:702-788`), on a daemon thread:

1. `_reap_project_orphans(project_dir, set())` (`app_runner.py:641`). Scans all of
   `/proc`, reads each pid's `cwd` symlink, and SIGKILLs the process group of anything
   whose cwd is under the project dir and whose argv[0] basename contains one of
   `node bun vite python python3 uvicorn deno gunicorn` (`_RUNTIME_HINTS`,
   `app_runner.py:638`).
2. `_bring_up_deps` (`app_runner.py:612`): `docker compose -f <file> up -d <service>` per
   dep, 300s timeout, best-effort.
3. Pre-allocate every enabled service's port through `port_registry.allocate`.
4. Per service in topological order: required-secret gate, `_run_build`, `_spawn_service`.

`_spawn_service` (`app_runner.py:537`) runs `subprocess.Popen(["/bin/bash", "-c",
start], cwd=..., start_new_session=True, env=env)` with stdout and stderr appended to
`.runtime/app-logs/<slug>__<service>.log`.

Three env vars are injected unconditionally (`app_runner.py:548-551`):
`WORKSHOP_APP_SLUG`, `WORKSHOP_SERVICE_NAME`, and
`WORKSHOP_PROXY_BASE = /base-apps/run/<slug>/proxy/<service>/`.

**There is no supervisor on this path.** `app_runner.py:1253-1258` states it directly: a
service found dead in `app_status` is marked `failed` and its port released, and nothing
restarts it. `restarts` is hardcoded to 0 on this path (`app_runner.py:1273`). The old
`app_monitor` re-spawn loop was removed; `port_registry.reconcile_loop`'s docstring
(`port_registry.py:173-177`) records that the re-spawn path "had been unreachable ever
since".

#### 1.1.4 The container backend

Selection is a single call: `_use_containers()` calls `container_runtime.available()`
(`app_runner.py:826`, `container_runtime.py:46`), which shells `docker version --format
{{.Server.Version}}` and caches the boolean for the process lifetime. The choice is
frozen per app at `start_app` (`app_runner.py:1012`) into `app["backend"]`, so
status/stop/restart route to the same backend for the app's whole lifetime.

`_container_bring_up` (`app_runner.py:903-981`): allocate ONE host port (only for the
exposed service), resolve secrets per service, `compose_gen.generate(...)`, run the
required-env gate, then `container_runtime.up(slug, compose_path, services=runnable,
build=True, cwd=project_dir)`.

What the container backend drops relative to the subprocess backend, measured:

- The manifest `health` path is never emitted into compose. `compose_gen.generate` writes
  no `healthcheck` for an app service. Health comes only from a dep block's own
  healthcheck if the project's compose file had one.
- Ports for non-exposed services are not allocated. `_container_services`
  (`app_runner.py:869`) sets `port` to the persisted value only for the exposed service
  and `None` for all others.
- `pid` is always `None`.

#### 1.1.5 What `compose_gen` actually generates

`compose_gen.generate` (`compose_gen.py:128-258`) writes `.runtime/compose/<slug>.yml`.
Per enabled app service the generated block is exactly:

```
image:        one of oven/bun:1 | python:3.12-slim | denoland/deno:latest | node:22-bookworm-slim
              chosen by substring match on "<build> <start>" (compose_gen.py:41-54)
working_dir:  /app
volumes:      ["<absolute host path to service cwd>:/app"]     <- BIND MOUNT of the source tree
command:      ["sh", "-c", "<build> && exec <start>"]           with every $ escaped to $$
environment:  PORT=8080, WORKSHOP_APP_SLUG, WORKSHOP_SERVICE_NAME, WORKSHOP_PROXY_BASE,
              optional <port_env>, auto <DEP>_URL, plus the manifest env with peers substituted
restart:      unless-stopped
security_opt: ["no-new-privileges:true"]
cap_drop:     ["ALL"]
user:         nobody
env_file:     [".runtime/compose/<slug>__<service>.env"]        only when secrets exist, mode 0600
ports:        ["127.0.0.1:<host_port>:8080"]                    only on the first exposed service
```

There is no Dockerfile and no image build. The `build: true` passed to
`container_runtime.up` (`app_runner.py:960`) has nothing to build, because no service
block carries a compose `build:` key. Inference: `--build` is a no-op for app services
here; it could only matter for a dep block copied out of a project compose file that had
its own `build:`, and `_sanitize_dep_block` (`compose_gen.py:95`) does not keep `build`
in its `keep` tuple, so it cannot matter there either.

Every service listens on the same fixed `INTERNAL_PORT = 8080` inside its container
(`compose_gen.py:37`). Peers address each other as `http://<service>:8080`.

**Measured drift**: the one real generated file, `.runtime/compose/gengui.yml` (mtime
2026-07-05 21:48), carries NO `security_opt`, `cap_drop`, or `user: nobody`. Those were
added to `compose_gen` later. The running container therefore predates the hardening and
runs without it. Nothing regenerates a compose file except a fresh `start_app`.

**Measured test-isolation leak, active during this audit**: `.runtime/compose/` also
contains `test-app.yml` and `test-app-with-deps.yml`, mtime 2026-08-24 14:02, whose bind
mounts point at `/tmp/pytest-of-clawd-a/pytest-1/test_compose_gen_*/src`. Their mtimes
advanced twice during the audit, traced to `pytest tests/` (pid 821451). The
`compose_gen` tests write into the real runtime compose root. Two fictional app slugs now
sit permanently in production state.

### 1.2 `backend/services/port_registry.py` (303 lines), the port model

Store: `.runtime/ports.json` (`port_registry.py:62`). One flat map, owner to `{port,
kind, owner, pid, claimed_at}`.

Windows (`port_registry.py:46-50`), measured:

| kind | range | user |
|---|---|---|
| `preview` | 8500-8599 | `services/dev_servers.py` |
| `deploy` | 8600-8699 | `services/local_runner.py` (dead, see 1.6) |
| `app` | 8700-8799 | `services/app_runner.py` |

`RESERVED` (`port_registry.py:55-60`) hardcodes `8000` (backend), `5173/5174/5175`
(vite), `11434/11435` (ollama), `8443/8445/8446/8447` (tailscale serve mounts).

Allocation (`allocate`, `port_registry.py:193`) is idempotent per owner, probes
`bind(("127.0.0.1", port))` before handing a port out, honours a `preferred`, and raises
`RuntimeError` when the 100-port window is exhausted. Owner keys are chosen by the
caller; `app_runner._app_owner` (`app_runner.py:440`) uses `app:<slug>/<service>`.

Reconciliation: `reconcile_loop` (`port_registry.py:171`) is started unconditionally in
the backend lifespan at `main.py:57-58`, every 15 seconds. It drops an allocation whose
recorded pid is dead. `_alloc_alive` (`port_registry.py:123`) treats a `null` pid as
alive forever, so a reservation that never gets a pid is never reclaimed.

`process_alive` (`port_registry.py:90`) reads `/proc/<pid>/stat` and treats states `Z`,
`X`, `x` as dead, reaping if possible. This is Linux-`/proc`-specific.

Non-test importers, measured: `main.py`, `routes/base_apps.py`,
`services/app_runner.py`, `services/dev_servers.py`, `services/local_runner.py`,
`services/presence.py` (which uses only `process_alive`).

**Live content of `.runtime/ports.json`** (208 bytes, mtime 2026-07-05 21:48), measured:
a single allocation, `app:gengui/frontend` to 8799, `pid: null`, claimed
2026-07-06T04:48:57Z. In the entire history of this registry, one port has been handed
out.

Measured: nothing is listening in the `preview` window (8500-8599) or the `deploy` window
(8600-8699) on this host. Only 8799 is bound in the `app` window.

### 1.3 `backend/services/container_runtime.py` (239 lines), the docker driver

A thin shell wrapper. Every call is `docker compose -p ws_<sanitized-slug> -f <file>
<args>` (`container_runtime.py:75-89`). Verbs: `config_ok`, `up`, `down`, `restart`,
`logs`, `ps`, plus `health`, which shells `docker inspect --format` over
`{{.Id}} {{.RestartCount}} {{.State.OOMKilled}} {{.State.ExitCode}} {{.State.StartedAt}}`.

`available()` (`container_runtime.py:46`) is the whole gate. It caches once per process
in a module dict; a `refresh=True` parameter exists and has **zero callers** in the tree
(grep). So a backend that boots while docker is down uses the subprocess backend until it
is restarted.

Timeout `WORKSHOP_COMPOSE_TIMEOUT`, default 1800s (`container_runtime.py:31`).

Only importer: `services/app_runner.py`. Measured.

### 1.4 The docker daemon on this host

Measured:

- `which docker` gives `/usr/local/bin/docker`. Client is Docker Engine Community 29.6.0
  with Docker Desktop CLI plugins. `docker compose version` gives `v2.34.0-desktop.1`.
- The client targets **`unix:///run/user/1003/docker.sock`**, i.e. **rootless docker under
  uid 1003**.
- `id` gives `uid=1003(clawd-a) gid=1003 groups=1003,65534(nogroup)`. The operator user is
  **not** in a `docker` group. Rootless is the whole mechanism.
- No podman, no podman-compose, no nerdctl.
- The daemon IS running, proven indirectly: `/proc/4366/cgroup` and `/proc/4839/cgroup`
  both read
  `0::/user.slice/user-1003.slice/user@1003.service/user.slice/docker-64008f127ca3....scope`.
  Those two pids are `bun run dev -- --port 8080 --strictPort --host 0.0.0.0 --base
  /base-apps/run/gengui/proxy/frontend/` and its vite child, started 21 July, still alive,
  both with RSS 0 (fully paged out).

So: the container backend really did run, once, and its one container is still up seven
weeks later.

The `container_runtime` docstring (`container_runtime.py:15-17`) says the fallback exists
"until the backend process can reach the docker socket (it needs the `docker` group,
applied on a fresh login/reboot)". That is stale: the working configuration is rootless
docker on a per-user socket, not a `docker` group. Measured contradiction between comment
and reality.

### 1.5 The Deploy subsystem: a second, unrelated system

This is the part of the surface most worth being blunt about. There are two things named
"deploy" in this repo and they share nothing but the word.

- **Deploy record** (`services/deploys.py`) is a binding from a project to an EXTERNAL
  hosting target (Cloudflare Pages, or rsync over SSH). It never runs anything locally.
- **App runner** (section 1.1) is local processes and containers. It writes its state into
  a file called `app-deploys.json` and its state machine calls itself a deployment, but it
  is not a Deploy record and the two never reference each other.

`GET /apps/{id}/deploys` returns Deploy records (`routes/apps.py:261`, then
`apps_svc.deploys_for_app`, then `services/deploys.py`). It does not know about the app
runner. Measured.

#### 1.5.1 `services/deploys.py` (203 lines): the record

Store: `.runtime/deploys.json` via `WORKSHOP_DEPLOYS_STORE` (`deploys.py:36-39`).

`Deploy` dataclass (`deploys.py:57-73`): `id` (`dep_<ms>_<hex>`), `project_slug`, `name`,
`target` (`{kind, ...}`), `source` (`{kind, branch, build:{...}}`), `domain`, `autonomy`
(one of `AUTONOMY_MODES`, default `reactive`), `created_at`, `updated_at`, `last_run_id`,
`current` (`{run_id, commit_sha, deployed_at, url, upload_ref}`).

There is no `status` field on the record and no run history on the record. Run state lives
entirely in the Job feed (`.runtime/jobs.jsonl`) keyed by `binding.deploy_id`.

**Live content of `.runtime/deploys.json`** (6934 bytes, mtime 2026-08-23 02:29),
measured: **18 records, all identical in every meaningful field**:
`(project_slug='gizmo', name='prod', autonomy='reactive', target.kind='static')`, created
between 2026-08-21 01:47 and 2026-08-23 09:29, every one with `created_at ==
updated_at`, none with `current` or `last_run_id`.

Two things follow, both measured:

1. `gizmo` **does not exist**. Not in `.runtime/projects.json` (which holds
   `development, gengui, commute, location, birds, budget, notes-app`), and
   `/workspace/projects/gizmo` is not a directory.
2. `target.kind = "static"` is **not a registered provider**. `deploy_providers.kinds()`
   returns `["cloudflare-pages", "rsync-ssh"]` (`deploy_providers/__init__.py:14-17`,
   `cloudflare_pages.py:37`, `rsync_ssh.py:37`). `deploy_runner.kick` raises
   `ValueError(f"unknown provider: ...")` at `deploy_runner.py:299-300` for exactly this
   value. So all 18 records are unrunnable by construction.

Inference: these 18 rows are the residue of something creating a deploy target repeatedly
(18 times in 55 hours) with no validation that the project or provider exists, and nobody
noticing because nothing ever tried to run one.

#### 1.5.2 `services/deploy_runner.py` (407 lines): the pipeline

`kick(deploy_id, ref, message, trigger)` (`deploy_runner.py:285`) starts a Job
(`kind="deploy"`) and a daemon thread. `_run_deploy` (`deploy_runner.py:157`) walks:
preflight, `_prepare_workdir`, `provider.build`, `provider.upload`, `provider.status`,
then `deploys.set_current` plus `jobs.finish`.

`_prepare_workdir` (`deploy_runner.py:85`) does `git worktree add --detach` into
`.runtime/deploys/<deploy_id>/runs/<run_id>/src`, falling back to `git clone`, falling
back to `shutil.copytree` for a non-git project.

`kick_rollback` (`deploy_runner.py:385`) re-promotes a prior successful run's
`upload_ref`.

**Measured: this has never executed.** Histogram of `kind` over all 11,883 records in
`.runtime/jobs.jsonl`: `None` 11765, `background` 113, `sweep` 5. Zero `deploy`, zero
`deploy-rollback`, zero `deploy-watcher-error`. `grep -c deploy_id` over the same file
returns 0. `.runtime/deploys/` (the runs and notices root) does not exist.

#### 1.5.3 `services/deploy_watcher.py` (207 lines): the auto-deploy loop

`watcher_loop` (`deploy_watcher.py:187`) is started unconditionally in the backend
lifespan at `main.py:61-62`, sleeping `WORKSHOP_DEPLOY_WATCH_INTERVAL` (default 300s) then
running `tick_all` in a thread.

`tick_one` (`deploy_watcher.py:131`) short-circuits at line 140-141:
`if deploy.get("autonomy") != "autonomous": return {"status": "not-autonomous"}`.

**Measured**: all 18 stored deploys have `autonomy: "reactive"`. So the loop runs every 5
minutes on every backend boot and returns `not-autonomous` eighteen times, forever. The
`git ls-remote` path, the notice writer, and the kick path below that line have never been
reached with a live record.

`NOTICES_ROOT = .runtime/deploys` (`deploy_watcher.py:35`). Measured: the directory does
not exist, so `_write_notice` has never run and `GET
/projects/{slug}/deploys/{id}/notice` has always returned nothing.

#### 1.5.4 `services/deploys_override.py` (81 lines)

`.runtime/deploys-autonomy-override.json`. Measured: the file does not exist, so the
override has never been engaged. Fail-closed on corrupt (`deploys_override.py:13`). Routed
and reachable from `/deploys` (`Deploys.tsx:49,68,86`).

#### 1.5.5 `services/deploy_providers/` (617 lines across 4 files)

| Provider | KIND | env_requirements | Local assumptions |
|---|---|---|---|
| `cloudflare_pages.py` (266 lines) | `cloudflare-pages` | `CF_API_TOKEN` (required), `CF_ACCOUNT_ID` (required) | Cloudflare API over HTTPS, plus a shelled `npx wrangler` |
| `rsync_ssh.py` (237 lines) | `rsync-ssh` | `SSH_KEY_PATH` (required, an **absolute path on this host**) | shells an SSH client with `-i <path> -o StrictHostKeyChecking=accept-new -o BatchMode=yes`, plus `rsync` |

Both are reachable only through `deploy_runner`, which has never run. Measured: no
provider function has ever been called outside tests.

### 1.6 `backend/services/local_runner.py` (431 lines): dead lifecycle, live helpers

The module is imported by `app_runner` and nothing else (measured). But only FOUR things
in it are actually used, all by `app_runner`:

- `PROJECTS_REGISTRY` (`app_runner.py:49`)
- `resolve_command` and `DeployError` (`app_runner.py:261-262`)
- `scoped_env` (`app_runner.py:491`)
- `_reflect_surface` (`app_runner.py:805, 1050, 1062, 1074`)

The lifecycle half of the module is dead. `start_deploy` (`local_runner.py:316`),
`stop_deploy` (`:399`) and `deploy_status` (`:288`) have **zero non-test callers**
(measured by grep across `backend/` and `scripts/`; the only other hits are two comment
lines at `routes/projects.py:114`). Their HTTP routes
(`/base-apps/deploy/{slug}/{start,stop,status}`) were deleted on 2026-08-17; the note is
still in `routes/base_apps.py:497-503`.

Corroborating runtime evidence, measured: `.runtime/deploy-logs/` does not exist, and
nothing is listening in the 8600-8699 deploy window. `local_runner` has never started a
deploy.

**Measured store-path collision.** `local_runner.RUNTIME_STORE` is
`paths.env_path("WORKSHOP_DEPLOY_RUNTIME", "deploys.json")` (`local_runner.py:59`), which
resolves to `.runtime/deploys.json`. `deploys.STORE_PATH` (`deploys.py:36`) is the same
file. Two different schemas share one path and one `{"deploys": {...}}` envelope:
`deploys.py` keys by `dep_<id>`, `local_runner` keys by project slug and stores
`asdict(Deploy)` with a pid. `local_runner._load_runtime` (`local_runner.py:129`) only
checks that `deploys` is a dict, so it would load 18 `dep_*` records as running deploys,
and the first `_persist_runtime` (`local_runner.py:141`) would overwrite the file with
`RUNNING`, silently destroying all 18 Deploy records. This has not happened only because
`start_deploy` has never been called.

`ENV_ALLOWLIST` (`local_runner.py:82-94`), the scoped env every spawned service gets on the
subprocess path: `PATH HOME LANG LC_ALL LC_CTYPE TZ TERM USER LOGNAME SHELL TMPDIR`, plus
`PORT`. Everything else in the platform's environment is dropped. That is the whole
isolation story for the subprocess backend.

### 1.7 Storage, state and secrets an app is given

This is the shortest section in the document, and that is the finding.

| Resource | What an app gets today | Evidence |
|---|---|---|
| **Database** | **Nothing.** There is no per-app database, no provisioning step, no connection string minted for an app. If an app wants postgres it must ship its own `docker-compose.yml` and point a manifest `deps[]` entry at it, and then hardcode the credentials in its own manifest `env`. | grep for `createdb` / `CREATE DATABASE` / provisioning across `backend/services` and `backend/routes`: zero hits related to apps. `information_schema.columns` where `table_name ilike '%deploy%' or column_name ilike '%deploy%'` returns **0 rows**. The 23-table Workshop schema has no app-storage tables. gengui's manifest hardcodes `DB_USER: gengui`, `DB_PASSWORD: gengui_dev_pass`. |
| **Object storage** | **Nothing.** No blob store, no bucket, no upload target for an app. | No such module exists. |
| **Files** | The project directory itself, bind-mounted read-write into the container at `/app` (`compose_gen.py:213`). On the subprocess path, the process simply runs with `cwd` inside the tree. Either way an app writes to the operator's checkout. | measured |
| **Named volumes** | Only those a dep block already referenced, hoisted to the top level by `_collect_named_volumes` (`compose_gen.py:102`). No volume is created FOR the app. | measured |
| **Env vars** | Two tiers. (a) Non-secret config from manifest `services[].env`, which is committed in the repo. (b) Secret values from `services/secrets_store.py`. | measured |
| **Secrets at rest** | `.runtime/secrets/<slug>.json`, dir mode 0700, file mode 0600, created 0600 from the open (`secrets_store.py:93`). Encrypted at rest only when `secrets_crypto.enabled()`, plaintext JSON otherwise (`secrets_store.py:88-91`). A `_workspace` slug provides a cross-project fallback (`secrets_store.py:42, 148`). The container path also writes a sibling 0600 env file at `.runtime/compose/<slug>__<service>.env` (`compose_gen.py:229`). | measured |
| **Secrets in practice** | **Never used.** `.runtime/secrets/` contains exactly one file, `gengui.json`, whose entire content is `{}` (2 bytes, mtime 2026-06-16). No secret value has ever been stored for any app or for `_workspace`. | measured |
| **Identity / auth** | Nothing. An app gets no user identity, no session, no token, no service account. | measured |
| **Logs** | `.runtime/app-logs/<slug>__<service>.log`, append-only, unbounded, never rotated. On the container path, logs come from `docker compose logs` instead and the file is unused. | measured |

Consequence, inferred but strongly: an app in this system is a bind-mounted source tree
with a start command and some env. It has no durable state of its own that survives the
directory being deleted, and it has no story at all for state that must not live on the
operator's laptop.

### 1.8 The registries: four of them, unlinked

| # | Store | Rows (measured) | What a row carries | Who reads it |
|---|---|---|---|---|
| 1 | `.runtime/projects.json` | 7: `development` (self), `gengui`, `commute`, `location`, `birds`, `budget`, `notes-app` | `dir`, `state` (`mounted` / `sketch`), `self`, `surfaces{integrated, standalone, public}` | `routes/projects.py`, `app_runner._project_dir`, `deploy_runner._resolve_project_dir`, `deploy_watcher._resolve_project_dir`, `build_agents`, `tasks_all` |
| 2 | `.runtime/installed-apps.json` | 8: `finance, budget, birds, location, commute, health, wearables, notes-app` | a bare id string | `services/installed_apps.py`, then `app_registry`, `main.py` router mounting, the launcher, the public edge blocklist |
| 3 | `.runtime/apps.json` | 8 | the identity manifest per app: `title`, `sensitivity`, `api_prefixes`, `provides`, `needs`, `kit`, `ui.agent_button` | `services/manifests.py`, `GET /base-apps/manifests` |
| 4 | Postgres `app` table | approx 9 (planner estimate; rows unreadable under RLS) | `id` (uuid), `slug`, `name`, `icon`, `kind`, `parent_id`, `project_slug`, `archived_at` | `routes/apps.py` and `services/apps.py` |

Overlap between (1) and (2), measured: `budget, birds, location, commute, notes-app`, i.e.
**5 slugs in both**, matching what `CLAUDE.md` already records.

The two entities are different in kind and the split is exactly the one
`app-is-the-entity-2026-08-20.md` retires. Registry (2)/(3) is the BUILT-IN app catalog:
apps whose code is in the monorepo under `apps/` and whose routers `main.py` mounts into
the platform process. Registry (1) is the list of directories the app runner can start as
separate processes. Registry (4) is the newer DB entity that neither (1) nor (2) knows
about. `services/app_links.py` bridges (4) to (1) via `project_slug`; nothing bridges (4)
to (2).

The measured consequence of that split is already written up in
`shared/knowledge/development/an-app-resolves-to-two-different-roots-depending-1a02fe8d022.md`
(2026-08-23): `app:birds` resolves to `projects/birds` (118 files, empty `src/`) for the
Files tab and to `apps/birds` (30,150 files, 2.2G) for the knowledge store. **The real app
is unreachable from the UI.**

Also measured: DB `project` estimates 2 rows while `.runtime/projects.json` holds 7. The
file registry and the DB are not the same set.

### 1.9 The local marketplace (`services/registry.py`)

`GET/POST /base-apps/registry*` publish a project as a git bundle and install it back as a
new project. Store is `<workshop_root>/registry.json` with bundles under
`<workshop_root>/registry-bundles/` (`registry.py:59-70`), NOT under `.runtime/`.

**Measured: neither path exists.** `/workspace/registry.json` and
`/workspace/registry-bundles` are both absent. The marketplace has never been used, so
publish, install, `update_status` and `update_from_upstream` have never run.

Frontend: `pages/Marketplace.tsx` is routed at `/marketplace` (`App.tsx:148`) but has no
dock or page-column row in `places.ts`. Its only link is `ProjectDetail.tsx:1607`,
rendered only when a project is ALREADY published. Since nothing has ever been published,
that link has never rendered, so `/marketplace` is reachable only by typing the URL.

### 1.10 The HTTP surface

`main.py` mounts `base_apps.router` at prefix `/base-apps` (`main.py:382`),
`apps_routes.router` at `/apps` (`main.py:343`), `deploys_routes.router` at the root
(`main.py:393`). A middleware at `main.py:306-315` strips a leading `/api`, so every path
answers both with and without it.

Endpoints in scope, with their frontend caller (measured by grep over `frontend/src`,
excluding the generated `api/schema.gen.ts`):

| Endpoint | Frontend caller | Verdict |
|---|---|---|
| `GET /base-apps/run/{slug}/manifest` | `AppServicesPanel.tsx:109`, `AppPreview.tsx:54` | used |
| `POST /base-apps/run/{slug}/start` | `AppServicesPanel.tsx:228,234` | used |
| `POST /base-apps/run/{slug}/stop` | `AppServicesPanel.tsx:237` | used |
| `GET /base-apps/run/{slug}/status` | `AppServicesPanel.tsx:100`, `AppPreview.tsx:53` | used |
| `GET /base-apps/run/{slug}/logs` | `AppServicesPanel.tsx:150` | used |
| `POST /base-apps/run/{slug}/services/{service}/restart` | `AppServicesPanel.tsx:309` | used |
| `ANY /base-apps/run/{slug}/proxy/{service}/{path}` | `AppServicesPanel.tsx:73`, `AppPreview.tsx:87` | used |
| `GET /base-apps/run/{slug}/env` | `EnvironmentPanel.tsx:59` | used |
| `PUT /base-apps/run/{slug}/env` | `EnvironmentPanel.tsx:78` | used |
| `DELETE /base-apps/run/{slug}/env/{key}` | `EnvironmentPanel.tsx:95` | used |
| **`GET /base-apps/run`** (list all) | none | **zero callers** |
| **`GET /base-apps/ports`** | none | **zero callers** |
| **`GET /base-apps/gc/preview`**, **`POST /base-apps/gc/run`** | none | **zero callers** |
| **`GET /base-apps/registry/projects/{slug}/status`**, **`POST .../update`** | none | **zero callers** |
| `GET /base-apps/registry`, `/{id}`, `POST /registry/publish`, `/registry/{id}/install` | `lib/marketplace.ts:38,43,48,56` | routed but the page is effectively unreachable (1.9) |
| `GET /base-apps/launcher` | `Apps.tsx:348`, `Home.tsx:152` | used |
| `GET /base-apps/manifests`, `/manifests/{id}`, `PUT /manifests/{id}/ui` | `Grants.tsx:85`, `Apps.tsx:159,182`, `AppChatDock.tsx:24` | used |
| **`GET /base-apps/permissions/{app_id}`** | none (only named in prose at `Grants.tsx:706`) | **zero callers** |
| `GET /deploys`, `/deploys/providers`, `/deploys/autonomy/override` (GET/POST/DELETE) | `Deploys.tsx:48,49,68,86`, `DeploysPanel.tsx:77` | used, backed by 18 junk records |
| `GET/POST /projects/{slug}/deploys`, `PATCH`, `DELETE`, `:deploy`, `:rollback`, `/runs`, `/notice` | `DeploysPanel.tsx`, `DeployDetail.tsx` | used, but the pipeline behind them has never executed |
| `POST /deploys/watcher/tick` | `DeploysPanel.tsx:176` | used |
| **`POST /projects/{slug}/deploys/{deploy_id}:tick`** | none | **zero callers** |
| `GET /deploys/{deploy_id}/log/{run_id}` | `DeployDetail.tsx:180` | used; no log file has ever existed |
| `GET /apps`, `/apps/{id}/{chats,tickets,notes,runs,deploys,agents}` | `lib/apps.ts:19` and the per-app tabs | used |
| **`POST /apps`, `PATCH /apps/{id}`, `DELETE /apps/{id}`** | none | **zero callers. There is no create, rename or delete-app UI at all.** |

### 1.11 The public edge, and how a running app is (not) reached from outside

`backend/workshop_public_app.py` (490 lines) is a separate uvicorn process on port 8002
(measured running: pid 4178028), behind a Cloudflare tunnel
(`infra/systemd/cloudflared.service`, hostname from `~/.config/workshop/instance.env`). It
does exactly two things:

1. Serves the built SPA from `frontend/dist`, with `/assets` immutable and `index.html`
   `no-cache` (`workshop_public_app.py:426-490`).
2. Reverse-proxies **`/api/{path}` only** to `http://127.0.0.1:8000`
   (`workshop_public_app.py:332-334`), plus a websocket twin at `:345`, with a blocklist
   of loopback-trusted prefixes (`/relay`, `/events`, `/auto-pr`) plus per-app blocks
   pulled from `app_registry.installed_edge_blocks()`, failing CLOSED if that read raises.

**Measured consequence: a running app has no public route.** The frontend builds the proxy
URL against the BACKEND origin, not the edge:

```
// frontend/src/components/AppServicesPanel.tsx:72-78
function proxyHref(slug: string, service: string): string {
  const base = `/base-apps/run/${slug}/proxy/${service}/`;
  const { protocol, hostname, port } = window.location;
  const backendPort = port === "8443" ? "8446" : port === "5173" ? "8000" : port;
  ...
}
```

`8446` is a tailscale-serve mount of the backend. So the only ways to reach a running app
are `http://127.0.0.1:<8700-8799>` directly, or the tailnet host on port 8446 at
`/base-apps/run/<slug>/proxy/<service>/`. A request for `/base-apps/run/...` (no `/api`)
against the public edge falls through to the SPA catch-all and returns `index.html`.

The proxy itself (`base_apps.py:619-691`) also:

- Forwards the FULL incoming path so an app built with `--base $WORKSHOP_PROXY_BASE`
  resolves its assets.
- Forces `accept-encoding: identity` upstream so it can string-inject a nav overlay
  (`app_overlay.inject`) into any `text/html` response.
- **Strips `content-security-policy`, `content-security-policy-report-only` and
  `x-frame-options` from every response** (`base_apps.py:667-670`), so the app can be
  iframed in the Workshop preview. The comment says "a dev-preview proxy, not a production
  origin".
- Does not carry WebSockets. Vite HMR does not traverse it (stated at
  `base_apps.py:608-611`).

### 1.12 Frontend deployment UI

| File | Lines | Route | Reachability (measured) |
|---|---|---|---|
| `frontend/src/pages/Apps.tsx` | 605 | `/apps` (`App.tsx:147`) | routed plus linked (`places.ts:167`, `Home.tsx:165`) |
| `frontend/src/pages/Deploys.tsx` | 305 | `/deploys` (`App.tsx:111`) | routed plus linked (`places.ts:175`). No breadcrumb node exists for it (`appNav.tsx` `NODES` has no `/deploys`). |
| `frontend/src/pages/DeployDetail.tsx` | 451 | `/deploys/:id` (`App.tsx:113`) | routed plus linked from `Deploys.tsx:223`, `DeploysPanel.tsx:327`, `AppDeploysTab.tsx:57` |
| `frontend/src/components/DeploysPanel.tsx` | 517 | component | mounted at `ProjectDetail.tsx:1578` |
| `frontend/src/components/AppServicesPanel.tsx` | 396 | component | mounted at `ProjectDetail.tsx:1575`. **This is the only start/stop UI in the product.** |
| `frontend/src/components/EnvironmentPanel.tsx` | 221 | component | mounted at `ProjectDetail.tsx:1581` |
| `frontend/src/pages/AppPreview.tsx` | 149 | `/projects/:slug/run` (`App.tsx:140`) | routed, effectively unlinked: its only link (`AppServicesPanel.tsx:300`) renders only when a service is `running && (expose || healthy)`. With nothing running, URL-only. |
| `frontend/src/pages/apps/tabs/AppDeploysTab.tsx` | 86 | `/apps/:appSlug/deploys` (`App.tsx:161`) | routed plus linked via `APP_TABS` |
| `frontend/src/pages/Marketplace.tsx` | 203 | `/marketplace` (`App.tsx:148`) | routed, no nav row, conditional link never renders (1.9). URL-only. |
| `frontend/src/apps/registry.tsx` | 387 | n/a | the hardcoded frontend app list |

Two measured structural problems in the UI layer:

- **The launcher and the dock read different sources.** `/apps` reads `GET
  /api/base-apps/launcher` (`Apps.tsx:345-355`). The dock tiles and all 8 per-app tab rows
  come from the hardcoded `APPS` array in `apps/registry.tsx` via `appPlaces()`
  (`registry.tsx:357`, consumed at `useShell.ts:128`). An app installed backend-side but
  absent from `APPS` appears in the launcher grid with **no dock tile and no per-app
  tabs**. Additionally, `Apps.tsx:81-82` lets the hardcoded registry's icon and status
  probe OVERRIDE the backend card's.
- **A second hardcoded icon table.** `Home.tsx:141-144` keeps a `Record<string,
  ComponentType>` of six lucide icons that `Apps.tsx:74-82` says was removed in favour of
  `AppGlyph`. An app icon outside those six renders as a generic `LayoutGrid` on the
  dashboard while rendering correctly on `/apps`.

There is **no UI anywhere for**: creating, renaming or deleting an App entity; viewing the
port registry; running the GC; or checking for a registry update.

---

## Part 2. The five questions

### Q1. The ACTUAL lifecycle of an app today, end to end

There is exactly one path from a directory to serving traffic. Written as real calls:

**A. Getting into the registry (one of three ways, all writing `.runtime/projects.json`)**

1. `POST /projects/` at `routes/projects.py:488` (scaffold from a template), or
2. `POST /projects/import` at `routes/projects.py:1131` (adopt an existing directory), or
3. `POST /base-apps/registry/{id}/install`, which calls `services/registry.install()`
   (never used, see 1.9).

**B. Declaring what to run**

4. A human or an agent writes `workshop.app.json` into the project dir. There is **no API
   and no UI that writes a manifest**; measured by grep, nothing calls a manifest writer.
   All four manifests on disk were hand-authored.

**C. Starting it**

5. Operator clicks Start in `AppServicesPanel.tsx:228`, issuing
   `POST /api/base-apps/run/<slug>/start`.
6. `routes/base_apps.py:517 run_start` calls `app_runner.start_app(slug)`
   (`app_runner.py:984`).
7. `app_runner.is_self_project(slug)` (`:58`); if true, 409 and stop.
8. `app_runner.resolve_manifest(slug)` (`:348`) calls `_project_dir` (`:167`), which reads
   `.runtime/projects.json`, then `_read_manifest_file` (`:177`), else `_autodetect`
   (`:246`).
9. `_STATE[slug]` initialised; `app["backend"] = "container" if
   container_runtime.available() else "subprocess"` (`app_runner.py:1012`). Persisted to
   `.runtime/app-deploys.json`.
10. A daemon thread starts `_container_bring_up` or `_bring_up`. The HTTP call returns a
    `starting` snapshot immediately.

**D. Container path (the live path on this host)**

11. `port_registry.allocate("app:<slug>/<exposed-service>", "app", preferred=...)`
    (`port_registry.py:193`). ONE host port, only for the exposed service.
12. `app_runner._secret_env_for_service` (`:455`) calls `secrets_store.env_for` (`:139`)
    per service.
13. `compose_gen.generate(slug, manifest, project_dir, host_port, secrets_by_service)`
    (`compose_gen.py:128`) writes `.runtime/compose/<slug>.yml` plus 0600 env files.
14. `_missing_required_env` (`app_runner.py:466`) per service; a service missing a required
    key is marked `failed` and excluded, never started.
15. `container_runtime.up(slug, compose_path, services=runnable, build=True,
    cwd=project_dir)` (`container_runtime.py:99`) shells
    `docker compose -p ws_<slug> -f <file> up -d --remove-orphans --build <services>`.
16. `_container_services` (`app_runner.py:869`) shells `docker compose ps --all --format
    json` plus `docker inspect` for restart history, and maps docker states into the
    five-status vocabulary.
17. `_reflect_primary_surface` (`app_runner.py:796`) calls `local_runner._reflect_surface`
    (`local_runner.py:151`), writing `surfaces.standalone = {runner, service, port, url,
    started_at}` back into `.runtime/projects.json`.

**E. Subprocess path (the fallback)**

11'. `_reap_project_orphans` (`:641`) SIGKILLs stray runtimes under the project dir.
12'. `_bring_up_deps` (`:612`) shells `docker compose up -d` per dep.
13'. `port_registry.allocate` for EVERY enabled service.
14'. per service: required-env gate, `_run_build` (`:570`), `_spawn_service` (`:537`)
     which is `Popen(["/bin/bash","-c", start], start_new_session=True, env=scoped_env)`.
15'. `port_registry.set_pid(owner, pid)`.
16'. `_reflect_primary_surface`.

**F. Serving traffic**

18. Nothing is listening publicly. The operator opens the tailnet host on port 8446 at
    `/base-apps/run/<slug>/proxy/<service>/` (`AppServicesPanel.proxyHref`,
    `AppServicesPanel.tsx:72`).
19. `routes/base_apps.py:623 run_proxy` calls `app_runner.app_status(slug)`, finds the
    port, and forwards via `httpx` to `http://127.0.0.1:<port><full path>`, stripping CSP
    and `X-Frame-Options`, injecting a nav overlay into HTML.

**Not in the lifecycle, because it does not exist**: no build artifact, no image, no
registry push, no release, no versioning, no rollback of a running app, no health gate
before cutover, no zero-downtime anything, no domain, no TLS, no database.

### Q2. Process or container? What decides?

**Both, and the decision is one boolean, cached per backend process and frozen per app.**

`app_runner._use_containers()` (`app_runner.py:826`) calls `container_runtime.available()`
(`container_runtime.py:46`), which shells `docker version --format {{.Server.Version}}`
and stores the result in a module-level dict for the life of the backend process. The
result is frozen into `app["backend"]` at `start_app` (`app_runner.py:1012`) so an app
never straddles both backends.

There is no manifest field, no per-app setting, and no UI control for this. An app cannot
ask to be containerized. A `refresh=True` parameter exists on `available()` and has zero
callers, so a docker daemon that comes up after the backend started is not noticed until
the backend restarts.

Measured on this host: docker IS reachable (rootless, `/run/user/1003/docker.sock`), so
the container path is the live one, and `.runtime/app-deploys.json` records
`backend: "container"` for the one app.

The two backends are not equivalent. Measured differences:

| | subprocess | container |
|---|---|---|
| ports allocated | one per service | one, for the exposed service only |
| restart on crash | **none** (`app_runner.py:1253`) | docker `restart: unless-stopped` |
| restart count | hardcoded 0 | real, from `docker inspect .RestartCount` |
| OOM / exit code | not reported | reported |
| manifest `health` probe | honoured (`_service_healthy`, `:517`) | **ignored**, no compose healthcheck emitted |
| logs | append-only file, never rotated | `docker compose logs` |
| env isolation | `ENV_ALLOWLIST`, 11 vars plus PORT | container isolation, `cap_drop: ALL`, `user: nobody` |
| peer addressing | `http://127.0.0.1:<allocated port>` | `http://<service>:8080` |

An app therefore behaves differently depending on whether docker happened to be up when
the backend booted. Inference: that is a correctness hazard, not just a portability one.

### Q3. What an app gets for storage and state, and what it is NOT given

**Gets:**

- Its own source directory, bind-mounted read-write at `/app`. Writes land in the
  operator's checkout.
- Non-secret config from manifest `services[].env`, committed to the repo, with
  `${peer.url}` and `${peer.port}` substitution.
- Secret values from `.runtime/secrets/<slug>.json` (0600, optionally encrypted), scoped
  per service by the `service` field on each env requirement, injected only into the
  process env or a 0600 compose env file.
- Three platform env vars: `WORKSHOP_APP_SLUG`, `WORKSHOP_SERVICE_NAME`,
  `WORKSHOP_PROXY_BASE`.
- One loopback host port, if it declares `expose`.
- Whatever containers its own `docker-compose.yml` defines, if a manifest `deps[]` entry
  points at them. That is the ONLY way an app gets a database, and the app must supply both
  the compose file and the credentials.

**Is NOT given:**

- A database. No provisioning, no DSN, no schema, no migration hook.
- Object or blob storage.
- A persistent volume of its own.
- A backup, a snapshot, or any retention story.
- A user identity, a session, a service account, or any way to authenticate a caller.
- A public URL, a domain, or TLS.
- WebSockets through the proxy.
- A CSP or `X-Frame-Options` (the proxy strips them).
- Resource limits: no `mem_limit`, no `cpus`, no `pids_limit` anywhere in
  `compose_gen.generate`. `oom_killed` is reported but never bounded.
- Log rotation.
- Any isolation from the operator's filesystem beyond the bind mount and `user: nobody`.

And measured: the secrets store has **never held a value**. `.runtime/secrets/` contains
one file, `gengui.json`, content `{}`. The entire declare-then-fill env pipeline has run
end to end zero times.

### Q4. What has provably never run, has no caller, or is unreachable

Ordered by confidence, all measured.

**Never run (positive evidence of absence):**

1. **The whole Deploy pipeline.** `deploy_runner.kick`, `_run_deploy`, `kick_rollback`,
   `_run_rollback`, `_prepare_workdir`, `_cleanup_worktree`. Evidence: 0 of 11,883 job
   records have `kind` in `deploy` or `deploy-rollback`; 0 records contain `deploy_id`;
   `.runtime/deploys/` does not exist.
2. **Both deploy providers.** `cloudflare_pages.py` (266 lines) and `rsync_ssh.py` (237
   lines). Reachable only through (1).
3. **The deploy watcher's actual logic.** The loop runs every 300s on every boot, but
   `tick_one` returns `not-autonomous` at `deploy_watcher.py:140-141` for all 18 stored
   records. `_remote_sha`, `_running_for`, `_write_notice`, `read_notice`, `clear_notice`,
   and the `kick` branch have never executed with a live record. `.runtime/deploys/`
   (NOTICES_ROOT) does not exist.
4. **`deploys_override`.** `.runtime/deploys-autonomy-override.json` does not exist.
5. **`local_runner.start_deploy` / `stop_deploy` / `deploy_status`.** Zero non-test callers
   since their routes were deleted 2026-08-17. `.runtime/deploy-logs/` does not exist;
   nothing listens in 8600-8699.
6. **The local marketplace.** `registry.json` and `registry-bundles/` do not exist.
   Publish, install, `update_status`, `update_from_upstream` have never run.
7. **The `.jsonc` manifest branch.** No `workshop.app.jsonc` exists anywhere.
8. **`deploy.config.yaml` autodetection with `platform:` / `environments:`.** Only one such
   file exists and nothing reads those keys.
9. **`_mini_yaml`** (`app_runner.py:319`), the no-PyYAML fallback. PyYAML is a hard import
   in `compose_gen.py:29`, so if it were missing the container backend would fail at
   import; inference: this fallback cannot matter in a tree where compose_gen loads.
10. **`container_runtime.available(refresh=True)`.** Zero callers.
11. **`container_runtime.config_ok`.** Zero non-test callers.

**Zero frontend callers (endpoint exists, nothing in the product calls it):**

12. `GET /base-apps/run` (list all apps)
13. `GET /base-apps/ports`
14. `GET /base-apps/gc/preview`, `POST /base-apps/gc/run`
15. `GET /base-apps/registry/projects/{slug}/status`, `POST .../update`
16. `GET /base-apps/permissions/{app_id}`
17. `POST /projects/{slug}/deploys/{deploy_id}:tick`
18. **`POST /apps`, `PATCH /apps/{id}`, `DELETE /apps/{id}`.** The App entity, which
    `app-is-the-entity-2026-08-20.md` makes the model of record, has no create, rename or
    delete UI.

**Unreachable or effectively unreachable from the UI:**

19. `/marketplace` (routed, no nav row, its one link is conditional on a state that has
    never existed).
20. `/projects/:slug/run` (AppPreview), linked only when a service is already running.
21. The `apps/birds` tree, per the 2026-08-23 note: the Files tab shows `projects/birds`
    (118 files) while the app is `apps/birds` (30,150 files).
22. `/deploys` and `/deploys/:id` have no breadcrumb node in `appNav.tsx` `NODES`;
    `appNav.tsx:56` still carries a `/settings/base-apps` node for a route that is now a
    redirect (`App.tsx:193`).

**Ran once, long ago, and is now frozen:**

23. The subprocess backend. `.runtime/app-logs/` has exactly two files, both `gengui`:
    `gengui__core.log` (4684 B, 2026-06-15, every start ends `Environment validation
    failed: Missing required API keys`) and `gengui__frontend.log` (8170 B, 2026-06-22,
    vite ready).
24. The container backend. One compose file generated 2026-07-05, one container
    (`64008f127ca3`) running since 2026-07-21.

**Count of dead or unreachable items above: 24** (11 never-run, 7 zero-caller endpoint
groups, 4 unreachable UI paths, 2 frozen-since-June/July backends). In source terms that
is roughly **1,850 lines never executed in production**: `deploy_runner.py` 407 plus
`deploy_watcher.py` 207 plus `deploys_override.py` 81 plus `deploy_providers/` 617 plus
the dead half of `local_runner.py` (approx 250) plus `registry.py` publish/install/update
paths (approx 300).

**Defects surfaced while measuring, worth fixing or deleting regardless of the redesign:**

- 18 Deploy records for a project (`gizmo`) that does not exist, with a provider kind
  (`static`) that is not registered.
- `local_runner.RUNTIME_STORE` and `deploys.STORE_PATH` are the same file with
  incompatible schemas; the first write from `local_runner` would destroy all Deploy
  records.
- `compose_gen` tests write `test-app.yml` and `test-app-with-deps.yml` into the live
  `.runtime/compose/`.
- `.runtime/compose/gengui.yml` predates the `security_opt` / `cap_drop` / `user: nobody`
  hardening, so the one running container does not have it.
- `manifest_detected: false` recorded for an app whose manifest existed three hours
  earlier.

### Q5. Hardcoded assumptions that only hold on THIS machine

Every one of these is measured, with the line that carries it. These are what a hosted,
multi-tenant design cannot inherit.

**One host, loopback only**

1. `port_registry.port_free_on_os` binds `127.0.0.1` specifically
   (`port_registry.py:139`). Freeness is a property of one machine.
2. Every service URL the runner ever mints is `http://127.0.0.1:<port>`
   (`app_runner.py:694, 741, 1100`, `_init_service_state`).
3. `_service_healthy` probes `http://127.0.0.1:<port>` and
   `socket.create_connection(("127.0.0.1", port))` (`app_runner.py:517-532`).
4. `compose_gen` publishes the exposed service to `127.0.0.1:<host_port>:8080`
   (`compose_gen.py:235`).
5. The reverse proxy targets `http://127.0.0.1:{port}` (`base_apps.py:658`).
6. `workshop_public_app.BACKEND` defaults to `http://127.0.0.1:8000`
   (`workshop_public_app.py:39`).
7. `scripts/restart_app.sh:27` hardcodes `http://127.0.0.1:8000/base-apps/run/...`.

**One port space, 300 ports total**

8. `WINDOWS` gives 100 ports each to preview, deploy and app (`port_registry.py:46-50`).
   Three hundred concurrent services is the ceiling for the entire installation, forever.
9. `RESERVED` hardcodes this deployment's own ports: 8000, 5173/5174/5175, 11434/11435
   (ollama), 8443/8445/8446/8447 (tailscale serve) (`port_registry.py:55-60`).
10. `AppServicesPanel.proxyHref` hardcodes the port map 8443 to 8446 and 5173 to 8000
    (`AppServicesPanel.tsx:75`). Two magic numbers about one machine's tailscale
    configuration, compiled into the frontend.

**One local docker daemon, rootless, owned by one uid**

11. `container_runtime._docker()` is `shutil.which("docker")`; every operation is a local
    shell-out (`container_runtime.py:42, 80`). There is no daemon URL, no remote context,
    no API client.
12. The daemon is rootless at `/run/user/1003/docker.sock`, i.e. bound to uid 1003. There
    is no configuration for which socket to use.
13. Compose project names are `ws_<slug>` in a single flat namespace
    (`container_runtime.py:36-39`). Two tenants with the same app slug collide.
14. `_bring_up_deps` shells `docker compose ... up -d` from the backend process
    (`app_runner.py:620-623`), on the same daemon, with no namespace separation from the
    app's own compose project.

**One filesystem, the operator's checkout**

15. `WORKSHOP_ROOT` is the repo root and everything hangs off it: `.runtime/ports.json`,
    `.runtime/app-deploys.json`, `.runtime/compose/`, `.runtime/app-logs/`,
    `.runtime/secrets/`, `.runtime/projects.json`, `.runtime/installed-apps.json`,
    `.runtime/deploys.json`, `.runtime/jobs.jsonl`.
16. Every registry is a single JSON file mutated read-modify-write with an `os.replace`,
    explicitly justified by "single-user box, one backend process"
    (`port_registry.py:24-25`, `deploys.py:9-11`). There is no locking across processes and
    no transaction.
17. `compose_gen` bind-mounts an **absolute host path** into the container
    (`compose_gen.py:213`, `f"{abs_cwd}:/app"`). The generated compose file is only valid
    on the machine that generated it, for that user.
18. `.runtime/compose/gengui.yml` literally contains
    `/home/clawd-a/workshop/projects/gengui/frontend:/app`.
19. `notes-app`'s manifest carries the example value
    `/home/clawd-a/workshop/projects/notes-app/vault`.
20. `rsync_ssh` requires `SSH_KEY_PATH`, an absolute path to a private key on this host
    (`rsync_ssh.py:50`).

**One process table (`/proc`), Linux only**

21. `_reap_project_orphans` (`app_runner.py:641-680`) enumerates `/proc`, reads each pid's
    `cwd` symlink, matches against the project dir, and SIGKILLs process groups. It is both
    Linux-specific and single-tenant-specific: on a shared host it would kill another
    tenant's processes whose cwd happened to be under the same path.
22. `port_registry.process_alive` parses `/proc/<pid>/stat` for zombie state
    (`port_registry.py:100-120`).
23. Liveness generally is pid-based (`os.killpg`, `os.waitpid`), which only works when the
    runner and the workload are on the same kernel and the runner is the parent.

**One operator, no tenancy anywhere**

24. Port-registry owners are `app:<slug>/<service>` (`app_runner.py:440`): a flat slug
    namespace with no tenant component.
25. Secrets are keyed by project slug alone (`secrets_store._path`,
    `secrets_store.py:32`), with a global `_workspace` fallback shared by every project
    (`secrets_store.py:42, 148`).
26. `.runtime/installed-apps.json` is a single global install list. There is no per-user or
    per-tenant installed set.
27. `is_self_project` (`app_runner.py:58`) special-cases "the app IS this checkout", a
    concept that only exists because the platform and the workload share a filesystem.
28. Every route in scope is gated by `_OPERATOR` in `main.py`. There is exactly one
    operator.

**One network trust boundary**

29. The proxy strips CSP and `X-Frame-Options` from every app response
    (`base_apps.py:667-670`). Acceptable for a tailnet dev preview, disqualifying for a
    shared origin.
30. The public edge's safety depends on a blocklist plus the fact that it reaches the
    backend from loopback (`workshop_public_app.py:12-19`). That is a network-position
    trust model.
31. An app has no origin of its own. It is served from the platform's origin under a path
    prefix, so any app can read the platform's cookies for that origin unless prevented
    some other way. Inference, but a direct consequence of 29 plus the path layout.

**The single biggest one**: `compose_gen.py:213` bind-mounts an absolute host path from
the operator's checkout into the container. It makes "the app" and "a directory in the
operator's git working tree" the same object. Nothing about the current model survives
that being false: not the manifest resolution (`_project_dir` reads a `dir` field), not
the build step (runs in the tree), not the runtime (writes to the tree), not the file API,
not the Files tab, not `is_self_project`. A hosted design has to break that identity
first, and everything downstream changes when it does.

---

## Part 3. Disposition of every item

**KEEP-SHAPED** means the concept is right, the code will change.
**REPLACE** means the concept is right, this implementation cannot be hosted.
**DELETE** means dead, or a local-only assumption with no hosted equivalent.

### KEEP-SHAPED

| Item | Why |
|---|---|
| A declarative per-app manifest (`workshop.app.json`) | An app declaring what it is, rather than the platform special-casing it, is the same principle as "no platform source file may name a specific app". The file is the right idea. |
| `services[]` with `name`, `start`, `cwd`, `build`, `expose`, `enabled`, `depends_on`, `env` | These are the fields every hosting platform has. They survive. |
| `env_requirements[]` with `key`, `description`, `required`, `secret`, `example`, `how_to_obtain`, `service`, `group` | The best-designed thing in the surface, and it has no hosted-world problem. `how_to_obtain` in particular is the AI-builder differentiator. Keep the schema verbatim. |
| Declare-requirements / store-values split, presence-only status | The `check_secrets` semantics (an agent can reason about completeness without touching values) is correct and should be preserved exactly. |
| Required-env gate before spawn (`app_runner.py:751`, `:929`) | Refusing to start rather than crash-looping on a missing key is right. |
| The five-status service vocabulary plus an app phase (`up`, `partial`, `starting`, `stopped`) | Correct shape. Note that `crashlooping` only exists on the container path today. |
| Desired-state (`running` / `stopped`) persisted separately from observed state | Correct control-theory shape; keep it. |
| Restart counts meaning CRASHES only, never operator restarts (`app_runner.py:1125-1129`) | A hard-won distinction. Do not relearn it. |
| Deploy record as a binding (project to target) with a `current` pointer, history elsewhere | The record shape is fine. Everything that executes it is not (see REPLACE and DELETE). |
| Provider interface (`preflight`, `build`, `upload`, `status`, `rollback`, plus `env_requirements` and `TARGET_FIELDS`) | A clean plugin seam. Reusable even if both current providers go. |
| The generated nav overlay concept (`app_overlay.inject`) | Framing a running app with platform chrome is a real product feature. |

### REPLACE

| Item | Why the implementation cannot be hosted |
|---|---|
| `port_registry.py` in full | 300 loopback ports, OS bind probe, pid-based reclamation, flat owner namespace. A hosted world addresses services by name, not by a port on one host. |
| `container_runtime.py` | Local `docker compose` shell-out with no daemon target, no remote context, no tenancy in the project name. |
| `compose_gen.py` | Absolute host-path bind mounts, one fixed internal port, no image build, no resource limits, no healthchecks, no volumes provisioned for the app. A hosted world needs an immutable artifact built from the source, not a mount of the operator's tree. |
| `app_runner.py` lifecycle (`start_app`, `stop_app`, `restart_service`, `app_status`) | The verbs are right; the mechanism (threads in the API process, pid tracking, `/proc` scanning, two divergent backends) is not. |
| `app_runner._service_env` peer injection (`<DEP>_URL`, `${svc.url}`) | Service discovery is a real need. Doing it by string-substituting a loopback port at spawn time is not. |
| The reverse proxy (`base_apps.py:619-691`) | Strips CSP and `X-Frame-Options`, no WebSockets, serves apps from the platform origin under a path prefix. Every app needs its own origin in a hosted world. |
| `secrets_store.py` file backend | The API is right (see KEEP-SHAPED); `.runtime/secrets/<slug>.json` on the operator's disk with a global `_workspace` fallback is not. Needs a real per-tenant secret store. |
| `.runtime/app-deploys.json` as runner state | Single-file JSON read-modify-write, "single backend process" by construction. Needs to be rows in a database with a tenant key. |
| The four unlinked registries (`projects.json`, `installed-apps.json`, `apps.json`, DB `app`) | Collapse to one, per `app-is-the-entity-2026-08-20.md` section 9. This is the migration that doc already specifies and that this audit confirms is not built. |
| Logs (`.runtime/app-logs/<slug>__<svc>.log`, append-only, unrotated) | Needs a real log pipeline with retention. |
| `_reflect_primary_surface` writing `surfaces.standalone` | "Where is this app running" is a real question; writing the answer back into a projects registry file is not the way to answer it. The `integrated` and `public` surface slots have never been written by anything and should not be carried over. |
| `deploys.py` record store | Right shape, wrong storage (single JSON file, no validation that `project_slug` or `target.kind` exists, which is how 18 junk rows happened). |
| The provider registry mechanism (`deploy_providers/__init__.py`) | Fine as a seam; needs validation at record-creation time, not at kick time. |

### DELETE

| Item | Why |
|---|---|
| `services/deploy_runner.py` (407 lines) | Never executed. Its model (git-worktree a local checkout, build locally, upload to someone else's PaaS) is the opposite of hosting apps yourself. |
| `services/deploy_watcher.py` (207 lines) | Never reached past line 141. Polling `git ls-remote` every 300s is not how a hosted platform learns about a push. |
| `services/deploy_providers/cloudflare_pages.py` (266 lines) | Never run. Ships someone else's static hosting. |
| `services/deploy_providers/rsync_ssh.py` (237 lines) | Never run. `SSH_KEY_PATH` to an absolute path on the operator's laptop. |
| `services/deploys_override.py` (81 lines) | Never engaged; guards a pipeline that never ran. |
| `local_runner.start_deploy` / `stop_deploy` / `deploy_status` / `Deploy` dataclass / `RUNNING` / `RUNTIME_STORE` / `_load_runtime` / `_persist_runtime` | Zero callers since 2026-08-17. Actively dangerous: `RUNTIME_STORE` collides with `deploys.STORE_PATH` and would destroy the Deploy records. Keep only `PROJECTS_REGISTRY`, `resolve_command`, `scoped_env`, `_reflect_surface`, and move those to where they are used. |
| The `deploy` port window (8600-8699) | Its only user is the above. |
| `app_runner._reap_project_orphans` (`:641-680`) | A `/proc` scan that SIGKILLs by cwd prefix. Single-tenant by construction; a container runtime makes it unnecessary. |
| `app_runner._mini_yaml` (`:319`) | A hand-rolled YAML parser for a fallback that cannot trigger while `compose_gen` hard-imports PyYAML. |
| `deploy.config.yaml` autodetection (`_services_from_deploy_config`, `:274`) | One file in the tree, a third competing schema, `expose` guessed by substring match on the service name. |
| `workshop.app.jsonc` and its comment-stripping branch | No such file has ever existed. |
| `services/registry.py` publish/install/update, plus `/base-apps/registry*`, plus `pages/Marketplace.tsx` | Never used; `registry.json` and `registry-bundles/` do not exist. A local git-bundle marketplace is not what a hosted app platform needs. |
| `GET /base-apps/ports` | Exposes the loopback port table. No callers; no hosted meaning. |
| `GET /base-apps/run` (list) | No callers. |
| `GET /base-apps/gc/preview`, `POST /base-apps/gc/run` | No callers; `services/gc.py` reaps `dev_servers.RUNNING`, which is the preview lifecycle, not apps. It does not know about containers or compose files at all. |
| `GET /base-apps/permissions/{app_id}` | No callers. |
| `POST /projects/{slug}/deploys/{deploy_id}:tick` | No callers. |
| `frontend/src/apps/registry.tsx` `APPS` array | The one file allowed to name apps, and also the reason the dock and the launcher disagree. In a hosted world the app list is data, never a compiled-in array. |
| `Home.tsx:141-144` `APP_ICONS` | A second hardcoded icon table that shadows `AppGlyph` and silently degrades unknown apps to `LayoutGrid`. |
| `AppServicesPanel.proxyHref`'s 8443-to-8446 and 5173-to-8000 port map | One machine's tailscale layout, compiled into the frontend. |
| The 18 `gizmo` Deploy records in `.runtime/deploys.json` | Data, not code, but they should not survive the migration: nonexistent project, unregistered provider kind. |
| `.runtime/compose/test-app.yml`, `test-app-with-deps.yml` | Test leakage into production state. Fix the test isolation and delete the files. |
| `surfaces.integrated` and `surfaces.public` in `projects.json` | Declared at `routes/projects.py`, never written by anything, in a registry that is itself being replaced. |
| `is_self_project` and the `_SELF_MSG` 409 path | Only necessary because the platform and the workload share a checkout. If a hosted design keeps that property it has failed. |

---

## Appendix: files touched by this audit

Backend: `services/app_runner.py`, `services/port_registry.py`,
`services/container_runtime.py`, `services/compose_gen.py`, `services/deploys.py`,
`services/deploy_runner.py`, `services/deploy_watcher.py`, `services/deploys_override.py`,
`services/deploy_providers/{__init__,base,cloudflare_pages,rsync_ssh}.py`,
`services/local_runner.py`, `services/secrets_store.py`, `services/app_registry.py`,
`services/installed_apps.py`, `services/registry.py`, `routes/base_apps.py`,
`routes/apps.py`, `routes/deploys.py`, `routes/projects.py`, `main.py`,
`workshop_public_app.py`.

Frontend: `src/App.tsx`, `src/apps/registry.tsx`,
`src/pages/{Apps,Deploys,DeployDetail,AppPreview,Home,Marketplace,ProjectDetail}.tsx`,
`src/components/{AppServicesPanel,DeploysPanel,EnvironmentPanel}.tsx`,
`src/pages/apps/tabs/*`, `src/components/shell/places.ts`,
`src/lib/{apps,appIcon,marketplace}.ts`.

Runtime state: `.runtime/{app-deploys,ports,deploys,installed-apps,apps,projects}.json`,
`.runtime/{app-logs,compose,secrets}/`, `.runtime/jobs.jsonl`.

Scripts and infra: `scripts/restart_app.sh`, `scripts/apply.sh`,
`infra/systemd/cloudflared.service`.
