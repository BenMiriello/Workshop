# Health Auto Export iOS — Apple Health → Workshop

Open Wearables was poorly built / out of date. Replaced with **Health Auto
Export** (HAE) — a paid (~$5) iOS app that periodically exports Apple Health
data to a self-hosted HTTP endpoint. Same private-secure pattern as
Owntracks: native app, background entitlements, push to Tailscale-only URL
gated by HTTP Basic Auth.

## One-time setup

1. **App Store** → install **Health Auto Export — JSON+CSV** (the "JSON+CSV" build
   is the one with HTTP/webhook export; there's a free version that only does
   manual CSV — get the paid one, ~$5).
2. Open the app. On first launch it asks for Apple Health permissions —
   grant **all** metric categories you want streamed (HRV, heart rate, steps,
   sleep, active energy, workouts, etc.). You can change later.
3. **Automations** tab (bottom) → **+** to create a new automation:
   - **Frequency:** Daily (or Hourly if you want fresher data)
   - **Aggregate Data:** "Daily Average" (or "Show All Samples" for raw)
   - **Data to Include:** all the metric categories you granted access to
   - **Export Type:** **REST API**
   - **URL:** `https://debian.tail38e62e.ts.net:8446/api/health/ingest`
   - **Authentication:** Basic Auth
     - User: `workshop`
     - Password: (from `~/.config/clawdbot/health-auth.env` on the box —
       grab via Workshop → Settings → Health, tap the reveal icon next to
       "pass")
   - **Format:** JSON
4. **iOS Settings → Health Auto Export → Background App Refresh: ON**
5. Save the automation. Tap the manual "Export Now" button once to fire a
   first push and confirm the endpoint accepts it.

## Verify

On the box:
```bash
curl -sS https://debian.tail38e62e.ts.net:8446/api/health/latest | python3 -m json.tool
```
Should return `{"metrics": {"heart_rate": {...}, "step_count": {...}, ...}}`.

Workshop → Settings → Health (Apple Health) shows per-metric latest values
with timestamps. The status row goes green once data starts flowing.

## Privacy posture

- Endpoint sits behind Tailscale Serve — **tailnet only**, no public internet.
- TLS via Tailscale-issued Let's Encrypt cert.
- HTTP Basic Auth on every POST — no auth, no write.
- HAE in REST mode is point-to-point — does **not** call home to any
  third-party server.
- All metric data on disk at `~/workshop/.runtime/health.jsonl` only.
- Workshop → Settings → Health → "clear all data" wipes the file.

## Replacing Open Wearables

OW was an off-the-shelf wearables backend that turned out poorly maintained.
It's still on `:8445` for now (Workshop Apps tile links there) but with HAE
streaming, you can ignore it. We'll drop the OW Tailscale mount + Apps tile
once you've confirmed HAE data is what you actually want.

## Rotating the shared secret

```bash
openssl rand -base64 24 | tr -d '/+=' > /tmp/newpass
# Edit ~/.config/clawdbot/health-auth.env, replace HEALTH_PASS
# Restart backend: pkill -f 'uvicorn.*main:app' && cd ~/workshop/backend && \
#   set -a && source ~/.config/clawdbot/auth.env && source ~/.config/clawdbot/health-auth.env && set +a && \
#   nohup .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 > /tmp/workshop-backend.log 2>&1 & disown
# Update Health Auto Export password in the automation config.
```
