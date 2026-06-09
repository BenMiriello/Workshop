# BirdNET Yard Station — bring-up runbook

This is the file you open tomorrow and follow top to bottom. Every step is an
exact command. The plan is here, the configs are here, the verification gates
are here.

## What goes where (read once, never confuse again)

```
[Pi, outdoors, on a USB mic]      Pi runs: mediamtx (RTSP server) + ffmpeg.
        |                          That is ALL it runs. No detection, no DB,
        |                          no clips. Thin streamer.
        |  tailnet (WireGuard)
        v
[Brain = this Debian box]         Brain runs: BirdNET-Go (detector) +
                                   workshop-backend (FastAPI) + frontend +
                                   sanitizing publisher. Owns the SQLite DB
                                   and the audio clips. Talks to the Pi over
                                   tailnet via RTSP pull.
        |
        |  (sanitized, no audio/clips/coords)
        v
[Cloudflare R2 + Pages]           Public stats page. Optional, deferrable
                                   without affecting the detector.
```

You asked "what's the system for installing BirdNET on the Pi?" — there isn't
one. **BirdNET-Go does not run on the Pi.** The Pi is a microphone with a
network card. Inference happens on the Brain because the 3090 is here and the
SQLite DB has to be owned by the same uid as the backend that reads it. The
Pi's entire job is: ALSA input → ffmpeg → RTSP on `:8554/yard`. See
`pi/setup.sh` (one script, one job).

## State as of right now

- `brain/secrets.env` written with real lat/long (37.369764, -122.095773 from
  OwnTracks, matches 1849 S Springer Rd) and a placeholder Pi hostname. Mode
  0600. Confirmed gitignored.
- `workshop-backend` is running on `:8000` and healthy. It is supervised by
  an orphan watchdog (`~/workshop/birdnet/analyzer/watchdog.sh`, PID parented
  to init), not by systemd. **Survives this session, does NOT survive a
  reboot.** Promotion to the systemd unit is your one click step at the top
  of tomorrow's session — see step "Promote backend to systemd" below.
- BirdNET-Go binary not yet installed on the Brain.
- No real hardware tested. Tailnet has an offline node `raspberrypi`
  (`100.84.51.96`, last seen 171d ago) — pick: revive it or flash new.

## Step 0 — Promote workshop-backend to systemd (5 min, BEFORE the Pi)

Reason: BirdNET-Go is going to POST detections to
`http://127.0.0.1:8000/birds/webhook` 24/7. A backend that doesn't survive a
reboot is a 24/7 detector that silently loses events on the next power blip.

Why I didn't do this for you already: the auto-mode classifier blocked the
kill of the live watchdog (pre-existing infra not created in this session).
You run it; takes 30 seconds.

```bash
# Stop the watchdog so it doesn't fight systemd:
kill "$(cat /tmp/birdnet-watchdog.pid 2>/dev/null)" 2>/dev/null
rm -f /tmp/birdnet-watchdog.pid
pkill -f 'uvicorn main:app.*8000'
sleep 2

# Promote the unit. The unit file already exists and points at the same venv:
systemctl --user daemon-reload
systemctl --user enable --now workshop-backend
systemctl --user status workshop-backend --no-pager   # active (running)
curl -s localhost:8000/healthz                         # {"ok":true}
```

Verification gate: `systemctl --user is-enabled workshop-backend` says
`enabled` and `is-active` says `active`. After a `reboot` the backend comes
back automatically. If you also want the analyzer service auto-restarted by
systemd instead of the watchdog, install its unit too:

```bash
sudo cp ~/workshop/birdnet/analyzer/birdnet-analyzer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now birdnet-analyzer
```

## Step 1 — Pi flash and tailnet (15-25 min)

On your laptop, SD card inserted:

1. Open Raspberry Pi Imager. Choose **Pi OS Lite (64-bit)**.
   In the gear-icon advanced menu:
   - hostname: `birdpi`
   - enable SSH (password or your public key)
   - wifi SSID + PSK + country
   - locale
   Write, eject, insert into Pi, power on.
2. From the laptop on the same LAN, confirm SSH works:
   ```bash
   ssh pi@birdpi.local "uname -a"   # may take ~60s after first boot
   ```
3. Copy the Pi files from the Brain over to the Pi. **Do not sign into
   GitHub on the Pi, do not clone the workshop repo there** — the Pi only
   needs four small files and shouldn't carry repo credentials outdoors.
   Run this one line from the laptop; it tars `~/workshop/birdnet/pi/` on
   the Brain straight into `~/birdnet-pi/` on the Pi:
   ```bash
   ssh clawd-a@debian.tail38e62e.ts.net \
     "tar -C ~/workshop/birdnet -czf - pi/" \
     | ssh pi@birdpi.local "mkdir -p ~/birdnet-pi && tar -C ~/birdnet-pi -xzf -"
   ```
   If you're reusing the existing `raspberrypi` node that's already on
   tailnet, you can do this from the Brain directly instead:
   ```bash
   rsync -av ~/workshop/birdnet/pi/ pi@raspberrypi.tail38e62e.ts.net:~/birdnet-pi/
   ```
4. Run the staging script on the Pi:
   ```bash
   ssh pi@birdpi.local "bash ~/birdnet-pi/pi/setup.sh"
   ```
   The script installs ffmpeg + alsa-utils + tailscale, stages `mediamtx.yml`
   into `/etc/mediamtx/`, and copies `mediamtx.service` to
   `/etc/systemd/system/`. It does **not** start tailscale (interactive auth)
   or start mediamtx (you confirm the ALSA device first).
5. Join the tailnet (interactive — opens an auth URL on the terminal):
   ```bash
   ssh pi@birdpi.local "sudo tailscale up --hostname=birdpi"
   # Visit the URL printed on the laptop terminal, approve.
   ```
6. From the Brain, confirm connectivity:
   ```bash
   ping -c5 birdpi.tail38e62e.ts.net
   ```

Gate 1: ping succeeds, < 5% loss. If yard wifi is shaky, fix it here — every
later layer assumes the link is up.

If you reuse the existing `raspberrypi` node instead of `birdpi`:
- Skip the imager step, bring the Pi online, re-auth tailscale, confirm
  `tailscale status` shows it.
- Edit `brain/secrets.env` line `PI_TAILSCALE_HOST=raspberrypi.tail38e62e.ts.net`.

## Step 2 — Confirm the mic, set the ALSA device (10-20 min)

On the Pi:

```bash
arecord -l
# Look for: "card 1: Device [USB Audio Device]" or similar. Note the card number.

# Run the test script (defaults to plughw:1,0; pass plughw:<card>,0 if different):
cd ~/birdnet-pi/pi    # wherever you cloned to
./record-test.sh plughw:1,0 6
# Output: channels=1, sample_rate=48000, mean_volume + max_volume in dB.

# Listen for real:
scp pi@birdpi.local:/tmp/birdmic-test.wav ~/
# Open ~/birdmic-test.wav locally. You should hear the yard, not a hum.
```

If `arecord -l` showed a different card number, edit the systemd env:

```bash
sudo sed -i 's|MTX_ALSA_DEVICE=plughw:1,0|MTX_ALSA_DEVICE=plughw:2,0|' \
  /etc/systemd/system/mediamtx.service
sudo systemctl daemon-reload
```

Gate 2: clean recording, mono, 48 kHz, no constant low-volume hum. If hum:
move the USB sound card onto a short extension cable away from the Pi board
(documented fix; Pi switching supply leaks into close USB audio).

## Step 3 — Bring up the RTSP stream (10 min)

On the Pi:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mediamtx
sudo journalctl -u mediamtx -f
# Expect: "[RTSP] listener opened on :8554"
#         "[path yard] runOnInit started"
# No "ffmpeg exited" lines.
```

From the Brain:

```bash
ffprobe rtsp://birdpi.tail38e62e.ts.net:8554/yard
# Expect: 1 audio stream, codec pcm_s16be, 1 channel, 48000 Hz.
ffplay rtsp://birdpi.tail38e62e.ts.net:8554/yard    # optional: listen
```

Gate 3: ffprobe clean. If it hangs or "connection refused": tailnet down or
mediamtx not on `:8554`. On the Pi, `ss -tlnp | grep 8554`.

## Step 4 — Install BirdNET-Go on the Brain (20-40 min)

This is the actual detector. Single binary, runs as systemd, owns the SQLite
DB at `/home/clawd-a/birdnet/data/birdnet.db` and clips at
`/home/clawd-a/birdnet/clips/`.

On the Brain (as `clawd-a`):

```bash
# 1. Confirm the upstream installer URL — it has drifted in the past:
xdg-open https://github.com/tphakala/birdnet-go     # check the README

# 2. Install (currently):
curl -fsSL https://github.com/tphakala/birdnet-go/raw/main/install.sh | bash
birdnet-go --version
which birdnet-go
# Adjust ExecStart in brain/birdnet-go.service if the path differs from
# /usr/local/bin/birdnet-go.

# 3. Make data dirs (already exist as empty; just confirm):
mkdir -p /home/clawd-a/birdnet/data /home/clawd-a/birdnet/clips

# 4. Let BirdNET-Go materialize the release's default config and inspect:
birdnet-go realtime --help | less
# This shows the keys the installed release expects. Compare to our template.

# 5. Reconcile our config template against the release default and install it:
cp /home/clawd-a/workshop/birdnet/brain/config.yaml /home/clawd-a/birdnet/config.yaml
# Edit if the installed release uses a different notification block name
# (alert-rules engine vs top-level webhook). The target URL and JSON body in
# brain/webhook-template.json don't change.

# 6. secrets.env is already written and 0600. Confirm:
ls -la /home/clawd-a/workshop/birdnet/brain/secrets.env
# -rw------- 1 clawd-a clawd-a ... secrets.env
# If you reused raspberrypi instead of birdpi, edit PI_TAILSCALE_HOST in it.

# 7. Install the systemd unit:
sudo cp /home/clawd-a/workshop/birdnet/brain/birdnet-go.service \
  /etc/systemd/system/birdnet-go.service
sudo systemctl daemon-reload
sudo systemctl enable --now birdnet-go
journalctl -u birdnet-go -f
# Expect: "Connected to RTSP source rtsp://birdpi.tail38e62e.ts.net:8554/yard"
# NO reconnect spam.
```

Gate 4: any of these within ~30 min of going live (if the yard is quiet, play
a clear bird-call YouTube clip near the mic to force one):

```bash
sqlite3 /home/clawd-a/birdnet/data/birdnet.db \
  "select date,time,common_name,round(confidence,2) from notes order by id desc limit 5;"
ls -la /home/clawd-a/birdnet/clips/ | tail        # at least one WAV
curl -s localhost:8000/birds/summary              # available:true, count>0
curl -s localhost:8000/events/recent?source=birdnet | head -c 1000
```

Open the Workshop frontend → Apps → Yard Birds (`/apps/birds`). The same row
shows there. **You're done with the detector at this point.**

## Step 5 — (reserved, keeps numbering with README)

## Step 6 — Subscribe an OpenClaw agent (15 min, optional day 1)

`/events/{source}` already gets a `birdnet` event per detection. To get
narration:

```bash
# Pick or create an agent (or reuse plaid-watcher's pattern):
curl -s localhost:8000/agents/ | head -c 1000

# Add birdnet to its subscriptions + set autonomy mode:
AGENT=agent-XXXX
curl -s -X PATCH localhost:8000/agents/$AGENT/metadata \
  -H 'content-type: application/json' \
  -d '{"subscriptions":["birdnet"], "autonomy_mode":"event-driven", "max_calls_per_hour": 12}'

# Or use the frontend AgentConfig page — same effect, less typing.

# Force a test event (loopback, no real bird needed):
curl -s -X POST localhost:8000/birds/webhook \
  -H 'content-type: application/json' \
  -d '{"scientific_name":"Cardinalis cardinalis","common_name":"Northern Cardinal","confidence":0.92,"date":"2026-06-07","time":"08:00:00"}'
curl -s "localhost:8000/agents/$AGENT/runs" | head -c 1000
```

Gate 6: at least one agent run consumed a birdnet event, throttle = no.

## Step 7 — Cloudflare public site (optional, separate concern, day 2)

You're right that the Cloudflare side is independent of the Pi setup. Do not
block on it tomorrow. When you have an hour:

```bash
# Interactive — must run as you, opens a browser auth URL:
npx wrangler login

# One-time provisioning:
npx wrangler r2 bucket create yard-birds-public
cd ~/workshop/birdnet/public-site
npx wrangler pages project create yard-birds --production-branch main
# Then in the Cloudflare dashboard: Pages > yard-birds > Settings > Functions
# > R2 bindings: variable name BIRDS_BUCKET = yard-birds-public.

# Token + env file:
cp ~/workshop/birdnet/publish/cloudflare.env.example \
   ~/workshop/birdnet/publish/cloudflare.env
chmod 600 ~/workshop/birdnet/publish/cloudflare.env
# Edit it with CLOUDFLARE_ACCOUNT_ID + scoped token (R2 write on yard-birds-public ONLY) + R2_BUCKET=yard-birds-public.

# Sanity check the boundary BEFORE the first push:
DRY_RUN=1 ~/workshop/birdnet/publish/publish.sh
head -c 500 ~/workshop/.runtime/birds-public.json   # no lat/long/clips

# Real publish + deploy site:
~/workshop/birdnet/publish/publish.sh
cd ~/workshop/birdnet/public-site
npx wrangler pages deploy . --project-name yard-birds

# Install the hourly auto-publish timer:
sudo cp ~/workshop/birdnet/publish/birds-publish.service \
        ~/workshop/birdnet/publish/birds-publish.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now birds-publish.timer
systemctl list-timers birds-publish*
```

Gate 7: Pages URL renders species/counts/confidence/first-seen.
`curl -s https://yard-birds.pages.dev/birds-public.json | grep -iE 'latitude|longitude|clip_name|clips/'` returns empty.

## Where to find everything

| Thing | Path |
|---|---|
| This runbook | `~/workshop/birdnet/BRINGUP.md` |
| Real secrets (lat/long, Pi host) | `~/workshop/birdnet/brain/secrets.env` (0600, gitignored) |
| Pi setup script | `~/workshop/birdnet/pi/setup.sh` |
| Pi mic test script | `~/workshop/birdnet/pi/record-test.sh` |
| Pi mediamtx config | `~/workshop/birdnet/pi/mediamtx.yml` |
| Pi systemd unit | `~/workshop/birdnet/pi/mediamtx.service` |
| Brain BirdNET-Go config | `~/workshop/birdnet/brain/config.yaml` |
| Brain BirdNET-Go systemd unit | `~/workshop/birdnet/brain/birdnet-go.service` |
| Webhook JSON template | `~/workshop/birdnet/brain/webhook-template.json` |
| Backend bird routes | `~/workshop/backend/routes/birds.py` |
| SQLite read + sanitizer | `~/workshop/backend/services/birds_store.py` |
| Frontend private UI | `~/workshop/frontend/src/pages/apps/Birds.tsx` |
| Sanitizing publisher | `~/workshop/birdnet/publish/publish.sh` |
| Hourly publish timer | `~/workshop/birdnet/publish/birds-publish.timer` |
| Public-site static page | `~/workshop/birdnet/public-site/` |
| Public-site deploy guide | `~/workshop/birdnet/public-site/DEPLOY.md` |
| Cloudflare env (you create) | `~/workshop/birdnet/publish/cloudflare.env` (0600, gitignored) |
| SQLite (created by birdnet-go) | `/home/clawd-a/birdnet/data/birdnet.db` |
| Clips (created by birdnet-go) | `/home/clawd-a/birdnet/clips/` |
| BirdNET-Go journal | `journalctl -u birdnet-go -f` |
| Workshop backend journal | `journalctl --user -u workshop-backend -f` |
| Mediamtx journal (on Pi) | `sudo journalctl -u mediamtx -f` |

## Privacy boundary, restated

The sanitizer in `backend/services/birds_store.build_public_dataset()` is the
only producer of public data. `/birds/public` emits species + counts +
day-granular timestamps + confidence + first-seen. No lat/long, no
`clip_name`, no audio bytes. `publish.sh` re-greps for those tokens and hard-
aborts before pushing. A bug in the public UI cannot leak because the
sensitive data is never sent there.

## Common failures and fixes

- **`birdnet-go` reconnect spam.** Pi's mediamtx is up but ffmpeg failed to
  open ALSA. Pi-side `sudo journalctl -u mediamtx -f` shows the ffmpeg stderr.
  Fix the `MTX_ALSA_DEVICE` in the systemd env, not the YAML.
- **ffprobe "connection refused" from Brain.** Tailnet down or mediamtx not
  listening. On the Pi: `ss -tlnp | grep 8554`.
- **Detector connects but never writes a row.** Threshold too high or range
  filter culling. Drop `birdnet.threshold` to 0.2 and `rangefilter.threshold`
  to 0.01 temporarily. Play a loud clear call near the mic. If still nothing,
  the notification webhook block in the installed release uses a different
  schema — reconcile against the live config.
- **Webhook fires but no event in `/events/recent`.** Same root cause.
  Compare the running `birdnet-go realtime --help` against `brain/config.yaml`
  and migrate the block. The JSON contract (the body in
  `webhook-template.json`) and the target URL are stable.
- **Public page renders empty.** The R2 binding variable on the Pages project
  must literally be `BIRDS_BUCKET`. The Pages Function reads it by that name.
- **Hum or whine on the mic.** USB extension cable off the Pi board.

## What I will not do without you

- `wrangler login` (interactive browser auth URL).
- `tailscale up` on the Pi (interactive auth URL).
- Kill the orphan watchdog supervising the current backend (classifier
  blocked it; step 0 above is the operator action).
- Hand-edit BirdNET-Go's notification block to a schema we haven't seen
  installed yet — we reconcile against the live release, not guess.
