# Bird-Station Pi — Context Doc #2: the "zero detections" root cause (mic powering)

**Status:** 2026-06-13 ~10:50 PT. **RESOLVED — the rig is fully working end-to-end and detecting real birds.** The mic-powering fix (Saramonic SR-EA2S) landed, the bridge silent-wedge bug was found and fixed, the device is claimed/shared so it shows on the public site, and a live "Recording" UI indicator was added. Detections are flowing (House Finch, Chestnut-backed Chickadee). See **§0.1 CURRENT STATUS** immediately below for the live picture; the rest of this doc is the diagnostic history that got us here (still useful: the flatness methodology, hardware facts, gotchas). Author: Claude (clawd-a session). Supersedes `BIRDSTATION-CONTEXT.md` (doc #1) mic conclusions; doc #1's transport/security work remains valid.

---

## 0.1 CURRENT STATUS (2026-06-13, working)

Everything below the dotted line in §0 was the "waiting on hardware" state; this is the **current** state.

- **Mic: WORKING.** Fix = **Saramonic SR-EA2S** USB audio interface, **switch set to TRS** for the Movo DOM1, on the Pi's OTG adapter. Enumerates as `card 1` -> `plughw:1,0` (same as the dead cards, so streamer/bridge needed no change). It supplies the plug-in power the DUKABEL/AJDPOI cards couldn't. Verified by `bt.sh`: a voice/whistle test drove bird-band min flatness to **0.066** (vs static's pinned ~0.4-0.8 that never dips) -> `VERDICT: SIGNAL`. Operator confirmed by ear.
- **Detections: FLOWING.** Real local species stored (House Finch, Chestnut-backed Chickadee). Non-local BirdNET misIDs (Long-tailed Tit, Spotted Flycatcher) are correctly dropped by the range filter (`in_range=False`). `DEDUP_S=2.5s` only merges a continuing song into one row (not a throttle).
- **Non-bird noise classes (e.g. "Engine") are REAL detections, not false positives** — the mic genuinely hears passing cars and BirdNET labels them correctly. They're stored with `is_bird=0` (kept distinct from bird species). Do not treat them as errors or auto-mute them.
- **Threshold: KEEP min_conf 0.5. Do NOT lower it** — operator's call 2026-06-13; it's already producing some false positives, so 0.4 would make it worse. To raise the *true* detection rate toward what's audible, improve **mic placement/gain** (get it nearer the birds), not the threshold.
- **Bridge: hardened.** It had silently wedged ~17h (blocking `read()` on a half-open stream, no EOF to trigger reconnect). Fixed with a 30s **stall watchdog** (see §5). Both Pi streamer and Brain bridge are `enabled` + `Restart=always`, so it auto-connects on plug-in/reboot and self-heals a stall.
- **App visibility: FIXED.** "Yard Mic" device row had `owner_user_id=NULL`, so the login-scoped public app (`springer_app:app` :8001) filtered it out (it filters by viewer ownership; the private `main:app` :8000 doesn't). Claimed it `owner_user_id=1, shared=1` and backfilled its detections. Now visible on **birds.benmiriello.com**.
- **UI "Recording" indicator: ADDED.** Animated `StatusPill status="running"` per live device on the Devices page + a `RecordingIndicator` in `BirdsHeader` on every birds page (incl. public). `isRecording(last_seen, 60s)` helper in `birds/ui.tsx`. GOTCHA fixed: `last_seen` is bumped both by incoming audio AND by a browser just loading the app (settings-sync at `birds_analyze.py:2372`), so a viewing browser falsely read as "recording"; the current browser now uses the local recorder state, only audio-driven devices (the Pi) use `last_seen`.
- **PUBLIC SITE MUST BE REBUILT after birds frontend changes** — `birds.benmiriello.com` serves a prebuilt bundle (`frontend/dist-springer/`), NOT hot-reload. Rebuild: `cd ~/workshop/frontend && npx vite build --config vite.springer.config.ts` (no restart needed; springer_app reads from disk). The local app (vite dev) updates instantly; the public one does not until rebuilt. Keep it current.

---

## 0. TL;DR / where we landed

- The yard mic produced **pure static** end-to-end. Operator heard loud birds with his ears; the rig recorded only noise.
- It was **never the transport, the codec, the CPU, or the threshold** (doc #1 fixed all those). It was the **microphone signal chain**.
- **The microphone (Movo DOM1) is GOOD** — operator plugged it into his computer and test-recorded cleanly. Confirmed alive.
- The problem is the **USB sound cards on the Pi do not deliver usable plug-in power** to the Movo's electret capsule, so it can't transduce → the ADC records its own noise floor (static).
- Compounding it: the original card (DUKABEL) is also a **TRRS combo jack**, electrically wrong for a TRS mic regardless of power.
- **On order (operator placed both):** Rode SC4 (TRS→TRRS adapter) and Saramonic SR-EA2S (USB audio interface with plug-in power + TRS/TRRS switch). When either arrives: run `bash ~/workshop/birdnet/tools/bt.sh 15` and read the verdict.

---

## 1. The measurement methodology (READ THIS FIRST — it's why doc #1 was wrong)

Doc #1 (and the first half of this session) judged audio by **level** (mean/max dB) and a made-up "dynamic range" = max−mean. **That is wrong and repeatedly mislabeled static as signal.** Loud static has a big peak-to-mean gap too. Level tells you nothing about whether sound is *structured*.

**Use SPECTRAL FLATNESS** (Wiener entropy): geometric-mean / arithmetic-mean of the magnitude spectrum.
- **~0 = tonal / structured** (birds, voice, anything intelligible).
- **~1 = white-noise / static.**
- A working mic on a live environment shows flatness **dipping toward 0** whenever a tonal sound occurs. Static stays **pinned high and never dips.**

Calibrated baselines measured on THIS rig (`ffmpeg aspectralstats`):
| source | full-band flatness | note |
|---|---|---|
| pure tone (2-3 kHz) | 0.004 | fully structured |
| white noise | 0.845 | pure static |
| DUKABEL "dead" capture | ~0.58 pinned | static |
| AJDPOI yard captures | 0.74–0.83 pinned (min never < ~0.70) | **static** |
| (target) a real bird call | should drive bird-band min well below ~0.2 | not yet observed |

**The tool:** `~/workshop/birdnet/tools/bt.sh [seconds]` does the whole loop in one shot: stop streamer → capture → restart streamer → pull → compute full-band + bird-band (2-8 kHz) flatness → print a STATIC-vs-SIGNAL verdict → publish mp3s. Use it for every future test. Example output:
```
15s  maxdB=-53.3  flat full(mean/min)=0.831/0.791  bird2-8k(mean/min)=0.475/0.448
VERDICT: STATIC (no signal)
listen: http://100.104.196.85:8765/bt.mp3   (+20dB: /bt_boosted.mp3)
```
Gotchas baked into the tool / learned the hard way:
- The dotted ffmpeg metadata key `lavfi.aspectralstats.1.flatness` trips the **egress-guard** (reads it as an external hostname) when it's on the shell command line. Put flatness logic in a **script file** (bt.sh does), not an inline `-c` arg.
- `mawk` has no `asort` → `sort -n` upstream of awk for percentiles.
- Foreground `sleep` is blocked by the harness; the capture is `ffmpeg -t`, not sleep, so it's fine. For waits use `run_in_background`.

**Operator delivery channel for audio:** mp3-over-tailnet. `python3 -m http.server 8765 --bind 100.104.196.85` (the Brain's tailnet IP) serving `/tmp/yard_listen/`; operator opens `http://100.104.196.85:8765/<file>.mp3` from any tailnet device. There is **no clawd-a-writable SMB share** to his Mac (`/media/benmiriello` is root-only; the only CIFS mount is user `simonsays`'s). bt.sh auto-starts this server.

---

## 2. The hardware chain (exact devices, verified)

```
Movo DOM1 mic  --3.5mm TRS-->  [USB sound card]  --USB(OTG)-->  Pi Zero W  --tailnet-->  Brain
   (GOOD)                       (the problem)
```

**Microphone — Movo DOM1** (Amazon ASIN B01H61U27Q). 3.5mm **TRS 3-pole** omnidirectional **electret condenser** lavalier; sensitivity −30 dB; a **camera** mic; "powered by plug-in power from any 3.5mm TRS device." Box: mic + warranty card, no adapter. **CONFIRMED WORKING** (recorded fine plugged into the operator's computer). An electret needs DC bias (~few volts, <0.5 mA) fed up the tip to run its internal FET amp; no bias → no signal → you record noise.

**USB sound card #1 — DUKABEL** (ASIN B07RS11M1T). USB id `1b3f:2008 Generalplus`. **CTIA 4-pole TRRS combo jack** (one hole = L/R out + mic, mic on the *sleeve*). Its own listing **Note 5: "Do not work with TS or TRS microphones."** So a bare TRS mic here is doubly wrong: pin-misaligned AND combo-jack. ALSA name "USB Audio Device", appears as `card 1`, `plughw:1,0`.

**USB sound card #2 — AJDPOI 3-pack** ("USB Audio Adapter 3.5mm Headphone and Microphone Jacks External Stereo Sound Card"). USB id reports `8086:0808` (generic/cloned descriptor). Has **separate** headphone-out + mic-in jacks (correct interface for a TRS mic). ALSA name "USB PnP Sound Device", `card 1`, mono mic-in, controls = Mic Capture 0–16 + Auto Gain Control. **Still recorded pure static** with the Movo correctly in its mic input, seated, gain set, AGC off, config verified. → its mic jack apparently does **not** supply adequate plug-in-power bias (the documented-common cheap-card failure; unverified by meter on this exact unit).

**Pi — Raspberry Pi Zero W Rev 1.1** (verified on-device: `/proc/device-tree/model`, cpuinfo Revision `9000c1`, BCM2835). ARMv6, 512 MB, single micro-USB OTG data port. USB-A card attaches via a micro-USB→USB-A OTG adapter (operator also has this; it passes data fine — the card enumerates and streams — so it is **ruled out** as the culprit).

**Key electrical facts established this session:**
- TRS mic vs CTIA TRRS combo jack (DUKABEL): pins misalign; mic signal lands on a headphone-output pin; mic-in pin gets nothing. = static.
- Even on a *correct* separate mic jack (AJDPOI): static → points at **plug-in power**, not wiring.
- "Electret lav = only static on a cheap USB sound card" is a documented failure mode (insufficient bias). Sources: [BirdNET-Pi #373](https://github.com/mcguirepr89/BirdNET-Pi/discussions/373), [captainbodgit USB-module bias testing](http://captainbodgit.blogspot.com/2024/12/birdnet-pi-usb-audio-module-testing.html), [electret powering](https://www.epanorama.net/circuits/microphone_powering.html).

---

## 3. What we ruled out / proved this session

- **Mic dead?** NO — recorded fine on the operator's computer. (I wrongly hypothesized "dead" once; retracted.)
- **Transport/codec/CPU?** NO — doc #1's lean raw-PCM-over-TCP transport is realtime, lossless, stable; bridge posts live (device `last_seen` advances to the second). Leave it alone.
- **ALSA config (muted/wrong input/wrong channel)?** NO — mic-in is mono, capture switch on, gain set, AGC off, no mux/boost mis-set. Verified by `amixer -c1 contents` + native hw-params.
- **Wrong jack?** The mic must be in the **mic INPUT** hole; a mic in the **headphone OUTPUT** hole is guaranteed static (operator briefly had it there during one test).
- **Plug seating?** Reseated / snapped further — no change (still static). Not a seating issue.
- **Gain/AGC?** Swept gain 0→8→16 and AGC on — flatness stayed pinned ~0.74-0.83, no structured dips (the brief low-flatness frames were **capture-startup transients** at t=0 / low-freq, not birds — verified on the spectrogram).
- A **USB re-enumeration** once cleared a separate transient "stuck constant output" glitch on the DUKABEL (`echo 0/1 > /sys/bus/usb/devices/1-1/authorized`), but that's not the core issue.

**Conclusion:** mic good + two cards both static + correct config ⇒ the cards aren't delivering usable plug-in power to the electret. Fix = a powering interface.

---

## 4. The fixes ON ORDER, and how to test them

Operator ordered **both** (2026-06-12):

1. **Rode SC4** — 3.5mm TRS-female → TRRS-male adapter (~$10). Two uses:
   - **Mic-alive cross-check (already effectively confirmed):** Movo→SC4→phone, record a voice memo. (Mic already proven on his computer, so this is redundant now, but it's the canonical test.)
   - **On the Pi with the DUKABEL:** SC4 routes the Movo's signal onto the DUKABEL's *biased* CTIA mic/sleeve pin → can power + capture it. **Caveat:** combo-jack bias is only ~2.2 V, so it may work but be quiet. Test with `bt.sh`.

2. **Saramonic SR-EA2S** — USB audio interface with a 3.5mm mic input that **explicitly provides plug-in power** + a **TRS/TRRS switch** (~$10). Class-compliant USB (no drivers → works on the Pi like the other cards). **Most likely to "just work"** and the cleanest reliable fix. Set the switch to **TRS** for the Movo.

**Test procedure when a part arrives:**
1. Plug it into the Pi's OTG adapter in place of the current card; Movo into its **mic input** (SR-EA2S: switch = TRS).
2. SSH check it enumerated: `ssh pi@raspberrypi 'arecord -l'` → confirm a USB capture card on `card 1` (if it lands on a different card number, see §6).
3. `bash ~/workshop/birdnet/tools/bt.sh 15` → read the VERDICT line. If `SIGNAL (structured!)` or bird-band min drops well below ~0.2 → it works. Listen to the mp3 link to confirm by ear.
4. If it works: the streamer (`plughw:1,0`) and bridge are already pointed correctly — real detections should begin landing in `/apps/birds` for clear calls ≥0.5. Set the mic gain to a sane non-clipping level and `ssh pi@raspberrypi 'sudo alsactl store'`.

**Fallback if both somehow still static:** a DIY 9V bias box (9V → ~10kΩ → 0.22µF inline), or a self-powered USB lav mic (sidesteps plug-in power entirely). Not expected to be needed.

---

## 5. Current live state of the rig (all UP, WORKING)

- **Pi streamer:** `birdstream-lean.service` (user `birdstream`), `ffmpeg -f alsa -i plughw:1,0 -f s16le tcp://100.84.51.96:9000?listen=1`. Raw lossless PCM over the tailnet IP. Auto-starts, Restart=always. Now feeding **real audio** from the Saramonic SR-EA2S (switch=TRS).
- **Brain bridge:** `birds-yard-bridge.service` (systemd **--user**, manage with `XDG_RUNTIME_DIR=/run/user/1003 systemctl --user ...`). Pulls the TCP stream, 3 s WAV blocks, POSTs to `http://127.0.0.1:8000/birds/analyze` as device **"Yard Mic"** (token `yard-pi-springer-01`, mode fixed, location_id 6 "1849 South Springer Road", min_conf 0.5). Posting realtime, `last_seen` advancing ~every 3 s.
  - **STALL WATCHDOG (added 2026-06-13, `yard_bridge.py`):** a daemon thread kills ffmpeg if no audio bytes arrive for `STALL_SECS=30` (env `YARD_STALL_SECS`), forcing the existing reconnect loop. This fixes the silent ~17h wedge where a blocking `read()` on a half-open TCP stream never saw EOF. **If detections go quiet: FIRST check `last_seen` freshness; a stale `last_seen` with the service `active` means re-verify this watchdog is intact, then `systemctl --user restart birds-yard-bridge.service`.**
- **App:** device "Yard Mic" registered (`owner_user_id=1, shared=1` — claimed 2026-06-13 so it shows on the public site), `last_seen` advancing live, detections landing in `/apps/birds` and on `birds.benmiriello.com`. Analyzer resident on `127.0.0.1:8012`. Browser DB `~/birdnet/data/browser-stations.db` (no `sqlite3` CLI on the box — use `python3` + `sqlite3` module).
- **Mic gain:** was 12/16 on the now-removed AJDPOI; the close-range SIGNAL test on the Saramonic clipped at 0 dBFS, so the Saramonic's capture gain runs hot — re-tune to a non-clipping level for distant birds and `ssh pi@raspberrypi 'sudo alsactl store'`. (Not yet re-tuned as of this writing.)

---

## 6. Operational cheatsheet & gotchas

```bash
# THE test (use this for everything):
bash ~/workshop/birdnet/tools/bt.sh 15

# manual capture (stop streamer first; listen=1 single-client):
ssh pi@raspberrypi 'sudo systemctl stop birdstream-lean; ffmpeg -nostdin -f alsa -channels 1 -sample_rate 48000 -i plughw:1,0 -t 15 /tmp/x.wav </dev/null; sudo systemctl start birdstream-lean'

# which card / mixer:
ssh pi@raspberrypi 'arecord -l; amixer -c1 contents'

# bridge:
XDG_RUNTIME_DIR=/run/user/1003 systemctl --user restart birds-yard-bridge.service
XDG_RUNTIME_DIR=/run/user/1003 journalctl --user -u birds-yard-bridge.service -n 30

# spectral flatness of a wav (the right metric), via script (egress-guard safe):
#   see fb() in tools/bt.sh
```
- **`-listen 1` is single-client** — only the bridge reads at a time. `bt.sh` stops the streamer for you. Don't pull a second reader while the bridge is connected.
- **USB card swap renumbers the ALSA device** and kills any in-progress `-f alsa` capture. Swap hardware FIRST, then capture. If a new interface lands on `card 2` not `card 1`, either fix `plughw:1,0` in `birdstream-lean.service` or unplug other USB audio. Auto-detect: `arecord -l | grep "USB Audio"`.
- **Headphone jack = OUTPUT.** A mic there = guaranteed static.
- **rsync/scp to the Pi must use the FQDN** `pi@raspberrypi.tail38e62e.ts.net` (egress-guard treats the short name as external). Plain `ssh pi@raspberrypi` is fine.
- Tailnet/security/ACL/streamer details: see doc #1 `BIRDSTATION-CONTEXT.md` §2,5,6 (still accurate).
- **Don't judge audio by dB level.** Use flatness. (The single biggest lesson here.)

---

## 7. Behavioral notes for the next agent (operator feedback this session)

- **Don't inflate a possibility into a proven blocker, then prescribe one required fix.** State KNOWN vs HYPOTHESIS explicitly. The operator caught me doing this repeatedly ("mic is dead", "AJDPOI fixes it", "buy this") on incomplete info.
- **Be fast and terse.** Multi-step, multi-minute investigations with regenerated spectrograms and long reasoning burned the operator's patience. One-shot tools (`bt.sh`) + short outputs.
- **The operator is at the hardware, away from the screen, during physical tests** — give the FULL instruction set + a fixed timer BEFORE he leaves; he can't read mid-test prompts. (See memory `feedback_physical_test_instructions_upfront`.)
- **Trust his ears as ground truth.** He repeatedly heard "pure static" while my level metrics said "signal." He was right every time.

---

## 8. Pointers
- Doc #1 (transport, security, ACL, streamer/bridge build): `~/workshop/birdnet/BIRDSTATION-CONTEXT.md`.
- Memories: `project_birdstation_pi_2026_06_12` (updated with this root cause + the flatness metric lesson), `feedback_physical_test_instructions_upfront`, `project_birdnet_yard_station` (app-side pipeline).
- Test tool: `~/workshop/birdnet/tools/bt.sh`. Listen server: `python3 -m http.server 8765 --bind 100.104.196.85` serving `/tmp/yard_listen/` (auto-started by bt.sh; /tmp is ephemeral).
- App: device "Yard Mic", loc 6, token `yard-pi-springer-01`, threshold 0.5. Backend `~/workshop/backend` (:8000), analyzer `~/workshop/birdnet/analyzer` (:8012).
