# Owntracks iOS — continuous background location

The web Location tab is gone. Browser geolocation can't run when Safari
is backgrounded or the tab is closed — iOS aggressively suspends it.

Owntracks is a free native iOS app that holds CoreLocation entitlements
and streams location to a self-hosted HTTP endpoint forever (even with
phone locked / Workshop tab closed / Safari killed). Position data lives
only on this Debian box, gated by HTTP Basic Auth, on the tailnet only —
no third party.

## One-time setup

1. App Store → install **Owntracks** (free, no account required).
2. Open the app → Settings (gear icon).
3. **Mode:** HTTP.
4. **URL:** `https://debian.tail38e62e.ts.net:8446/api/location/owntracks`
5. **Authentication:** Basic.
   - **Username:** `workshop`
   - **Password:** (in `~/.config/clawdbot/owntracks-auth.env` on the box)
6. **TrackerID (TID):** any 2-letter code (e.g. `bm`) — surfaces as `source: "owntracks-bm"` in the backend.
7. **DeviceID:** any string (e.g. `iphone`).
8. **Reporting mode:** **Significant** (default; battery-friendly).
   - iOS only reports when you move meaningfully (~50–500m) — uses
     Apple's CoreLocation significant-change events, which Apple won't
     kill to save battery.
   - Switch to **Move** temporarily for commute mornings (~30s cadence,
     higher accuracy, higher battery cost).
9. **iOS Settings → Owntracks → Location → "Always"** (not "While Using").
10. **iOS Settings → Owntracks → Background App Refresh → On.**
11. In the app's main view, tap the upload-arrow once to send a manual
    ping and confirm the endpoint works.

## Verifying it's working

On the Debian box:

```bash
curl -sS http://127.0.0.1:8000/location/latest | python3 -m json.tool
```

You should see `source: "owntracks-<tid>"` with a recent `ts`.

Tail the backend log for `POST /location/owntracks 200 OK` lines:

```bash
tail -f /tmp/workshop-backend.log | grep owntracks
```

## Privacy posture

- Endpoint sits behind Tailscale Serve — **only your tailnet** reaches it.
- TLS via Tailscale-issued Let's Encrypt cert for `*.tail38e62e.ts.net`.
- HTTP Basic Auth on every POST — no auth, no write.
- Owntracks HTTP mode is point-to-point — it does **not** call home to
  any third-party server (no MQTT broker, no Owntracks cloud).
- All position data on disk at `~/workshop/.runtime/location.jsonl` only.
- Workshop dashboard has a "Clear history" button that truncates the
  file end-to-end.

## Battery notes

- "Significant" mode uses ~1–3% battery / day in practice.
- "Move" mode (active polling every 30s) can drain 10–15% / day; flip
  it on at the start of a commute, off when you arrive.
- Owntracks has built-in scheduled mode-switching if you want
  automatic "Move during AM commute / Significant otherwise".

## Rotating the shared secret

```bash
openssl rand -base64 24 | tr -d '/+=' > /tmp/newpass
# Edit ~/.config/clawdbot/owntracks-auth.env, replace OWNTRACKS_PASS value
# Restart backend: pkill -f 'uvicorn.*main:app' && nohup ... (see CLAUDE.md)
# Update password in Owntracks iOS app settings.
```
