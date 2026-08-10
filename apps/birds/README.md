# BirdNET Yard Station

Always-on backyard bird-song detector. A mic outside feeds a local BirdNET-Go
classifier running 24/7 on the Debian box; detections are logged, surfaced in a
private Workshop app, fed to the OpenClaw assistant as events, and published as
a sanitized stats-only public page. No cloud inference. Audio and location stay
inside the network.

## Architecture (four layers + a hard privacy boundary)

```
[Pi capture node, outdoors]   mic -> mediamtx/ffmpeg -> RTSP (48k mono PCM)
        |  tailnet
        v
[Brain: Debian box]   BirdNET-Go: pulls RTSP, detects, owns SQLite + clips,
        |             serves stock dashboard :8080, POSTs each detection to ->
        |--------------------> backend /birds/webhook (enrich first-seen)
        |                              -> Workshop event bus -> OpenClaw agents
        |  read-only SQLite
        v
[Private UI]  Workshop /apps/birds  (tailnet-only): species, charts, activity,
        |     per-detection audio playback. Full fidelity.
        |  sanitizer (/birds/public) -> publish.sh (hourly) -> R2
        v
[Public page]  Cloudflare Pages: species + counts + day-times + confidence +
              first-seen ONLY. No audio, no clips, no coordinates, ever.
```

### Privacy boundary (non-negotiable)

Sanitizing happens at the SOURCE, in `backend/services/birds_store.build_public_dataset()`.
The public deployment receives only that scrubbed JSON via R2. It has no network
path to the private SQLite DB, the audio clips, or any location. A bug in the
public UI is structurally incapable of leaking, because the sensitive data was
never sent there. `publish.sh` also hard-aborts if lat/long/clip strings ever
appear in the payload (belt and suspenders).

## Decisions (defaulted during the build; override before hardware bring-up)

- Pi role: **thin streamer**, inference on the Debian box. The Pi runs no
  detection and stores nothing.
- Stream tool: **mediamtx + ffmpeg**. Single static binary that IS an RTSP
  server and supervises the ffmpeg capture itself (auto-restart). go2rtc is
  built for many-source restreaming/WebRTC; overkill for one mic -> one consumer.
- Audio: **48kHz mono PCM (L16)**. Model is 16-bit so 24-bit is pointless; PCM
  not mp3 because lossy compression hurts detection.
- BirdNET-Go: **native binary**, not Docker. Direct filesystem ownership of the
  SQLite DB + clips (backend reads them as the same user, no volume mapping),
  simpler systemd, no container layer before the GPU if Perch/ONNX is added.
- Assistant feed: **webhook**, not MQTT. No broker exists on the box; one HTTP
  POST into the existing Workshop event bus is enough.
- Cloudflare store: **R2 JSON blob** served via a Pages Function binding.
- GPU: CPU handles vanilla detection fine. The 3090 is optional later (Perch v2
  / multi-model consensus, assistant layer). Not a bring-up blocker.

Still needs YOUR input before/at hardware time:
- Exact Pi model (Zero 2 W / 3B+ / 4) — decides if the streamer settings need
  tuning. Default config assumes a Pi capable of ffmpeg ALSA -> RTSP, which all
  of those handle for a single mono stream.
- Real lat/long -> `brain/secrets.env` (git ignored, never public).
- Pi tailnet hostname -> `brain/secrets.env`.
- Confirm the yard has solid wifi (Phase 0).

## Build status

Done and verified against a fixture DB (no hardware needed yet):
- [x] Brain: BirdNET-Go config, systemd unit, install runbook, webhook template.
- [x] Pi: mediamtx RTSP config, systemd unit, setup.sh, audio record-test.sh.
- [x] Backend: read-only SQLite layer + sanitizer (`services/birds_store.py`),
      API + webhook bridge (`routes/birds.py`), wired into `main.py`; `birdnet`
      added as a known event source.
- [x] Private UI: `frontend/src/pages/apps/Birds.tsx`, routed + carded in Apps.
- [x] Publish: `publish.sh` + hourly systemd timer, leak-guarded.
- [x] Public site: static page + Pages Function reading R2, deploy guide.

Pending hardware (tomorrow):
- [ ] Phase 0/1/2: flash Pi, join tailnet, confirm ALSA device, noise floor,
      bring up the RTSP stream (`pi/setup.sh`, `pi/record-test.sh`).
- [ ] Phase 3: install BirdNET-Go on the Brain, point at the Pi's RTSP, fill
      secrets.env, confirm real detections (`brain/install.md`).
- [ ] Phase 4: subscribe an OpenClaw agent to the `birdnet` event source so it
      narrates arrivals (the bridge already emits the events).
- [ ] Phase 6: create the R2 bucket + Pages project, deploy public site.

## Dev / test without hardware

```bash
# Seed a fixture BirdNET-Go DB (matches the real GORM schema):
backend/.venv/bin/python3 apps/birds/publish/seed_fixture_db.py

# Exercise the API:
curl -s localhost:8000/birds/summary
curl -s localhost:8000/birds/recent-arrivals?days=7

# Simulate a detection through the event bridge:
curl -s -X POST localhost:8000/birds/webhook -H 'content-type: application/json' \
  -d '{"scientific_name":"Cardinalis cardinalis","common_name":"Northern Cardinal","confidence":0.9,"date":"2026-06-06","time":"07:01:00"}'

# Produce + sanity-check the sanitized public dataset (no push):
DRY_RUN=1 apps/birds/publish/publish.sh

# Private UI: open the Workshop frontend -> Apps -> Yard Birds (/apps/birds).
```

## Files

- `brain/` — BirdNET-Go config + systemd + install runbook (Debian box).
- `pi/` — RTSP streamer config + systemd + setup/test scripts (Raspberry Pi).
- `publish/` — fixture seeder, sanitizing publish job + timer, Cloudflare env.
- `public-site/` — static public page + Pages Function + deploy guide.
- Backend code lives in `backend/services/birds_store.py` + `backend/routes/birds.py`.
- Private UI lives in `frontend/src/pages/apps/Birds.tsx`.
