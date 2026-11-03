# Deploying Workshop on another machine

One script does bring-up:

```bash
git clone <repo> workshop && cd workshop
bash scripts/bootstrap.sh      # first run creates config and stops
# fill in ~/.config/workshop/auth.env and instance.env
bash scripts/bootstrap.sh      # second run builds and installs
```

`bash scripts/preflight.sh` answers "is this machine ready, and what is missing"
at any time. It changes nothing and lists every gap at once rather than stopping
at the first.

## Why this is systemd and not a container

The backend is not a web app that happens to shell out. It spawns agent CLI
processes against a git working tree and manages the operator's own apps as
sibling Docker containers, and those two things are what decide the packaging.

Containerizing the backend breaks the app runner in a way that is silent rather
than loud. The compose files it generates carry **host absolute paths** as
bind-mount sources, and Docker resolves a bind mount on the daemon's filesystem,
not the client's. A backend inside a container would hand the daemon a path that
means nothing on the host, and Docker does not error on that: it creates an
empty directory and starts the app against nothing. The proxy target
(`127.0.0.1:<port>`) and the port allocator (which probes by binding a socket)
break the same way, because both assume the backend shares a namespace with the
apps it manages.

This is not a niche opinion. Komodo's maintainer recommends running its agent
under systemd for this exact reason, Portainer solved it with a dedicated
"compose unpacker" and put it behind their commercial tier, Coolify avoids it by
SSHing from its container back out to the host, and Docker's own agent sandbox
mounts files at identical absolute paths on host and sandbox. The only
containerization that works here mounts the repo, the home directory and the
docker socket at identical paths, which is a chroot with extra steps.

Agent turns break more quietly still: the CLI keys both session resume and MCP
server config by absolute working directory, so a path change means sessions stop
resuming and tools vanish from turns with no error.

The security argument for a container was already answered another way. The
backend runs against **rootless** Docker, so container root maps to the service
user rather than host root.

## What each piece is

| Unit | Serves | Notes |
|---|---|---|
| `workshop-backend.service` | the API on `127.0.0.1:8000` | loopback only, deliberately |
| `workshop-frontend.service` | vite on `127.0.0.1:5173` | the SOURCE origin, reflects edits instantly |
| `workshop-public.service` | `frontend/dist` on `:8002` | the BUILT origin, needs a rebuild after every frontend change |
| `cloudflared.service` | the public hostname | optional |

**The two frontend origins do not update together.** `:5173` serves source and
needs no build. `:8002` serves `frontend/dist` from disk and only changes when
`bash scripts/build_workshop_public.sh` runs. `bash scripts/ui_sync.sh` says which
is which and whether they agree.

Nothing is bound to a non-loopback address. Remote access is loopback-originating
by design (tailscale serve, cloudflared), because nothing sits in front of the
backend enforcing a boundary: the edge is a separate proxy process, not a
gateway the backend hides behind.

**Everything periodic is cron, not a systemd timer.** There are no timers at all.
The schedule lives in `infra/cron/workshop.crontab` (templated on `@WORKSHOP_ROOT@`
like the units) and installs with `bash scripts/install_cron.sh`, which splices a
marked block into the user crontab and leaves unrelated entries alone. It carries
the commute briefs, the daily digest and the frontend watchdog. `--check` reports
drift and prints the diff; `preflight.sh` runs that check so a missing schedule
shows up as something other than an absence of events. Autonomous entries ship
commented out, which is an operator decision recorded in the file; installing does
not turn them on.

`frontends_watchdog.sh` restarts `workshop-frontend.service` when that unit
exists, and only falls back to launching vite itself on a box that has none. It
used to always do the latter, which meant two owners for `:5173`: whenever the
watchdog won the race it held the port outside the unit's cgroup and the unit
crash-looped on a port it could not bind.

## Configuration

Three files under `~/.config/workshop/`, all mode 600:

- **`auth.env`** — `CLAUDE_CODE_OAUTH_TOKEN`. Every agent turn needs it. Without
  it the app serves fine and every chat fails, which reads as a broken app rather
  than a missing credential.
- **`instance.env`** — what makes a clone *this* deployment: origins, the tunnel,
  GitHub owner, data locations. Template: `infra/systemd/instance.env.example`.
- **`secrets.env`** — third-party API credentials. Template:
  `infra/systemd/secrets.env.example`.

Use plain `KEY=value`. An `export` prefix is mis-parsed: systemd's
`EnvironmentFile` drops the line and `scripts/lib/instance.sh` exports a variable
literally named `exportKEY`.

Paths are derived from the repo root, so a clone works anywhere on disk. The
`WORKSHOP_*` path variables exist for layouts that split runtime state or config
off the repo, and are not needed otherwise.

## Updating an existing install

```bash
git pull
backend/.venv/bin/pip install -r backend/requirements.txt   # if it changed
npm install && npm run build:ui && npm run build:web        # if frontend/ or packages/ui changed
bash scripts/restart_backend.sh --deferred                  # if backend/ changed
bash scripts/build_workshop_public.sh                       # if frontend/ changed
```

Pulling a long way forward? [UPGRADING.md](../UPGRADING.md) lists the changes an
older install does not pick up by itself.

`--deferred` waits for any in-flight agent turn to finish before restarting, so
an update cannot cut off a reply mid-stream. It refuses outright (exit 2) if a
turn is running and you did not ask it to wait.

The backend must not restart itself from inside a request: that runs in the
unit's own cgroup and gets killed partway through. `restart_backend.sh` detaches
first, which is what makes it safe to call from an agent turn.

## Optional pieces

Absent, each of these turns one feature off and leaves the rest running.

- **docker** — the app runner. Without it, apps fall back to bare subprocesses
  with no supervision.
- **gh** — pull requests and issue trackers.
- **cloudflared** — the public hostname.
- **ffmpeg** — birds audio.
- **voice** — `pip install -r backend/requirements-voice.txt` plus model files
  under `.runtime/voice/models/`. The packages alone are not enough; the models
  are large and are not pip-installable.

## Things that will bite you

- **`npm install` at the repo root, never inside `frontend/`.** This is an npm
  workspace. Installing in `frontend/` leaves `@workshop/ui` unresolvable.
- **`npm run build:ui` before any typecheck.** vite resolves the kit to its
  sources through a custom condition, but tsc goes through the package exports
  map to `dist/index.d.ts`. A missing `dist` builds fine and fails typecheck,
  so the gap is invisible until CI or the reviewer gate.
- **A port held by another user answers health checks.** On a shared host,
  another tenant's service on your port looks healthy and serves their app.
  Preflight flags a port bound by a process it cannot see.
- **`claude auth status` is not a liveness probe.** It reports local config and
  says logged-in for a token the server has already rejected.
