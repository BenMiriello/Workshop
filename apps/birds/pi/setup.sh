#!/usr/bin/env bash
# Pi capture node bring-up (Phase 0 + Phase 1 + Phase 2 install).
# Run ON the Pi after flashing Pi OS Lite (64-bit), enabling SSH + wifi via the
# Imager, and first boot. Idempotent-ish; safe to re-run.
#
# This installs and wires the streamer. It does NOT join Tailscale for you
# (that needs an interactive auth URL) or set the ALSA device (confirm it first).
set -euo pipefail

echo "== Pi capture node setup =="

# --- Phase 0: base packages ---
sudo apt-get update
sudo apt-get install -y ffmpeg alsa-utils curl

# --- Tailscale (Phase 0) ---
if ! command -v tailscale >/dev/null 2>&1; then
  echo "Installing Tailscale..."
  curl -fsSL https://tailscale.com/install.sh | sh
fi
echo
echo ">> Join the tailnet now (interactive, opens an auth URL):"
echo "     sudo tailscale up --hostname=birdpi"
echo ">> Confirm the yard has solid wifi: run 'ping -c5 debian.<tailnet>.ts.net'"
echo

# --- Phase 1: audio device ---
echo "== ALSA capture devices (find your USB sound card) =="
arecord -l || true
echo
echo ">> Note the card number above (e.g. card 1). Set MTX_ALSA_DEVICE in"
echo "   mediamtx.service accordingly (plughw:<card>,0). Then run record-test.sh."
echo

# --- Phase 2: mediamtx (RTSP server) ---
if ! command -v mediamtx >/dev/null 2>&1 && [ ! -x /usr/local/bin/mediamtx ]; then
  echo "Installing mediamtx..."
  ARCH="$(uname -m)"
  case "$ARCH" in
    aarch64|arm64) MTX_ARCH=arm64v8 ;;
    armv7l)        MTX_ARCH=armv7   ;;
    armv6l)        MTX_ARCH=armv6   ;;
    x86_64)        MTX_ARCH=amd64   ;;
    *) echo "Unknown arch $ARCH, install mediamtx manually from github.com/bluenviron/mediamtx/releases"; exit 1 ;;
  esac
  # Pin/confirm the version against the latest release before trusting this.
  MTX_VER="${MTX_VER:-v1.9.3}"
  TMP="$(mktemp -d)"
  curl -fsSL -o "$TMP/m.tar.gz" \
    "https://github.com/bluenviron/mediamtx/releases/download/${MTX_VER}/mediamtx_${MTX_VER}_linux_${MTX_ARCH}.tar.gz"
  tar -xzf "$TMP/m.tar.gz" -C "$TMP"
  sudo install -m 0755 "$TMP/mediamtx" /usr/local/bin/mediamtx
  rm -rf "$TMP"
fi

sudo mkdir -p /etc/mediamtx
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sudo cp "$SCRIPT_DIR/mediamtx.yml" /etc/mediamtx/mediamtx.yml
sudo cp "$SCRIPT_DIR/mediamtx.service" /etc/systemd/system/mediamtx.service

echo
echo ">> Edit MTX_ALSA_DEVICE in /etc/systemd/system/mediamtx.service if not plughw:1,0"
echo ">> Then: sudo systemctl daemon-reload && sudo systemctl enable --now mediamtx"
echo ">> Verify from the Brain:"
echo "     ffprobe rtsp://birdpi.<tailnet>.ts.net:8554/yard"
echo "== done =="
