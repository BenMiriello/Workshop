#!/usr/bin/env python3
"""Yard bridge: pull the Pi's RTSP yard stream, cut it into fixed 3s WAV chunks,
and POST each to the Workshop /birds/analyze endpoint as the 'Yard Mic' fixed
device (pinned to the yard location). This reuses the exact browser-detection
pipeline (range filter, confidence floor, trust model, recordings), so the Pi
shows up as a normal registered device and its detections land in /apps/birds.

Robustness: ffmpeg owns the RTSP connection; if the stream drops (Pi reboot,
wifi blip) ffmpeg exits and we reconnect after a short backoff. A failed POST
(backend restarting) is logged and skipped, never fatal. systemd --user wraps
the whole thing with Restart=always for process-level recovery + boot start.

No external Python deps (stdlib + curl + ffmpeg).
"""
import collections
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave

import numpy as np

SRC     = os.environ.get("YARD_SRC",   "tcp://raspberrypi:9000")  # raw s16le over plain TCP
API     = os.environ.get("YARD_API",   "http://127.0.0.1:8000/birds/analyze")
TOKEN   = os.environ.get("YARD_TOKEN", "yard-pi-springer-01")
DEVICE  = os.environ.get("YARD_DEVICE","Yard Mic")
LOCID   = os.environ.get("YARD_LOCID", "6")
MINCONF = os.environ.get("YARD_MINCONF","0.5")

RATE, CH, SW = 48000, 1, 2
CHUNK_SECS   = 3
CHUNK_BYTES  = RATE * CH * SW * CHUNK_SECS
# Read the stream in small increments (~30 ms) rather than a whole 3s chunk at once,
# so the live-spectrogram ring advances continuously. Reading a full CHUNK_BYTES per
# read() makes the ring jump every 3s, which the column thread renders as repeated
# columns / visible vertical seams. The 3s analyze chunk is still assembled from
# these small reads below.
READ_BYTES = RATE * CH * SW * 30 // 1000   # ~30 ms

# heartbeat: how often to log a "still alive, N chunks, M detections" line
HEARTBEAT_SECS = 300

# Stall guard: ffmpeg's read() is blocking, so a half-open / stalled TCP stream
# (connection accepted by the Pi but no PCM flowing) would hang the read forever
# with no clean EOF to trigger the reconnect loop. This is the silent 17h wedge we
# hit. If no audio bytes arrive for this long, kill ffmpeg to force a reconnect.
# 48kHz*16bit means ~96 KB/s when healthy, so any multi-second gap is abnormal.
STALL_SECS = int(os.environ.get("YARD_STALL_SECS", "30"))

# --- Live spectrogram --------------------------------------------------------
# The yard is a real live stream, so we draw the live feed's scrolling histogram
# straight off it: a background thread takes the most recent few seconds of PCM,
# turns it into spectrogram columns (same 0..255 band format the browser recorder
# publishes), and POSTs them to /birds/live/publish ~1/s. The backend overwrites
# one live_feed row per device, the app polls /live, and FeedCanvas renders it.
# This is decoupled from the 3s analyze chunks: the histogram stays smooth even
# between detections.
LIVE_API   = os.environ.get("YARD_LIVE_API", API.replace("/analyze", "/live/publish"))
# These mirror the browser recorder (recorder.tsx) so the yard feed looks IDENTICAL
# to any other live feed in the app: 40 LINEAR bands up to 12 kHz, a fixed dB window
# (WebAudio getByteFrequencyData defaults), 0.4 temporal smoothing, a column every
# ~30 ms, and the most recent 150 columns published every 800 ms (a scrolling window,
# not a fresh frame each tick).
NBANDS     = 40                 # FeedCanvas reads the band count from cols[0].length
MAXHZ      = 12000.0            # top of the displayed range (SPEC_MAX_HZ)
FFT_WIN    = 1024              # analyser.fftSize
COL_MS     = 0.030             # SPEC_COL_MS: one column ~every 30 ms (~33/s)
PUBLISH_SECS = 0.800          # publish on an 800 ms tick
NCOLS_PUB  = 300              # publish the last ~9s of columns (the viewer compresses
                              # them so the feed scrolls slowly; needs the wider window)
HIST_MAX   = 400             # rolling column history (> NCOLS_PUB)
SMOOTH     = 0.4            # analyser.smoothingTimeConstant
MIN_DB, MAX_DB = -100.0, -30.0  # WebAudio AnalyserNode default min/maxDecibels

# The ring is a PLAYBACK BUFFER: the producer drains it from the FRONT at a steady
# real-time rate (one HOP per column) regardless of how burstily the Pi delivers
# audio, so the feed scrolls evenly. PREBUFFER adds a small latency cushion that
# absorbs jitter; the buffer is capped so a reconnect burst can't pile up unbounded.
HOP_BYTES      = int(round(RATE * COL_MS)) * SW       # advance per column (~30 ms)
WIN_BYTES      = FFT_WIN * SW
PREBUFFER_BYTES = int(RATE * SW * 0.30)               # ~0.3 s jitter cushion
MAX_BYTES      = int(RATE * SW * 1.50)                # cap latency at ~1.5 s

_HANN   = np.hanning(FFT_WIN).astype(np.float32)
_WINSUM = float(_HANN.sum())
_NBINS  = FFT_WIN // 2 + 1
_TOPBIN = min(FFT_WIN // 2, int(np.ceil(MAXHZ / (RATE / 2.0) * (FFT_WIN // 2))))

_roll = bytearray()
_roll_lock = threading.Lock()
_roll_ts = 0.0          # wall time of the last PCM appended (stream-liveness gate)
_hearing: list = []     # last chunk's detections, shown as "now hearing"
_hearing_ts = 0.0

_prevmag = np.zeros(_NBINS, dtype=np.float32)   # for WebAudio-style temporal smoothing
_hist = collections.deque(maxlen=HIST_MAX)       # rolling spectrogram columns
_hist_lock = threading.Lock()


def _roll_add(data: bytes):
    global _roll_ts
    with _roll_lock:
        _roll.extend(data)
        # Bound latency/memory: after a reconnect burst, drop the oldest excess so
        # we don't play minutes-behind audio.
        if len(_roll) > MAX_BYTES:
            del _roll[:len(_roll) - MAX_BYTES]
        _roll_ts = time.time()


def _column(x: np.ndarray) -> list:
    """One spectrogram column from FFT_WIN float samples: WebAudio-equivalent byte
    levels (fixed dB window + temporal smoothing), pooled into NBANDS linear bands,
    band 0 = lowest freq (FeedCanvas draws band 0 at the bottom)."""
    global _prevmag
    # Magnitude normalized so a full-scale tone -> ~0 dB (matches getByteFrequencyData).
    mag = np.abs(np.fft.rfft(x * _HANN)) * 2.0 / _WINSUM
    _prevmag = SMOOTH * _prevmag + (1.0 - SMOOTH) * mag
    db = 20.0 * np.log10(_prevmag + 1e-10)
    byte = np.clip(255.0 * (db - MIN_DB) / (MAX_DB - MIN_DB), 0.0, 255.0)
    out = []
    for b in range(NBANDS):
        lo = int(b / NBANDS * _TOPBIN)
        hi = max(lo + 1, int((b + 1) / NBANDS * _TOPBIN))
        out.append(int(byte[lo:hi].mean()))
    return out


def column_producer():
    """Drain the PCM ring from the front at a steady real-time rate (one HOP per
    column), producing a column from each successive window. Because consumption is
    paced (not "read the latest tail"), bursty arrival is absorbed by the buffer and
    the feed scrolls evenly. Underrun (a real stream gap) pauses until PREBUFFER
    refills, so the feed never fabricates repeated columns."""
    primed = False
    next_t = time.monotonic()
    while True:
        # Deadline pacing: hold the column rate at exactly real-time so consumption
        # matches arrival (no slow drift into the cap, no fabricated columns).
        next_t += COL_MS
        delay = next_t - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_t = time.monotonic()       # fell behind; resync
        if time.time() - _roll_ts > 1.0:   # stream stalled -> reset, wait for audio
            primed = False
            continue
        with _roll_lock:
            avail = len(_roll)
            if not primed:
                if avail < PREBUFFER_BYTES + WIN_BYTES:
                    continue
                primed = True
            if avail < WIN_BYTES + HOP_BYTES:   # underrun -> re-prime before resuming
                primed = False
                continue
            window = bytes(_roll[:WIN_BYTES])
            del _roll[:HOP_BYTES]               # advance playback by one hop
        x = np.frombuffer(window, dtype="<i2").astype(np.float32) / 32768.0
        try:
            col = _column(x)
        except Exception as e:
            log("column error: %s" % e)
            continue
        with _hist_lock:
            _hist.append(col)


def publish_spectrogram(cols: list, hearing: list):
    body = json.dumps({"token": TOKEN, "cols": cols, "hearing": hearing})
    try:
        subprocess.run(
            ["curl", "-s", "--max-time", "5", "-X", "POST",
             "-H", "content-type: application/json", "--data-binary", "@-", LIVE_API],
            input=body, capture_output=True, text=True)
    except Exception:
        pass


def spectrogram_publisher():
    while True:
        time.sleep(PUBLISH_SECS)
        # Only publish while the stream is actually flowing; if the Pi drops, stop
        # so the feed ages out of /live (LIVE_TTL_S) and the histogram goes offline
        # instead of freezing on a stale frame.
        if time.time() - _roll_ts > 2.0:
            continue
        with _hist_lock:
            cols = list(_hist)[-NCOLS_PUB:]
        if len(cols) < 5:
            continue
        hearing = _hearing if (time.time() - _hearing_ts) < 6 else []
        publish_spectrogram(cols, hearing)


def log(msg):
    print("[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def post_chunk(pcm: bytes) -> dict | None:
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        w = wave.open(path, "wb")
        w.setnchannels(CH); w.setsampwidth(SW); w.setframerate(RATE)
        w.writeframes(pcm); w.close()
        r = subprocess.run(
            ["curl", "-s", "--max-time", "20", "-X", "POST",
             "-F", "audio=@%s;type=audio/wav" % path,
             "-F", "token=%s" % TOKEN,
             "-F", "device_name=%s" % DEVICE,
             "-F", "mode=fixed",
             "-F", "location_id=%s" % LOCID,
             "-F", "min_conf=%s" % MINCONF,
             "-F", "station=%s" % DEVICE,
             API],
            capture_output=True, text=True)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except Exception:
        return None
    finally:
        if path:
            try: os.unlink(path)
            except OSError: pass


def fmt_dets(dets):
    out = []
    for d in dets:
        name = d.get("common_name") or d.get("scientific_name") or "?"
        conf = d.get("confidence", 0) or 0
        stored = d.get("stored")
        status = d.get("status")
        tag = ""
        if stored is False:
            tag = " (dup/not-stored)"
        elif status:
            tag = " [%s]" % status
        out.append("%s %.0f%%%s" % (name, conf * 100, tag))
    return ", ".join(out)


def run_ffmpeg():
    # Source is a raw s16le byte stream over plain TCP (the Pi's lean streamer).
    # Tell ffmpeg the raw input format explicitly; output the same to stdout.
    return subprocess.Popen(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
         "-f", "s16le", "-ar", str(RATE), "-ac", str(CH), "-i", SRC,
         "-f", "s16le", "-ac", str(CH), "-ar", str(RATE), "-"],
        stdout=subprocess.PIPE)


def main():
    log("yard-bridge starting: %s -> %s  device='%s' loc=%s minconf=%s"
        % (SRC, API, DEVICE, LOCID, MINCONF))
    chunks = 0
    det_events = 0
    last_hb = time.time()

    # Watchdog: kills a stalled ffmpeg so the blocking read() below returns EOF and
    # the reconnect loop runs. state["last_data"] is the heartbeat the read updates.
    state = {"proc": None, "last_data": time.time()}
    def watchdog():
        while True:
            time.sleep(5)
            p = state["proc"]
            if p and p.poll() is None and time.time() - state["last_data"] > STALL_SECS:
                log("no audio for %ds; killing ffmpeg to force reconnect" % STALL_SECS)
                try: p.kill()
                except Exception: pass
    threading.Thread(target=watchdog, daemon=True).start()
    # Continuously turn the live PCM into scrolling spectrogram columns + publish.
    threading.Thread(target=column_producer, daemon=True).start()
    threading.Thread(target=spectrogram_publisher, daemon=True).start()

    global _hearing, _hearing_ts
    while True:
        proc = run_ffmpeg()
        state["proc"] = proc
        state["last_data"] = time.time()
        buf = b""
        try:
            while True:
                data = proc.stdout.read(READ_BYTES)
                if not data:
                    break  # stream ended (or watchdog killed a stalled ffmpeg)
                state["last_data"] = time.time()
                buf += data
                _roll_add(data)              # feed the live-spectrogram window (continuously)
                if len(buf) < CHUNK_BYTES:
                    continue
                resp = post_chunk(buf[:CHUNK_BYTES])
                buf = buf[CHUNK_BYTES:]
                chunks += 1
                if resp and resp.get("detections"):
                    det_events += 1
                    log("DETECT: " + fmt_dets(resp["detections"]))
                    _hearing = [{"common_name": d.get("common_name"),
                                 "scientific_name": d.get("scientific_name"),
                                 "confidence": d.get("confidence", 0),
                                 "is_bird": d.get("is_bird", True)}
                                for d in resp["detections"]]
                    _hearing_ts = time.time()
                now = time.time()
                if now - last_hb >= HEARTBEAT_SECS:
                    log("heartbeat: %d chunks posted, %d detection events so far"
                        % (chunks, det_events))
                    last_hb = now
        finally:
            try: proc.kill()
            except Exception: pass
        log("stream ended/lost; reconnecting in 5s")
        time.sleep(5)


if __name__ == "__main__":
    main()
