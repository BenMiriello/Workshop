# Brain setup: BirdNET-Go on the Debian box

Native binary install. Native over Docker because: direct filesystem ownership
of the SQLite DB and clips (the Workshop backend reads them as the same user,
no volume mapping), simpler systemd integration, and no container layer between
the app and the GPU if Perch/ONNX is added later.

## Install

```bash
# Official installer (Debian/Ubuntu/Pi OS). Confirm the URL against the repo
# README before running, it moves.
curl -fsSL https://github.com/tphakala/birdnet-go/raw/main/install.sh | bash
# Binary lands at /usr/local/bin/birdnet-go (verify path; adjust the .service if different).
birdnet-go --version
```

## Wire up

```bash
mkdir -p $BIRDNET_DATA_DIR $BIRDNET_CLIPS
# Generate the release's default config once, then reconcile our template into it:
birdnet-go realtime --help            # inspect current flags/keys
cp $WORKSHOP/apps/birds/brain/config.yaml $BIRDNET_DATA_DIR/config.yaml
cp $WORKSHOP/apps/birds/brain/secrets.env.example $WORKSHOP/apps/birds/brain/secrets.env
chmod 600 $WORKSHOP/apps/birds/brain/secrets.env
# edit secrets.env: real lat/long + Pi tailscale host

sudo cp $WORKSHOP/apps/birds/brain/birdnet-go.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now birdnet-go
journalctl -u birdnet-go -f
```

## Verify (Phase 3 gate)

- `journalctl -u birdnet-go` shows it connected to the RTSP source, no reconnect spam.
- Stock dashboard reachable over tailnet at `http://debian.tailnet:8080` (NOT public).
- After a real bird (or a test playback near the mic), a row appears in the DB:
  ```bash
  sqlite3 $BIRDNET_DATA_DIR/birdnet.db \
    "select date,time,common_name,round(confidence,2) from notes order by id desc limit 5;"
  ```
- A WAV clip lands in `$BIRDNET_CLIPS/`.
- The Workshop app at `/apps/birds` shows the same rows (it reads this DB read-only).

## Webhook into the assistant

Configure the notification webhook (block name varies by release; see config.yaml
notes) to POST `brain/webhook-template.json` to `http://127.0.0.1:8000/birds/webhook`.
Confirm with:

```bash
journalctl -u masini-bot -f   # or wherever the OpenClaw event consumer logs
# and check the Workshop events feed picked up a "birdnet" source event
curl -s localhost:8000/events/recent?source=birdnet | head
```

## GPU note

BirdNET-Go is TFLite/CPU by default and CPU handles vanilla detection fine. The
3090 only earns its keep for optional Perch v2 / multi-model consensus (ONNX
Runtime) and the assistant layer. Treat GPU accel as an optional later phase;
do not block bring-up on it.
