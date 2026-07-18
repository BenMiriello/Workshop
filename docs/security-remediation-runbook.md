# Security remediation runbook — operator actions — 2026-07-18

Companion to `security-audit-2026-07-18.md` (+ `-agents.md`). The audit pass applied everything that
was safe for `clawd-a` to change alone (see the "Fixes applied" section of the main report). This
runbook is the remainder: items that need **root** (`benmiriello`/`sudo`), another **stack owner**, or
an **architectural decision**. Commands are exact; read the note before each.

Legend: 🔴 root required · 👤 another user's service · 🏗 architecture decision · ⚠ changes usage.

---

## Part 1 — The unbreakable agent boundary (A1 / C1) 🏗🔴⚠

**Goal:** agents keep the ability to run apps as containers, but a compromised/injected agent turn
can no longer become *host root* or cross into other users' data. Today it can, because agent turns
run as `clawd-a` in the `docker` group (root-equivalent socket) with no sandbox.

**Recommended fix: rootless Docker for the app-runner + drop clawd-a from the `docker` group.**
Rationale: with a rootless daemon, "root in a container" maps to `clawd-a` on the host (via the
user-namespace subuid range), not real root — so `docker run -v /:/host` can read only what clawd-a
can already read, and cannot touch `/etc/shadow`, other users' `0700` homes, or escalate. Removing
the `docker` group drops access to the shared *root* daemon, which also restores the
clawd-a↔simonsays cluster boundary at the docker layer.

Feasibility here is good — prereqs already present: `/etc/subuid` + `/etc/subgid` have a clawd-a range
(`10231073:65536`), `newuidmap`, `slirp4netns`, `dockerd-rootless-setuptool.sh` installed, linger on.

**Two prerequisites before cutover:**
1. 🔴 `sudo apt install fuse-overlayfs` — WITHOUT it, rootless Docker falls back to the `vfs` storage
   driver, which duplicates every image layer. The host root fs is at **~89%** — a vfs daemon could
   fill it (there is a prior disk-full incident). Do not run the cutover on vfs.
2. Confirm disk headroom after: `df -h /`.

**STATUS 2026-07-18 — cutover LARGELY DONE, one root step remains.** The first attempt ran the
rootless setup + backend steps as `benmiriello` by mistake, but `sudo gpasswd -d clawd-a docker`
still executed. clawd-a (as itself) has since: installed `fuse-overlayfs` (done by operator),
installed + started **rootless docker for clawd-a** (`/run/user/1003/docker.sock`, overlayfs, linger
on), repointed `workshop-backend.service` to that socket (drop-in no longer uses `sg docker`), and
restarted it — backend is healthy, loopback-bound, and `container_runtime.available()` is True, so the
app runner works on rootless. **Boundary verified on the rootless daemon:** a container's
`cat /etc/shadow` and reading simonsays's `0700` home both return Permission denied.

⚠ **REMAINING (root):** the running systemd user-manager `user@1003.service` was started (at boot)
while clawd-a was still in `docker`, so it and its child services (incl. the backend, and any agent
turn the backend spawns) still carry the stale `docker` group and can still reach the root socket.
Flush it:
```
sudo systemctl restart user@1003.service      # or: reboot
```
After that: `grep Groups /proc/$(systemctl --user show -p MainPID --value workshop-backend.service)/status`
should NOT list `994`, and the boundary is fully closed. (Cron-spawned agent turns already lack the
stale group — they get groups fresh from PAM — so only backend-spawned turns are the residual until
the flush.) Two follow-ups: **migrate `ws_gengui`** off the root daemon onto rootless (it still runs
on the root daemon; the app runner will rebuild it on rootless when next started — or remove the old
container and restart the app from the UI), and a **stray rootless docker daemon was created for
`benmiriello`** during the misfire (`~benmiriello/.config/systemd/user/docker.service`) — harmless,
disable it with `systemctl --user disable --now docker` as benmiriello if unintended.

**Confirmed facts for this host (2026-07-18):** `unprivileged_userns_clone=1`, `max_user_namespaces`
high → rootless is viable; 192G free; **only ONE** clawd-a app-runner container is on the root daemon
(`ws_gengui-frontend-1`), so the migration is that single app. `dockerd-rootless-setuptool.sh check`
currently aborts "rootful Docker is accessible" — expected; that check passes once the group is
dropped (step 5), or run it with `--force` to preview.

**Cutover steps (do in one sitting; run as `clawd-a` except the two 🔴 root lines):**
```
# 0. 🔴 (root) install fuse-overlayfs so rootless uses overlay, not the disk-heavy vfs driver
sudo apt-get install -y fuse-overlayfs

# 1. (clawd-a) install + start the rootless daemon (does NOT touch the system daemon)
dockerd-rootless-setuptool.sh install --force
systemctl --user enable --now docker
export DOCKER_HOST=unix:///run/user/1003/docker.sock
docker info --format '{{.SecurityOptions}}'   # expect rootless + overlay2 (not vfs)
docker run --rm hello-world                    # verify

# 2. Point the backend at the rootless socket. Edit
#    ~/.config/systemd/user/workshop-backend.service.d/docker-group.conf :
#      - drop the `sg docker -c "..."` wrapper; ExecStart becomes a plain:
#        ExecStart=/home/clawd-a/workshop/backend/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
#      - add: Environment=DOCKER_HOST=unix:///run/user/1003/docker.sock
systemctl --user daemon-reload && systemctl --user restart workshop-backend.service

# 3. Migrate the one app to the rootless daemon:
#    - on the ROOT daemon:  docker rm -f ws_gengui-frontend-1   (or `compose down` for its project)
#    - re-launch it through the Workshop app runner (which now targets the rootless socket),
#      or `docker --host unix:///run/user/1003/docker.sock compose -p ws_gengui up -d` from its dir.
#    Confirm the app is healthy on rootless before continuing.

# 4. Confirm the backend app runner works on rootless (create/stop a throwaway app from the UI).

# 5. 🔴 (root) drop the group — now the raw root socket is gone for clawd-a:
sudo gpasswd -d clawd-a docker
#    Then restart clawd-a's user services / re-login so running processes lose the group.
```
**Note the app runner keeps working** — it just talks to the rootless socket instead of the root one;
agents keep the ability to run apps. What changes is that container-root now maps to `clawd-a`, not
host root.
**Verify the boundary held:** as clawd-a, `docker run --rm -v /home:/mnt alpine cat /mnt/simonsays/…`
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
Not a clawd-a container (a bare `docker run`, owner benmiriello/simonsays). Recreate it bound to
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
`sudoers` rule with a wrapper that runs only the exact fixed invocation clawd-a needs (likely
committing to `/opt/clawd-house/docs`), owned by clawd-house, not writable by clawd-a, rejecting any
`-c`/`--upload-pack`/user-supplied args. Or drop the rule if the commit path can move.

### Stale Serve entry 🔴 — `:8445 → :3100` points at the now-stopped open-wearables frontend
Harmless (502s), but tidy it up (needs sudo — clawd-a's `tailscale serve` is access-denied):
`sudo tailscale serve --https=8445 off`

### L2 — Vite dev server (:5173) served to the tailnet via Serve 8443
Source/source-map/FS exposure. Either `tailscale serve --https=8443 off`, or point it at the built
bundle. If kept, set `server.fs.strict: true` in `frontend/vite.config.ts`.

### L5 🔴 — Samba anon enumeration + rpcbind
`smbclient -L -N` leaks share/user names. Add `restrict anonymous = 2` to `/etc/samba/smb.conf` and
bind smbd to the intended interface; `sudo systemctl disable --now rpcbind` if NFS is unused.

---

## Part 3 — Other stack owner items

### M2 (secrets half) — open-wearables secrets
The exposed/crash-looping containers are now stopped (clawd-a did this). Before restarting the stack,
move the vendor OAuth / AWS / `ADMIN_PASSWORD` / `SECRET_KEY` / `RESEND_API_KEY` values out of the
committed compose env into a non-committed `env_file` (0600) or a secret store. Compose lives at
`~/workshop/infra/open-wearables/repo`.

### SEC-G1 — Fine-grained GitHub PAT (approved) 👤
Move the veritus boundary into the credential, not just the code. The current global git credential
is a **classic PAT** (`repo, read:org`) — broad enough to reach `veritusagent`.
1. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate: **Resource owner =
   your personal account only** (NOT any org), Repository access = All personal repos (or select),
   Permissions → Repository → **Contents: Read and write**, **Metadata: Read** (add Pull requests:
   Read/write if the auto-PR flow is used). No org/`read:org` scope.
2. Re-point gh to it: `echo <NEW_TOKEN> | gh auth login --with-token` (or
   `gh auth refresh`), then `gh auth status` should show the fine-grained token.
3. `git -C ~/workshop push` (or a project push) once to confirm the helper works.
This drops `read:org` and makes veritus unreachable by the token itself, not only by `reject_veritus`
in code.

### M1 — Letta `/v1` password (low priority)
clawd-a's letta `:8283` is loopback-only and the password is weak + visible to docker-group members
(root). Enforcing it has near-zero value until Part 1. simonsays's `letta-simonsays` `:8284` is
theirs to enforce if desired.

---

## Quick status

Applied by clawd-a already (no root): edge `/auto-pr` block, `/tools` LAN-trust narrowing, backend
bound to loopback, registration fail-closed, `/docs` off, `auth.db`/`letta.env` perms, WebFetch
egress coverage, recursive-claude clamps + depth guard, app-container `no-new-privileges`, mobile
bridge write-only + origin-lock + debug gating, open-wearables exposure stopped.

Highest-value remaining: **Part 1** (rootless cutover — the unbreakable agent boundary) and **C3**
(verify IPv6 firewall). Everything else is contained or lower-severity.
