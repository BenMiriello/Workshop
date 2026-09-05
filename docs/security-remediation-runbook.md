# Security remediation runbook — operator actions — 2026-07-18

Companion to `security-audit-2026-07-18.md` (+ `-agents.md`). The audit pass applied everything that
was safe for `the service user` to change alone (see the "Fixes applied" section of the main report). This
runbook is the remainder: items that need **root** (`the operator`/`sudo`), another **stack owner**, or
an **architectural decision**. Commands are exact; read the note before each.

Legend: 🔴 root required · 👤 another user's service · 🏗 architecture decision · ⚠ changes usage.

---

## Part 1 — The unbreakable agent boundary (A1 / C1) 🏗🔴⚠

**Goal:** agents keep the ability to run apps as containers, but a compromised/injected agent turn
can no longer become *host root* or cross into other users' data. Today it can, because agent turns
run as `the service user` in the `docker` group (root-equivalent socket) with no sandbox.

**Recommended fix: rootless Docker for the app-runner + drop the service user from the `docker` group.**
Rationale: with a rootless daemon, "root in a container" maps to `the service user` on the host (via the
user-namespace subuid range), not real root — so `docker run -v /:/host` can read only what the service user
can already read, and cannot touch `/etc/shadow`, other users' `0700` homes, or escalate. Removing
the `docker` group drops access to the shared *root* daemon, which also restores the
the service user↔simonsays cluster boundary at the docker layer.

Feasibility here is good — prereqs already present: `/etc/subuid` + `/etc/subgid` have a the service user range
(`10231073:65536`), `newuidmap`, `slirp4netns`, `dockerd-rootless-setuptool.sh` installed, linger on.

**Two prerequisites before cutover:**
1. 🔴 `sudo apt install fuse-overlayfs` — WITHOUT it, rootless Docker falls back to the `vfs` storage
   driver, which duplicates every image layer. The host root fs is at **~89%** — a vfs daemon could
   fill it (there is a prior disk-full incident). Do not run the cutover on vfs.
2. Confirm disk headroom after: `df -h /`.

**STATUS 2026-07-18 — cutover LARGELY DONE, one root step remains.** The first attempt ran the
rootless setup + backend steps as `the operator` by mistake, but `sudo gpasswd -d the service user docker`
still executed. the service user (as itself) has since: installed `fuse-overlayfs` (done by operator),
installed + started **rootless docker for the service user** (`/run/user/1003/docker.sock`, overlayfs, linger
on), repointed `workshop-backend.service` to that socket (drop-in no longer uses `sg docker`), and
restarted it — backend is healthy, loopback-bound, and `container_runtime.available()` is True, so the
app runner works on rootless. **Boundary verified on the rootless daemon:** a container's
`cat /etc/shadow` and reading simonsays's `0700` home both return Permission denied.

⚠ **REMAINING (root):** the running systemd user-manager `user@$(id -u).service` was started (at boot)
while the service user was still in `docker`, so it and its child services (incl. the backend, and any agent
turn the backend spawns) still carry the stale `docker` group and can still reach the root socket.
Flush it:
```
sudo systemctl restart user@$(id -u).service      # or: reboot
```
**COMPLETED 2026-07-18:** the operator ran `sudo systemctl restart user@$(id -u).service`. Verified after:
backend groups = `100 1003` (no `994`); a process spawned by the fresh user-manager (= how agent turns
are spawned) hitting the root socket returns **permission denied**; the rootless daemon is usable and
contained. **The boundary is fully closed.** `ws_gengui` was **migrated to rootless** (removed from the
root daemon, `frontend` service brought up on `/run/user/1003/docker.sock`; Vite ready, app runner sees
it `running`, root daemon clean). Remaining tidy-up (optional, the operator's own): a **stray rootless
docker daemon was created for `the operator`** during the misfire
(`~the operator/.config/systemd/user/docker.service`) — harmless; disable with
`systemctl --user disable --now docker` as the operator if unintended.

**Confirmed facts for this host (2026-07-18):** `unprivileged_userns_clone=1`, `max_user_namespaces`
high → rootless is viable; 192G free; **only ONE** the service user app-runner container is on the root daemon
(`ws_gengui-frontend-1`), so the migration is that single app. `dockerd-rootless-setuptool.sh check`
currently aborts "rootful Docker is accessible" — expected; that check passes once the group is
dropped (step 5), or run it with `--force` to preview.

**Cutover steps (do in one sitting; run as `the service user` except the two 🔴 root lines):**
```
# 0. 🔴 (root) install fuse-overlayfs so rootless uses overlay, not the disk-heavy vfs driver
sudo apt-get install -y fuse-overlayfs

# 1. (the service user) install + start the rootless daemon (does NOT touch the system daemon)
dockerd-rootless-setuptool.sh install --force
systemctl --user enable --now docker
export DOCKER_HOST=unix:///run/user/1003/docker.sock
docker info --format '{{.SecurityOptions}}'   # expect rootless + overlay2 (not vfs)
docker run --rm hello-world                    # verify

# 2. Point the backend at the rootless socket. Edit
#    ~/.config/systemd/user/workshop-backend.service.d/docker-group.conf :
#      - drop the `sg docker -c "..."` wrapper; ExecStart becomes a plain:
#        ExecStart=$WORKSHOP/backend/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
#      - add: Environment=DOCKER_HOST=unix:///run/user/1003/docker.sock
systemctl --user daemon-reload && systemctl --user restart workshop-backend.service

# 3. Migrate the one app to the rootless daemon:
#    - on the ROOT daemon:  docker rm -f ws_gengui-frontend-1   (or `compose down` for its project)
#    - re-launch it through the Workshop app runner (which now targets the rootless socket),
#      or `docker --host unix:///run/user/1003/docker.sock compose -p ws_gengui up -d` from its dir.
#    Confirm the app is healthy on rootless before continuing.

# 4. Confirm the backend app runner works on rootless (create/stop a throwaway app from the UI).

# 5. 🔴 (root) drop the group — now the raw root socket is gone for the service user:
sudo gpasswd -d the service user docker
#    Then restart the service user's user services / re-login so running processes lose the group.
```
**Note the app runner keeps working** — it just talks to the rootless socket instead of the root one;
agents keep the ability to run apps. What changes is that container-root now maps to `the service user`, not
host root.
**Verify the boundary held:** as the service user, `docker run --rm -v /home:/mnt alpine cat /mnt/simonsays/…`
should now be permission-denied (rootless can't read another user's 0700 home), and
`cat /etc/shadow` inside a container should fail.

**Interim if you can't do the full cutover soon:** the LAN code-exec path is already closed (the
audit narrowed `/tools` trust to loopback+docker-bridge and bound the backend to loopback), so the
residual is "an injected agent turn on this host can reach root via docker." A partial mitigation is
a **docker-socket-proxy** (tecnativa/docker-socket-proxy) that the backend talks to instead of the
raw socket — but note it cannot block a `create + bind-mount` escape, so it is weaker than rootless.
Rootless is the real fix.

**Also part of this boundary (code side, backend can do without root, but review first):** move the
app-runner's docker calls behind a narrow internal API so *agents never invoke docker directly* —
they request app lifecycle ops from the privileged runner component. Tracked as the structural half
of A1/A2; sequence it after the rootless cutover.

---

## Part 2 — Network exposure cleanup

### C3 🔴 — Verify inbound IPv6 is firewalled (do this FIRST; it gates severity of the rest)
The host has globally-routable IPv6 and several unauth services bind `[::]`. Confirm default-deny:
```
sudo ufw status verbose
sudo ip6tables -L -n
# Ensure inbound IPv6 is denied for: 6379, 6380, 8188, 11434, 11435, 445, 139, 111, 22
# If ufw is v4-only, enable IPv6:  set IPV6=yes in /etc/default/ufw, then: sudo ufw reload
```
If inbound IPv6 is NOT filtered, C2 (unauth Redis) is internet-exposed — treat as urgent.

### C2 👤🔴 — Unauthenticated `lovelace-redis` (:6379, all-interfaces + IPv6, 413 keys)
Not a the service user container (a bare `docker run`, owner the operator/simonsays). Recreate it bound to
loopback with a password:
```
# find how it's started (compose file or a run script), then rebind + set a password:
#   ports: "127.0.0.1:6379:6379"   and   command: redis-server --requirepass <strong>
# update whatever consumes it with the password. Same for intergen-redis :6380 (loopback already).
```

### H4 / H7 👤🔴 — ComfyUI (:8188) and Ollama (:11435/:11434) unauth on all interfaces
Both are other users' processes (hidden pids). Rebind to loopback / tailscale:
```
# ComfyUI: launch with  --listen 127.0.0.1  (not 0.0.0.0)
# Ollama:  set  Environment=OLLAMA_HOST=127.0.0.1  in its unit/compose, restart
# Reach either remotely only via an authenticated proxy or Tailscale Serve.
```

### H5 👤 — Unauthenticated ":9000 Control Panel" (Flask/Werkzeug) exposed to the tailnet
Anonymous, mutating (add/start/stop services), served tailnet-wide via Serve `8449`. Identify the
app; confirm Flask `debug=False` (else it is a remote RCE console); put auth in front OR drop the
serve mapping: `tailscale serve --https=8449 off`.

### M3 🔴⚠ — sshd password authentication
All `sshd_config` lines are commented → default `PasswordAuthentication yes` on all interfaces.
```
# confirm every host you log in FROM has its key in ~/.ssh/authorized_keys FIRST, then:
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' | sudo tee /etc/ssh/sshd_config.d/10-no-password.conf
sudo systemctl reload ssh
```

### H3 🔴⚠ — `NOPASSWD` sudo `git` as clawd-house (code-exec as clawd-house)
`git` is not a safe sudo verb (`-c core.pager=…`, hooks, aliases → arbitrary exec). Replace the
`sudoers` rule with a wrapper that runs only the exact fixed invocation the service user needs (likely
committing to `/opt/clawd-house/docs`), owned by clawd-house, not writable by the service user, rejecting any
`-c`/`--upload-pack`/user-supplied args. Or drop the rule if the commit path can move.

### ~~Stale~~ LIVE Serve entry 🔴 — `:8445 → :3100`, open-wearables frontend
Was recorded here as harmless (502s) because the frontend was believed stopped. **Re-measured
2026-09-05: it is running**, on `0.0.0.0:3100`, answering 200 unauthenticated, so this mapping
publishes it to the tailnet rather than 502-ing. See the corrected M2 entry in Part 3 for the
loopback bind and the container bounce that applies it. Decide there whether the mapping should
stay; if not, it still needs sudo (the service user's `tailscale serve` is access-denied):
`sudo tailscale serve --https=8445 off`

### L2 — Vite dev server (:5173) served to the tailnet via Serve 8443
Source/source-map/FS exposure. **Stopping the service does not stop the dev server, and that is the
finding.** Measured 2026-09-05: `systemctl --user stop workshop-frontend.service` did take `:5173`
down — and `scripts/frontends_watchdog.sh`, which cron runs every two minutes, brought it back 60
seconds later. The log line is explicit:

```
[2026-09-05T08:12:01-07:00] frontend 'workshop' down on :5173 — restarting (no systemd unit)
[2026-09-05T08:12:01-07:00]   relaunched 'workshop'
```

Two things make this worse than a plain restart. It took the **fallback** branch — cron's
environment has no `DBUS_SESSION_BUS_ADDRESS`, so the watchdog's `systemctl --user show -p LoadState`
probe returns empty, it concludes there is no unit, and it relaunches vite with `setsid nohup`
**outside systemd**. The result is the "two owners for one port" state the watchdog's own header
warns about: `systemctl --user show -p ActiveState` says `inactive` while `:5173` is served by a bare
`node .../node_modules/.bin/vite` parented to the watchdog. Anyone checking the unit concludes the
dev server is off. It also comes up **without the unit's `EnvironmentFile`**, so it has none of the
`WORKSHOP_CANONICAL_ORIGIN` / `WORKSHOP_TAILNET_HOST` values `vite.config.ts` needs for host
acceptance — the relaunch is not even equivalent to the unit.

**So the opt-out had to go where the resurrection happens, and now it is there.**
`scripts/frontends_watchdog.sh` reads `WORKSHOP_DEV_SERVER` (via `scripts/lib/instance.sh`, because
cron passes almost no environment) and skips the dev server entirely when it is `0`, logging that it
did. Default stays `1`, so a fresh clone is unaffected. Both branches tested.

**Remaining, and now it is three lines rather than host archaeology:**

1. **Turn the knob on** — add `WORKSHOP_DEV_SERVER=0` to `~/.config/workshop/instance.env`. An agent
   cannot: that directory is bind-mounted **read-only** into the turn sandbox (writes fail with
   `Read-only file system`).
2. **Stop it once** — `systemctl --user stop workshop-frontend.service` and kill any bare
   `node .../.bin/vite` still holding `:5173`. With the knob set, the watchdog leaves it down.
   Also `systemctl --user disable workshop-frontend.service` so it does not return at boot; an agent
   cannot, because the systemd user unit directory is not in its mount namespace and the file-touching
   verbs (`disable`, `is-enabled`, `list-unit-files`) fail for *every* unit, `docker.service`
   included, while `systemctl --user show` still answers through the manager.
3. **Drop the mapping** — `sudo tailscale serve --https=8443 off`. Needs root; the service user's
   `tailscale serve` is access-denied and `tailscaled.sock` is not reachable as that user.

To put the dev origin back at any time: delete the `WORKSHOP_DEV_SERVER` line (or set it to `1`) and
`systemctl --user start workshop-frontend.service`.

Not a substitute, and worth recording so nobody reaches for it as one: `server.fs.strict` already
defaults to **true** in Vite 5 (this tree is on 5.4.21), so setting it in `frontend/vite.config.ts`
changes nothing. Probing the running dev server for arbitrary reads bore that out — `/@fs/` requests
for `backend/services/ownership.py` and for files under `.runtime/users/` all returned the 5089-byte
SPA `index.html`, not the file. The exposure this finding is really about is the app's own
TypeScript source and source maps (`/src/main.tsx` did serve real transformed source), not
arbitrary filesystem read.

### L5 🔴 — Samba anon enumeration + rpcbind
`smbclient -L -N` leaks share/user names. Add `restrict anonymous = 2` to `/etc/samba/smb.conf` and
bind smbd to the intended interface; `sudo systemctl disable --now rpcbind` if NFS is unused.

---

## Part 3 — Other stack owner items

### M2 (secrets half) — open-wearables secrets
The exposed/crash-looping containers are now stopped (the service user did this). Before restarting the stack,
move the vendor OAuth / AWS / `ADMIN_PASSWORD` / `SECRET_KEY` / `RESEND_API_KEY` values out of the
committed compose env into a non-committed `env_file` (0600) or a secret store. Compose lives at
`~/workshop/infra/open-wearables/repo`.

**Re-measured 2026-09-05. The secrets half of this was already true, and the paragraph above was
wrong in both directions.** Correcting it rather than deleting it, because "move the secrets out of
the committed compose" is the instruction that would have been carried out otherwise, and there is
nothing there to move.

*Secrets — already satisfied, and nothing was ever committed.* Every value the finding names
(`WHOOP*`, `OURA*`, `FITBIT*`, `GARMIN*`, `STRAVA*`, `SUUNTO*`, `POLAR*`, `ULTRAHUMAN*`, `AWS_*`,
`ADMIN_PASSWORD`, `RESEND_API_KEY`, `SECRET_KEY`) appears **only** in `backend/config/.env`, which
both compose files already consume via `env_file:`. Zero of them appear in `docker-compose.yml` or
`docker-compose.prod.yml`; the single `SECRET_KEY` match in each is `$${SVIX_JWT_SECRET:=$$SECRET_KEY}`,
a shell default inside a container `command`, not a value. `backend/config/.env` is untracked in the
nested repo *and* the whole `infra/open-wearables/repo/` tree is gitignored by the parent, and the
nested history has only ever carried `.env.example` files
(`git log --all --pretty=format: --name-only | grep '\.env$'` finds none). **So there is no
credential rotation owed for exposure through either repo.** The one real gap was the file mode:
`backend/config/.env` was `644` and is now `600`. The local edits to `docker-compose.yml` are the
operator's own host-port remapping (`DB_PORT_HOST`, `REDIS_PORT_HOST`, `VITE_API_URL`) and contain no
secrets.

*Crash-looping backend/svix — closed.* `app` (8100) and `svix-server` (8072) are not listening and no
container is looping.

*All-interfaces exposure — was still open, config now fixed, container still needs a bounce.* The
claim that the frontend is stopped is stale: `frontend__open-wearables` was live on **`0.0.0.0:3100`
and `[::]:3100`**, answering 200 unauthenticated. Upstream publishes every port with no host IP, so
all of them bind `0.0.0.0` when they come up. Added `docker-compose.override.yml` in that checkout
(auto-loaded, leaves the vendored file untouched) prefixing `127.0.0.1:` to **db, redis, flower,
svix-server and frontend**. Tailnet access via Serve `:8445 -> :3100` is unaffected, because Serve
connects from loopback. **This takes effect only on the next `docker compose up -d`** — compose does
not re-bind a container that already exists — so `:3100` stays LAN-visible until the operator
recreates it:
`cd ~/workshop/infra/open-wearables/repo && docker compose up -d frontend` (or `docker compose down`
if the stack is not wanted at all). An agent cannot: the rootless docker socket is outside its mount
namespace.

*Left for the operator to decide, deliberately not changed.* The `app` service (API, host 8100) is
**not** in the override. The root `.env` sets `VITE_API_URL` to a LAN host on purpose — the SPA runs
in an off-host browser and calls that URL directly — so binding the API to loopback breaks the app
for anything not on this machine unless `VITE_API_URL` is repointed through the tailnet in the same
change. That is an access-model decision, not hygiene.

Also stale, and for the same reason: the "Stale Serve entry `:8445 -> :3100`" item above is **not**
stale. It points at a frontend that is running.

### SEC-G1 — Fine-grained GitHub PAT (approved) 👤
Move the the excluded work org boundary into the credential, not just the code. The current global git credential
is a **classic PAT** (`repo, read:org`) — broad enough to reach `<blocked-org>`.
1. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate: **Resource owner =
   your personal account only** (NOT any org), Repository access = All personal repos (or select),
   Permissions → Repository → **Contents: Read and write**, **Metadata: Read** (add Pull requests:
   Read/write if the auto-PR flow is used). No org/`read:org` scope.
2. Re-point gh to it: `echo <NEW_TOKEN> | gh auth login --with-token` (or
   `gh auth refresh`), then `gh auth status` should show the fine-grained token.
3. `git -C ~/workshop push` (or a project push) once to confirm the helper works.
This drops `read:org` and makes the excluded work org unreachable by the token itself, not only by `reject_blocked`
in code.

### M1 — Letta `/v1` password (low priority)
the service user's letta `:8283` is loopback-only and the password is weak + visible to docker-group members
(root). Enforcing it has near-zero value until Part 1. simonsays's `letta-simonsays` `:8284` is
theirs to enforce if desired.

**Re-measured 2026-09-05, and the service user's half is no longer reachable at all.** `:8283` is not
in `ss -tln` and `curl http://127.0.0.1:8283/v1/health/` fails to connect. The `letta server --host
0.0.0.0 --port 8283` process is still running in a container, but that port is not published to the
host, so the loopback reach the finding describes is closed for `:8283` — by the port not being
published, not by the password.

What is still open is the other half, and it is not the service user's to close:
`curl -o /dev/null -w '%{http_code}' http://127.0.0.1:8284/v1/agents/` returns **200
unauthenticated**, and `/v1/health/` reports Letta 0.16.8. Since loopback is shared between host
users, any host user reaches simonsays's agent-memory API. That server belongs to simonsays and
needs the flag that actually requires `LETTA_SERVER_PASS` plus a restart, done by them. Note also
that the agent cannot restart the containers either way: the rootless docker socket
(`/run/user/1003/docker.sock`) is outside its mount namespace, so `docker ps` cannot connect.

There is nothing to change in this repo for this finding — `infra/letta/.env` holds only Postgres
and Ollama settings, and no compose file for either Letta server lives here.

---

## Quick status

Applied by the service user already (no root): edge `/auto-pr` block, `/tools` LAN-trust narrowing, backend
bound to loopback, registration fail-closed, `/docs` off, `auth.db`/`letta.env` perms, WebFetch
egress coverage, recursive-claude clamps + depth guard, app-container `no-new-privileges`, mobile
bridge write-only + origin-lock + debug gating, ~~open-wearables exposure stopped~~ (see M2: the
frontend was still live on `0.0.0.0:3100` on 2026-09-05), L2 dev-server opt-out built (`:5173` is
still up until the knob is set — see L2).

A status line saying a thing is stopped is worth exactly as much as the last time somebody ran
`ss -tln`. Two entries in this file said "stopped" about a port that was listening. Re-measure before
trusting any line here.

Highest-value remaining: **Part 1** (rootless cutover — the unbreakable agent boundary) and **C3**
(verify IPv6 firewall). Everything else is contained or lower-severity.
