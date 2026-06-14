# Bird-Station Pi — Complete Context, Work Log & Handoff

**Status:** mid-work, 2026-06-12 ~15:20 PT. Root cause fixed (lossy transport → lean lossless transport); end-to-end live detection not yet observed (timing-dependent, dawn is the validation window). Author: Claude (clawd-a session). This is the full engineering record — read it before resuming.

---

## TABLE OF CONTENTS
1. Goal & requirements
2. Hardware & network topology (verified facts)
3. Chronological work log (the trial-and-error, with commands + measurements)
4. Current end-state architecture
5. Config & unit files (verbatim)
6. Security: ACL before/after, hardening steps
7. Measurement appendix (all data tables)
8. Citations / web research
9. Proven vs. NOT-yet-proven
10. Open items, decisions, next steps
11. Operational gotchas & cheatsheet
12. App-side reference

---

## 1. GOAL & REQUIREMENTS

Always-on Raspberry Pi in the backyard, USB mic, streams audio continuously to the main server ("the Brain" = this Debian box) which runs BirdNET detection and surfaces results in the Workshop `/apps/birds` app, with the Pi appearing as a registered **device** ("Yard Mic"). Operator's hard requirements:
- **Reliable / self-restarting:** flip-the-switch; survives a power-cycle with zero manual steps.
- **Secure:** reachable from the operator's own devices over the tailnet; **cannot pivot** to other devices if compromised; holds **no credentials**; limited to "only stream audio."
- **Low-maintenance.**
- The operator can SSH in from any of their devices (not just this one).
- Detection threshold integrity matters: do **not** lower it to force detections through (operator set 0.5 deliberately to suppress false positives).

---

## 2. HARDWARE & NETWORK TOPOLOGY (verified)

**Pi:** Raspberry Pi **Zero W Rev 1.1** (`/proc/device-tree/model`). SoC BCM2835, **single-core ARM1176JZF-S (ARMv6) @ 1 GHz**. CPU `Features: half thumb fastmult vfp edsp java tls` → **no NEON (no SIMD), no AES/crypto extensions**, only VFP. RAM 430 MB. OS Raspbian GNU/Linux 10 (buster), kernel 5.10.103+ (March 2022), **EOL**. This weakness drives every decision below.

**Mic:** USB audio dongle, `lsusb` ID `1b3f:2008 Generalplus Technology`. ALSA: `card 1: Device [USB Audio Device]`, capture device `plughw:1,0` (or `hw:1,0`). `--dump-hw-params`: **FORMAT S16_LE, CHANNELS 1 (mono), RATE [44100 48000]** → 48 kHz mono needs no resampling. Mixer: `Mic Capture Switch` = on; `Mic Capture Volume` range 0–28, set to **24** (+24 dB); `Auto Gain Control` = **off**; persisted with `sudo alsactl store`. `pi` user is in the `audio` group.

**Brain (this box):** hostname `bgmdebian`; tailnet name `debian`; tailnet IP `100.104.196.85`; LAN `192.168.1.77` (enp5s0/eth) and `192.168.1.155` (wlo1/wifi). Linux user `clawd-a` (uid 1003, gid 995-ish), **NO sudo**. Tailscale 1.98.4.

**Tailnet:** `tail38e62e.ts.net`, owner `benmiriello@gmail.com`. **Pi and Brain are on the SAME tailnet** (the inbound handoff prompt wrongly claimed different tailnets — corrected at the very start by `tailscale status`). Other devices on the tailnet: `mba` (Ben's MacBook Air, 100.65.64.16), `iphone` (100.72.20.109), `ipad` (offline 306d). Pi↔Brain data path is **direct** over the LAN (`tailscale ping debian` from the Pi → `pong ... via 192.168.1.77:41641`), NOT DERP-relayed.

**Pi tailnet identity:** name `raspberrypi`, IPv4 `100.84.51.96`, IPv6 `fd7a:115c:a1e0::4a35:3360`, MagicDNS `raspberrypi.tail38e62e.ts.net`, device id `3992383984602446`, tag `tag:bird-pi`, `keyExpiryDisabled=true`.

---

## 3. CHRONOLOGICAL WORK LOG

### Phase 0 — Establishing access (was the first real obstacle)
- `tailscale status` on the Brain showed `raspberrypi 100.84.51.96` on the same tailnet → corrected the handoff's "different tailnet" premise immediately. `tailscale ping raspberrypi` → pong (9 ms direct via `192.168.1.76`, plus DERP fallback).
- Found via `tailscale status --json`: Pi `KeyExpiry: 2026-06-15` (**3 days away** — node would have silently dropped off), `Tags: None`.
- SSH attempts **hung**. Verbose SSH (`ssh -v`) showed `Remote software version Tailscale` on port 22 → port 22 is **Tailscale SSH**, not OpenSSH (another handoff error — it claimed raw-IP hits OpenSSH-with-password). Connection negotiated KEX then stalled = Tailscale SSH **`check` mode** (wants a browser re-auth a headless agent can't do). Captured the proof: `tailscale ssh pi@raspberrypi` emitted `# Tailscale SSH requires an additional check. To authenticate, visit: https://login.tailscale.com/a/...`.
- Port probe of the Pi: only **22 open** (80/443/8000 closed → no web service).
- **Blocker:** to fix this durably (and to tag the Pi / write the ACL / kill the 3-day expiry) needed the Tailscale **admin API**, which needs a token. Asked the operator; got a token (`tskey-api-...`), stored 0600 at `~/.config/clawdbot/tailscale-api.env`.
- **Second blocker:** the clawd-a **egress-guard** hook (`~/.claude/hooks/egress-guard.py`, anti-exfiltration) blocks shell curl to external hosts incl. `api.tailscale.com`. I did NOT bypass it via python-urllib (the guard documents that residual; using it right after a block would be circumvention). Asked the operator; they chose "allowlist the host." Added `api.tailscale.com` to an `ALLOWLIST` set in the guard (narrow, operator-authorized; near-zero exfil risk — it's an auth-gated control endpoint).

### Phase 1 — Tailnet security (API-driven)
- `GET /api/v2/tailnet/-/acl` → the tailnet uses the **grants** model (not legacy `acls`), default wide-open: `grants:[{src:["*"],dst:["*"],ip:["*"]}]`, ssh: one `check` rule (`autogroup:member → autogroup:self`). Backed it up to `~/.config/clawdbot/ts-acl-backup-2026-06-12.hujson`.
- **Ordering to avoid lockout:** wrote the new policy WITH an `accept`-mode SSH rule for `tag:bird-pi` FIRST, validated (`/acl/validate` → `{}`), applied, THEN tagged the Pi. (If I tagged first, the Pi would leave `autogroup:self`, no SSH rule would match, and I'd be locked out — exactly the handoff's warning.)
- Tagged the Pi (`POST /device/3992383984602446/tags {"tags":["tag:bird-pi"]}`). Tag took, but `keyExpiryDisabled` stayed false → set it explicitly (`POST /device/.../key {"keyExpiryDisabled":true}`). Verified `keyExpiryDisabled: true`. **3-day clock killed.**
- **Headless SSH now works** (`ssh pi@raspberrypi 'id'` → no browser check). Confirmed `armv6l`, Raspbian 10.

### Phase 2 — Inventory (idempotent baseline)
Findings: passwordless sudo for `pi`; a leftover `appsvc` user + empty `/opt/myapp` (scaffolding from the other agent — removed since empty); **USB mic present** (card 1); **ffmpeg + mediamtx both MISSING**; credentials clean (no `~/.git-credentials`, no `~/.ssh`); **LAN-exposed services on 0.0.0.0**: sshd:22, VNC `vncserver-x11-serviced`:5900, CUPS:631; boot target `graphical.target` w/ autologin; `unattended-upgrades` NOT installed; `RunSSH: true`. Repo already had a planned streamer in `~/workshop/birdnet/pi/` (mediamtx.yml, mediamtx.service, setup.sh) and brain config in `~/workshop/birdnet/brain/`.

### Phase 3 — Audio base + first streamer (mediamtx + PCM)
- `apt-get update`: **`raspbian.raspberrypi.org` repo is 404** (EOL); `archive.raspberrypi.org` still serves. Installed `ffmpeg` (4.1.11) from the latter.
- Mic test: recorded 5–6 s, format pcm_s16le/48k/mono confirmed; level **mean −57.9 dB** indoors with someone near it → mic responds to sound. Bytes varied (not silence).
- Created non-root system user `birdstream` (in `audio`), `/opt/birdstream/` owned by it. Downloaded **mediamtx v1.9.3 armv6** binary on the Pi (github release; the Pi's own internet, not via the guarded Brain shell).
- Deployed adapted config: dropped the repo's `afftdn` FFT denoise (too heavy for armv6), kept `highpass=f=80`; ran mediamtx as non-root; rsync'd config via the **FQDN** (`pi@raspberrypi.tail38e62e.ts.net:`). First start failed: `ERR: json: unknown field "rtspTransports"` → v1.9.3 uses `protocols:` (renamed later). Fixed → mediamtx published `pcm_s16be` to path `yard`, listening `:8554`.
- **CPU at this point:** mediamtx 27% + ffmpeg 45% ≈ 73% of the single core (with a reader). Local ffprobe + a Brain-side pull both returned `pcm_s16be / 48000 / mono`. End-to-end Pi→tailnet→Brain proven.

### Phase 4 — Hardening
- Disabled `vncserver-x11-serviced`, `cups`/`cups.socket`/`cups.path`/`cups-browsed` → 5900/631 no longer listening.
- `systemctl set-default multi-user.target` + disabled lightdm → console boot (tty1 autologin `pi` kept as the local console safety-net).
- OpenSSH: appended `PasswordAuthentication no`, `PermitRootLogin no`, `KbdInteractiveAuthentication no` to `/etc/ssh/sshd_config` (buster's OpenSSH 7.9 has no `sshd_config.d` include), `sshd -t` OK, restarted. (Tailscale SSH is unaffected — separate from OpenSSH. No authorized_keys exist → OpenSSH is effectively unauthenticatable on the LAN.)
- **unattended-upgrades: deliberately NOT enabled** — buster is EOL, primary repo 404, near-useless + risks hiccupping the stream. Real fix = reflash to Bookworm (deferred, physical job). Flagged.
- **`ufw` could not be installed** (only in the dead repo, 404). Substitute: bound the streamer to the **tailnet IP only** so the audio isn't LAN-reachable (privacy: yard audio can catch conversation). Verified: `192.168.1.76:8554` → Connection refused; `raspberrypi:8554` (tailnet) → works.
- Observed: the Pi got sluggish under management (`systemctl list-unit-files` timed out at load ~5). First sign the box is easily saturated.

### Phase 5 — ACL lockdown + cold-boot test (security gate)
- **Cold-boot test PASSED:** issued `systemctl reboot`, polled from the Brain — Pi back in ~90 s, `tailscaled` active, `birdstream` active, boot target `multi-user.target`, `:8554` bound to the tailnet IP, stream pullable again. **Zero manual steps.**
- **ACL lockdown applied** (validated then POSTed): grants changed from `src:["*"]` to `src:["autogroup:member"]` + a self-only grant for the Pi. Verified from the Pi: TCP to `debian:8000` **blocked**, `debian:22` **blocked**, while the Brain can still pull `:8554` and my SSH still works. (`tailscale ping debian` still pongs — that's a control-plane disco probe, not application traffic; the actual TCP service connections are denied, which is the security property.)

### Phase 6 — Brain bridge + app device registration
- BirdNET-Go was NOT installed (no binary, no `birdnet.db`) — the 2026-06-06 plan was never deployed. Backend running on `:8000`, analyzer on `:8012`. **Decision:** feed the existing **browser `/birds/analyze` pipeline** instead of installing BirdNET-Go, because that pipeline owns the device/location registry + trust model + recordings, so the Pi becomes a real **device** (matching "registered as a device on the app"); BirdNET-Go's yard path would not.
- Read the endpoint contract: `POST /birds/analyze (audio, token, device_name, mode, lat, lon, location_id, min_conf, station)`; `POST /birds/device (token, name, mode, fixed_location_id, ...)`.
- Registered device: token `yard-pi-springer-01`, name `Yard Mic`, mode `fixed`, `fixed_location_id=6` ("1849 South Springer Road, Los Altos", lat 37.369721 lon -122.0956962 — the established yard, 215 prior detections).
- Built bridge `~/workshop/birdnet/bridge/yard_bridge.py` + **systemd --user** service `birds-yard-bridge.service` (clawd-a has no sudo, but `Linger=yes` makes user services boot-start + Restart=always). Bug caught: `Environment=YARD_DEVICE=Yard Mic` split on the space → device became "Yard"; fixed by quoting; re-asserted the name; confirmed no phantom device.

### Phase 7 — "No detections" debugging (the hard part)
Symptom: bridge healthy, posting, `last_seen` advancing — but **zero detections** despite the operator hearing definite birds.

- **Discovery 1 — throughput collapse.** Bridge heartbeat: **37 chunks in 5 min** (~37% of realtime, should be ~100). A 20 s capture request returned only **1.05 s** of audio. So the stream was arriving at ~37% realtime; each "3 s chunk" was ~8 s of audio with gaps removed → BirdNET fed temporally shredded audio → nothing.
- **Discovery 2 — the Pi was pinned.** Pi `/proc/loadavg` = **5.2** on one core. `top`: `tailscaled` 31%, `mediamtx` 19–30%, `ffmpeg` 17–45%. Logs: **`ALSA buffer xrun`** (capture dropping samples) + repeated **`[RTSP] write queue is full`**. Even with NO reader, baseline was ffmpeg 35% + mediamtx 30% = **65% CPU** (pure capture + RTP loopback publish, zero crypto).
- **Fix attempt A — Opus.** Switched ffmpeg to `libopus 128k`. mediamtx 30%→**4.5%**, realtime restored (20 s pull = 20 s with 2 readers). BUT (see Discovery 3) lossy.
- **Discovery 3 — Opus degrades detection.** Spectrograms of the live Opus stream were noise-only. I initially (wrongly) concluded "the mic is too weak / physical." **Disproven** by a **direct ALSA capture on the Pi** (bypassing mediamtx/Opus/network): it cleanly detected **Golden-crowned Kinglet 0.68** (a real Los Altos bird; faint high-freq ~8–10 kHz traces visible in the direct spectrogram). So the mic is fine; the lossy stream was the culprit.
- **Deterministic proof (Opus is lossy-fatal for faint calls):** took the known-good direct capture (kinglet 0.68) and round-tripped it through codecs offline:

  | Encoding | Kinglet confidence |
  |---|---|
  | Lossless original | **0.68** |
  | Opus 128k cx10 | 0.44 |
  | Opus 256k cx10 | 0.50 |
  | Opus 320k cx10 | 0.50 (caps) |
  | FLAC (lossless) | **0.68** |

  Opus costs ~0.18 on faint high-freq calls regardless of bitrate (caps ~0.50); the 0.5 floor then drops the survivors. Lossless is required. (Operator was right to reject "better compression.")
- **Bottleneck, web-verified (Phase 8).** A Pi Zero W does ~50 Mbit/s over WireGuard; our stream is 0.77 Mbit/s = **1.5% of that** → bytes were never the wall. The cost is **per-packet overhead on a no-NEON core**: (a) mediamtx/RTP machinery (65% CPU, no crypto) packetizing tiny RTP packets through a loopback double-hop; (b) `tailscaled` userspace `wireguard-go` (confirmed: `tun` device, **no kernel wg module loaded**), worsened by RTP's tiny-packet/high-rate pattern.

### Phase 8 — THE FIX: lean raw PCM over plain TCP (mediamtx dropped)
- New Pi service `birdstream-lean.service`: one ffmpeg, `-f alsa ... -i plughw:1,0 -f s16le tcp://100.84.51.96:9000?listen=1`. Big packets, no RTP, no codec, lossless. Disabled the old mediamtx `birdstream`.
- **Measured results:**

  | Metric | mediamtx/RTP/PCM | Opus 128k | **lean raw/TCP** |
  |---|---|---|---|
  | Pi load | 5.0 | ~2 (decaying) | **0.61** |
  | mediamtx CPU | 30% | 4.5% | **0 (gone)** |
  | ffmpeg CPU | 35% | 27–34% | **29%** |
  | tailscaled CPU | 31% | 19–28% | **31%** |
  | Throughput | ~37% realtime | realtime | **100% (25 s=25.0 s; 9.0 s/10 s)** |
  | Audio | lossless but shredded | lossy (0.68→0.44) | **lossless, intact** |
  | ALSA xruns | yes | — | **none** |

- Rewired the bridge: `YARD_SRC=tcp://raspberrypi:9000`, ffmpeg input `-f s16le -ar 48000 -ac 1 -i tcp://raspberrypi:9000`. Restarted; **0 reconnects** (stable), `last_seen` advancing, realtime confirmed.
- `tailscaled` ~31% is the irreducible residual (userspace WireGuard on a no-NEON core); Pi has ~40% headroom and runs clean. The lean fix solves BOTH the overload AND the lossy-degradation problem.

---

## 4. CURRENT END-STATE ARCHITECTURE

```
[USB mic plughw:1,0]
   -> ffmpeg (ALSA capture, raw s16le 48k mono)         PI: birdstream-lean.service (user birdstream)
   -> plain TCP listen 100.84.51.96:9000 (tailnet IP only; listen=1 single client)
        | raw lossless PCM over WireGuard (direct LAN path)
        v
   ffmpeg reads tcp://raspberrypi:9000 -> 3s WAV blocks  BRAIN: birds-yard-bridge.service (systemd --user)
   -> curl POST multipart -> http://127.0.0.1:8000/birds/analyze (device "Yard Mic", loc 6, min_conf 0.5)
        v
   Workshop backend :8000 -> resident BirdNET analyzer :8012 (birdnetlib, range filter, trust model)
   -> browser-stations.db -> /apps/birds UI (device + suggestions/confirm)
```

Monitor: `/tmp/yard_monitor.sh` (transient, nohup) polls the device's stored-detection count, exits + notifies on the first new one.

---

## 5. CONFIG & UNIT FILES (verbatim)

**Pi `/etc/systemd/system/birdstream-lean.service`:**
```ini
[Unit]
Description=bird-station lean streamer (raw PCM -> tailnet TCP :9000)
After=network-online.target sound.target tailscaled.service
Wants=network-online.target
[Service]
Type=simple
User=birdstream
Group=birdstream
SupplementaryGroups=audio
ExecStartPre=/usr/bin/timeout 90 /bin/sh -c 'until ip -4 addr show tailscale0 2>/dev/null | grep -qw inet; do sleep 1; done'
ExecStart=/usr/bin/ffmpeg -nostdin -hide_banner -loglevel info -f alsa -channels 1 -sample_rate 48000 -i plughw:1,0 -f s16le tcp://100.84.51.96:9000?listen=1
Restart=always
RestartSec=2
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=full
[Install]
WantedBy=multi-user.target
```

**Brain `~/.config/systemd/user/birds-yard-bridge.service`:** Type=simple, `ExecStart=/usr/bin/python3 /home/clawd-a/workshop/birdnet/bridge/yard_bridge.py`, Restart=always, WantedBy=default.target. Env: `YARD_SRC=tcp://raspberrypi:9000`, `YARD_API=http://127.0.0.1:8000/birds/analyze`, `YARD_TOKEN=yard-pi-springer-01`, `"YARD_DEVICE=Yard Mic"` (quoted!), `YARD_LOCID=6`, `YARD_MINCONF=0.5`.

**Bridge `yard_bridge.py` (behavior):** spawns `ffmpeg -nostdin -f s16le -ar 48000 -ac 1 -i $YARD_SRC -f s16le -ac 1 -ar 48000 -`; reads stdout in exact 288000-byte (3 s) blocks; writes a temp WAV via `wave`; `curl`-POSTs multipart to `/birds/analyze`; logs `DETECT:` lines + a 300 s heartbeat; on stream EOF reconnects after 5 s. Stdlib + curl + ffmpeg only.

**Pi `/opt/birdstream/` (legacy, now unused):** `mediamtx` (v1.9.3 armv6 binary) + `mediamtx.yml`. The old `birdstream.service` is disabled. Safe to delete later.

---

## 6. SECURITY — ACL & HARDENING

**ACL BEFORE (default, wide open):**
```json
{ "grants": [ {"src":["*"],"dst":["*"],"ip":["*"]} ],
  "ssh": [ {"action":"check","src":["autogroup:member"],"dst":["autogroup:self"],"users":["autogroup:nonroot","root"]} ] }
```
**ACL AFTER (locked, current):**
```json
{ "tagOwners": { "tag:bird-pi": ["autogroup:owner"] },
  "grants": [
    {"src":["autogroup:member"],"dst":["*"],"ip":["*"]},
    {"src":["tag:bird-pi"],"dst":["tag:bird-pi"],"ip":["*"]}
  ],
  "ssh": [
    {"action":"accept","src":["autogroup:member"],"dst":["tag:bird-pi"],"users":["autogroup:nonroot"]},
    {"action":"check","src":["autogroup:member"],"dst":["autogroup:self"],"users":["autogroup:nonroot","root"]}
  ] }
```
Effect: operator's (non-tagged) devices keep full access incl. Brain→Pi:9000 + SSH into the Pi; the tagged Pi can reach **only itself** (no pivot). SSH to the Pi is `accept` (no browser check) for any of the operator's devices.

**Hardening summary:** VNC + CUPS disabled; console boot; OpenSSH password+root login disabled; streamer bound to tailnet IP (LAN can't reach the audio); no creds on the Pi; non-root service user; key-expiry disabled. **NOT done:** unattended-upgrades (EOL buster), ufw (uninstallable). **Verified:** cold-boot recovery + no-pivot.

**Secrets (paths only, never inline):** Tailscale API token `~/.config/clawdbot/tailscale-api.env` (0600); ACL backup `~/.config/clawdbot/ts-acl-backup-2026-06-12.hujson`; lat/long in `~/workshop/birdnet/brain/secrets.env` (0600).

---

## 7. MEASUREMENT APPENDIX (all data)

**Audio levels (volumedetect mean/max dB):** indoor (someone near) −57.9 / —; yard pre-gain −73.1 / −54.6; yard gain=20 −61.2 / −39.8; gain=24 used now; gain=28+AGC = brighter haze, more noise. Direct capture (gain 24): kinglet present, faint 8–10 kHz traces.

**Detections observed:**
- Direct ALSA capture (lossless, no stream): **Golden-crowned Kinglet 0.6787** (0–3 s), Flammulated Owl 0.1474, Golden-crowned Kinglet 0.246 (3–6 s).
- Opus 128k round-trip of that file: Kinglet 0.4447, 0.3097.
- Opus stream live captures: empty, or noise-ghosts only — Akohekohe (Hawaiian) 0.1113, Spotted Crake (Eurasian) 0.1457/0.1088 (impossible species = hallucination on noise; range filter drops them).
- Lean-stream capture (one faint window): Kinglet 0.1118.

**Throughput:** mediamtx/PCM 37 chunks/5 min (~37%); 20 s req → 1.05 s. Opus 128k: 20 s = 20 s. Lean: 25 s = 25.0 s; last_seen advanced 9.0 s over 10 s wall.

**CPU/load:** see the two tables in §3 Phase 8 / §3 Phase 7.

**Pi network:** path direct `192.168.1.77:41641`; `tailscale0` = `tun`; `lsmod | grep wireguard` → none (userspace wireguard-go).

---

## 8. CITATIONS / WEB RESEARCH

- Pi Zero W WireGuard throughput ~50 Mbit/s (single-core bound): https://forum.level1techs.com/t/wireguard-on-pi-zero-benchmarked/178399 ; https://news.ycombinator.com/item?id=41889091
- Tailscale userspace wireguard-go vs kernel, perf: https://tailscale.com/blog/more-throughput ; https://tailscale.com/kb/1320/performance-best-practices
- mediamtx audio-over-IP on Pi (Opus low-delay pattern): https://novamostra.com/2025/05/13/audio-over-ip-using-raspberry-pi/
- BirdNET-Pi RTSP + USB audio discussion (related project; runs detection on bigger Pis): https://github.com/mcguirepr89/BirdNET-Pi/discussions/1006
- mediamtx releases (armv6 binary): https://github.com/bluenviron/mediamtx/releases

(Operator preference, now a saved memory: verify technical claims via web search, don't assert from training knowledge.)

---

## 9. PROVEN vs. NOT-YET-PROVEN

**Proven:** mic captures real birds (kinglet 0.68 direct); Opus degraded detections below the floor; lossless lean transport = Pi not overloaded + realtime + stable; no-pivot ACL; cold-boot recovery; device registered + bridge posting realtime.

**NOT yet proven (the real gate):** a live bird detection flowing through the NEW lean pipeline into `/apps/birds`. Timing-dependent — needs a clear call during a watch window. Lossless means a clear call *should* store at ≥0.5. **Dawn (~05:45 PT) is the validation window.**

---

## 10. OPEN ITEMS / DECISIONS / NEXT STEPS

1. **Validate end-to-end** with a real detection across a dawn window. This is the "is it actually fixed" gate.
2. **Threshold stays 0.5.** Do NOT lower it to manufacture detections (operator-set to suppress false positives). Faint/distant birds not registering = a **capture** problem (mic placement/quality: closer, elevated, exposed, foam windscreen, or a better/directional mic), not a threshold problem.
3. **`-listen 1` is single-client** — only the bridge can read at a time; **stop the bridge before any debug pull**. Reintroducing multi-reader adds back per-packet cost.
4. **Optional further CPU cut:** stream over the LAN IP (skip WireGuard crypto, ~31%→~0) at the cost of LAN audio exposure. Not needed (headroom is fine).
5. **Bookworm reflash (long-term):** restores security updates + ufw; physical job, wipes setup. Defer.
6. **Listening to the mic audio:** no clawd-a-writable SMB mount to the operator's Mac exists (only user `simonsays`'s `Secure_Storage`). Options: operator mounts a clawd-a-writable share, or build a small Brain-side "listen" endpoint serving a recent clip.

---

## 11. OPERATIONAL GOTCHAS & CHEATSHEET

**Gotchas:**
- `ffmpeg` reads stdin by default → eats a piped `bash -s` script. Always `ffmpeg -nostdin` (and `</dev/null`).
- Heredocs / `;`-chains over `ssh 'bash -s'` get newline-mangled by the shell-snapshot wrapper → prefer `Write`+`rsync`+run a real file, or single-line `;`-joined commands.
- `pkill -f yard_monitor.sh` matches its OWN command line → kills the shell (exit 144). Use `pkill -f "[y]ard_monitor.sh"` / pgrep+kill.
- rsync to the Pi must use the **FQDN** `pi@raspberrypi.tail38e62e.ts.net:` (egress-guard treats the short name as external). `ssh` is not guarded.
- Measuring `tailscaled` CPU over SSH self-inflates it (the SSH session runs through tailscaled).
- mediamtx v1.9.3 uses `protocols:` not `rtspTransports:`.

**Cheatsheet:**
```bash
# Pi streamer
ssh pi@raspberrypi 'sudo systemctl restart birdstream-lean'
ssh pi@raspberrypi 'sudo ss -tlpn | grep 9000'           # confirm listening
ssh pi@raspberrypi 'cat /proc/loadavg; top -bn1 | grep -E "[f]fmpeg|[t]ailscaled"'
# Bridge (Brain, systemd --user)
XDG_RUNTIME_DIR=/run/user/1003 systemctl --user restart birds-yard-bridge.service
XDG_RUNTIME_DIR=/run/user/1003 journalctl --user -u birds-yard-bridge.service -n 50
# Debug-capture from the stream (STOP bridge first; listen=1 single client)
ffmpeg -nostdin -f s16le -ar 48000 -ac 1 -i tcp://raspberrypi:9000 -t 20 /tmp/x.wav
# Direct mic capture (STOP streamer first)
ssh pi@raspberrypi 'sudo systemctl stop birdstream-lean; ffmpeg -nostdin -f alsa -channels 1 -sample_rate 48000 -i plughw:1,0 -t 15 /tmp/d.wav </dev/null'
# then: ssh pi@raspberrypi 'sudo systemctl start birdstream-lean'
# Analyzer probe (raw, all confidences, range on)
curl -s -X POST http://127.0.0.1:8012/analyze -H 'Content-Type: application/json' \
  -d '{"path":"/tmp/x.wav","min_conf":0.1,"lat":37.369721,"lon":-122.0956962,"week":24,"sf_thresh":0.01}'
# Spectrogram -> view the PNG
ffmpeg -nostdin -i /tmp/x.wav -lavfi "showspectrumpic=s=1100x480:legend=1:fscale=lin:stop=12000:color=intensity" /tmp/x.png
# Tailscale admin API (token in ~/.config/clawdbot/tailscale-api.env)
curl -s -u "${TS_API_TOKEN}:" https://api.tailscale.com/api/v2/tailnet/-/acl
```

---

## 12. APP-SIDE REFERENCE

- Location id **6** = "1849 South Springer Road, Los Altos", lat 37.369721 lon -122.0956962.
- Device: token `yard-pi-springer-01`, name "Yard Mic", mode fixed, loc 6.
- Smart settings (global, operator-tuned): min_conf **0.5**, sf_thresh 0.01, trust_radius_km 5, new_species_hits 3. Trust model: detections start "suggested" → confirm/reject.
- Backend `~/workshop/backend` (uvicorn :8000, nohup, env `WORKSHOP_511_API_KEY`); analyzer `~/workshop/birdnet/analyzer/service.py` (:8012, isolated venv); browser DB `~/birdnet/data/browser-stations.db`.
- Related code: `backend/routes/birds.py`, `backend/services/birds_analyze.py`, `frontend/src/pages/apps/birds/*`.
- Memory files: `project_birdstation_pi_2026_06_12`, `project_birdnet_yard_station`, `feedback_web_search_verify`.
```
