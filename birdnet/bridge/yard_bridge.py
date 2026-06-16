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
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave

SRC     = os.environ.get("YARD_SRC",   "tcp://raspberrypi:9000")  # raw s16le over plain TCP
API     = os.environ.get("YARD_API",   "http://127.0.0.1:8000/birds/analyze")
TOKEN   = os.environ.get("YARD_TOKEN", "yard-pi-springer-01")
DEVICE  = os.environ.get("YARD_DEVICE","Yard Mic")
LOCID   = os.environ.get("YARD_LOCID", "6")
MINCONF = os.environ.get("YARD_MINCONF","0.5")

RATE, CH, SW = 48000, 1, 2
CHUNK_SECS   = 3
CHUNK_BYTES  = RATE * CH * SW * CHUNK_SECS

# heartbeat: how often to log a "still alive, N chunks, M detections" line
HEARTBEAT_SECS = 300

# Stall guard: ffmpeg's read() is blocking, so a half-open / stalled TCP stream
# (connection accepted by the Pi but no PCM flowing) would hang the read forever
# with no clean EOF to trigger the reconnect loop. This is the silent 17h wedge we
# hit. If no audio bytes arrive for this long, kill ffmpeg to force a reconnect.
# 48kHz*16bit means ~96 KB/s when healthy, so any multi-second gap is abnormal.
STALL_SECS = int(os.environ.get("YARD_STALL_SECS", "30"))


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

    while True:
        proc = run_ffmpeg()
        state["proc"] = proc
        state["last_data"] = time.time()
        buf = b""
        try:
            while True:
                data = proc.stdout.read(CHUNK_BYTES - len(buf))
                if not data:
                    break  # stream ended (or watchdog killed a stalled ffmpeg)
                state["last_data"] = time.time()
                buf += data
                if len(buf) < CHUNK_BYTES:
                    continue
                resp = post_chunk(buf[:CHUNK_BYTES])
                buf = buf[CHUNK_BYTES:]
                chunks += 1
                if resp and resp.get("detections"):
                    det_events += 1
                    log("DETECT: " + fmt_dets(resp["detections"]))
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
