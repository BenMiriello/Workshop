#!/usr/bin/env bash
# Phase 1 audio sanity check. Records a short clip from the USB sound card and
# reports level stats so you can confirm: device works, capture is mono, noise
# floor is low (no Pi hum / USB whine). Run ON the Pi.
#
#   ./record-test.sh                 # uses plughw:1,0, 6 seconds
#   ./record-test.sh plughw:2,0 10   # explicit device + duration
set -euo pipefail

DEV="${1:-plughw:1,0}"
SECS="${2:-6}"
OUT="/tmp/birdmic-test.wav"

echo "Recording ${SECS}s from ${DEV} -> ${OUT}"
echo "(make some noise / whistle near the mic)"
# Capture at the same format the stream uses: 48k mono s16.
ffmpeg -hide_banner -loglevel error -y \
  -f alsa -channels 1 -sample_rate 48000 -i "$DEV" \
  -t "$SECS" -c:a pcm_s16le "$OUT"

echo
echo "== Channel count (must be 1 = mono) =="
ffprobe -hide_banner -v error -show_entries stream=channels,sample_rate -of default=noprint_wrappers=1 "$OUT"

echo
echo "== Level stats (watch mean/max volume + noise floor) =="
# volumedetect prints mean_volume / max_volume in dB. A quiet room should sit
# well below 0 dB with headroom; a constant loud floor near the mean means hum.
ffmpeg -hide_banner -v error -i "$OUT" -af volumedetect -f null /dev/null 2>&1 | grep -E "mean_volume|max_volume" || true

echo
echo "Copy it off the Pi to actually listen:"
echo "  scp pi@birdpi:/tmp/birdmic-test.wav ."
echo
echo "If you hear hum/whine: move the USB sound card onto a short extension"
echo "cable away from the Pi board, re-test. That is the documented fix for"
echo "Pi-induced electrical noise on USB audio."
