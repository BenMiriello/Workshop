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

**Cutover steps (do in one sitting, verify each):**
```
# 1. (as clawd-a) install + start the rootless daemon
dockerd-rootless-setuptool.sh install
systemctl --user enable --now docker            # user-level dockerd
docker --context=rootless run --rm hello-world  # verify it works

# 2. Point the backend at the rootless socket instead of the root socket.
#    Edit the drop-in: ~/.config/systemd/user/workshop-backend.service.d/docker-group.conf
#    - remove the `sg docker -c "..."` wrapper (no longer need the group)
#    - add:  Environment=DOCKER_HOST=unix:///run/user/1003/docker.sock
#    - ExecStart back to a plain: .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
systemctl --user daemon-reload && systemctl --user restart workshop-backend.service

# 3. Recreate any clawd-a app-runner apps on the rootless daemon (they currently live on the
#    root daemon). For each app: bring it down on the old daemon, `up` via the app runner.

# 4. 🔴 Only after the app runner is confirmed working on rootless: drop the group.
sudo gpasswd -d clawd-a docker
#    Log clawd-a out / restart its user services so running processes lose the group.
```
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
