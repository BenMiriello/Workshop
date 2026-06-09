#!/usr/bin/env python3
"""Phase 2 core (de-risked standalone): occlusion-sensitivity localization.

BirdNET classifies a whole 3s clip and won't say WHEN within it the call is. This
finds it WITHOUT model gradients (so it works with the existing TFLite model and
needs no downloads): slide a short mask across the 3s clip, zero that region, and
re-score; the mask that most DROPS the species' confidence sits on the call. The
peak-drop time = the call instant.

Uses the EXISTING analyzer /analyze (POSTs masked temp WAVs) — no change to the
live service, no restart. This proves the approach before wiring it into the
service as an endpoint and into the marker refinement for ambiguous detections.

  python3 locate.py single_oak-titmouse.wav "Baeolophus inornatus" --start 5.5
  python3 locate.py group_birdnet-sapsucker-woods.wav "Cyanocitta cristata" --start 21 --lat 42.48 --lon -76.45 --week 23
"""
import argparse, array, json, os, subprocess, tempfile, urllib.request, wave

ANALYZER = "http://127.0.0.1:8012"

def transcode_slice(src, dst, start, dur):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-t", str(dur),
                    "-i", src, "-ac", "1", "-ar", "48000", "-sample_fmt", "s16", dst], check=True)

def read_wav(path):
    with wave.open(path, "rb") as w:
        sr, n = w.getframerate(), w.getnframes(); raw = w.readframes(n)
    a = array.array("h"); a.frombytes(raw)
    return a, sr

def conf_of(samples, sr, tmp, sci, lat, lon, week):
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(samples.tobytes())
    body = {"path": tmp, "min_conf": 0.0}
    if lat is not None and lon is not None:
        body.update(lat=lat, lon=lon)
        if week: body["week"] = week
    req = urllib.request.Request(ANALYZER + "/analyze", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        dets = json.loads(r.read()).get("detections", [])
    return max([d["confidence"] for d in dets if d["scientific_name"] == sci], default=0.0)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip"); ap.add_argument("sci")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--win", type=float, default=3.0)
    ap.add_argument("--mask", type=float, default=0.6)
    ap.add_argument("--step", type=float, default=0.3)
    ap.add_argument("--lat", type=float); ap.add_argument("--lon", type=float); ap.add_argument("--week", type=int)
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        win = os.path.join(td, "win.wav"); tmp = os.path.join(td, "m.wav")
        transcode_slice(a.clip, win, a.start, a.win)
        y, sr = read_wav(win)
        base = conf_of(y, sr, tmp, a.sci, a.lat, a.lon, a.week)
        print(f"{a.sci}  window [{a.start:.1f},{a.start + a.win:.1f}]s  baseline conf={base:.3f}")
        if base < 0.05:
            print("  species not present in this window (nothing to localize)"); return
        prof = []
        t = 0.0
        while t < a.win:
            m = array.array("h", y)
            i, j = int(t * sr), int(min(a.win, t + a.mask) * sr)
            for k in range(i, j): m[k] = 0
            c = conf_of(m, sr, tmp, a.sci, a.lat, a.lon, a.week)
            prof.append((t + a.mask / 2, base - c, c))
            t += a.step
        peak = max(prof, key=lambda p: p[1])
        print("  mask-center  conf-when-masked  drop")
        for tc, drop, c in prof:
            bar = "#" * int(max(0, drop) * 40)
            mark = "  <= call here" if (tc, drop, c) == peak else ""
            print(f"   {a.start + tc:5.2f}s      {c:5.3f}          {drop:+.3f} {bar}{mark}")
        print(f"  => call localized at ~{a.start + peak[0]:.2f}s (drop {peak[1]:.3f})")

if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as e:
        raise SystemExit(f"analyzer unreachable at {ANALYZER} ({e})")
