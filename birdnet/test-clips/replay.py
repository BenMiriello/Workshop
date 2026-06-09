#!/usr/bin/env python3
"""Replay a clip through the local BirdNET analyzer exactly like the live recorder
does — 3s rolling windows stepped 1s — and print the per-window confidence
series. That series is precisely what the spectrogram localizer (specLocalize.ts)
consumes to group windows into events and place the marker, so this lets you see
on real audio what the localizer is working from (and how the range filter and
dedup would behave).

Needs ffmpeg on PATH and the analyzer running on 127.0.0.1:8012 (a local service,
allowed by the egress guard). Does NOT need the FastAPI backend.

  python3 replay.py group_birdnet-sapsucker-woods.wav
  python3 replay.py single_oak-titmouse.wav --lat 37.4 --lon -122.1 --week 23
"""
import argparse, json, os, subprocess, sys, tempfile, urllib.request, wave

def transcode(src, dst):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src,
                    "-ac", "1", "-ar", "48000", "-sample_fmt", "s16", dst], check=True)

def analyze(url, path, min_conf, lat, lon, week):
    body = {"path": path, "min_conf": min_conf}
    if lat is not None and lon is not None:
        body.update(lat=lat, lon=lon)
        if week: body["week"] = week
    req = urllib.request.Request(url + "/analyze", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("detections", [])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--analyzer", default="http://127.0.0.1:8012")
    ap.add_argument("--min-conf", type=float, default=0.5)
    ap.add_argument("--lat", type=float); ap.add_argument("--lon", type=float)
    ap.add_argument("--week", type=int)
    ap.add_argument("--window", type=float, default=3.0)
    ap.add_argument("--step", type=float, default=1.0)
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "full.wav")
        transcode(a.clip, wav)
        with wave.open(wav, "rb") as w:
            sr, n, total = w.getframerate(), w.getnframes(), 0
            frames = w.readframes(n)
        sampwidth, dur = 2, n / sr
        win, step = int(a.window * sr), int(a.step * sr)
        print(f"{os.path.basename(a.clip)}  {dur:.1f}s @ {sr}Hz  "
              f"(window {a.window}s / step {a.step}s)\n")

        series = {}  # sci -> [(t, conf)]
        t = 0
        while t + win <= n or (t == 0 and n < win):
            seg = frames[t * sampwidth: (t + win) * sampwidth]
            segpath = os.path.join(tmp, "seg.wav")
            with wave.open(segpath, "wb") as o:
                o.setnchannels(1); o.setsampwidth(sampwidth); o.setframerate(sr)
                o.writeframes(seg)
            dets = analyze(a.analyzer, segpath, a.min_conf, a.lat, a.lon, a.week)
            ts = t / sr
            if dets:
                tag = "  ".join(f"{d['common_name']}({d['confidence']:.2f}"
                                + (f",occ{d['occurrence']:.2f}" if d.get('occurrence') is not None else "")
                                + (",OUT" if d.get('in_range') is False else "") + ")"
                                for d in sorted(dets, key=lambda d: -d['confidence']))
                print(f"  t={ts:5.1f}s  {tag}")
            for d in dets:
                series.setdefault(d['scientific_name'], []).append((ts, d['confidence']))
            total += 1
            t += step

        print(f"\nWindows: {total}.  Per-species (the localizer's input series):")
        for sci, pts in sorted(series.items(), key=lambda kv: -max(c for _, c in kv[1])):
            ts = [p[0] for p in pts]
            print(f"  {sci:32s} windows={len(pts):2d}  peak={max(c for _,c in pts):.2f}  "
                  f"t={ts[0]:.0f}-{ts[-1]:.0f}s")

if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as e:
        sys.exit(f"missing dependency or file: {e}")
    except urllib.error.URLError as e:
        sys.exit(f"analyzer unreachable at the --analyzer URL ({e}); is it running on :8012?")
