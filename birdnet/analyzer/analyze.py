#!/usr/bin/env python3
"""BirdNET file analyzer, run in the ISOLATED analyzer venv (heavy ML deps stay
out of the live backend venv). The backend shells out to this.

  analyze.py <wav_path> <out_json> [min_conf]

Writes {"detections": [...]} to out_json. No lat/long/date is passed, so the
range filter is OFF - the browser-mic test tool detects any species and is not
tied to the operator's home location.

Results JSON shape per detection:
  { scientific_name, common_name, confidence, start_s, end_s }
"""
import json
import os
import sys

# Silence TensorFlow's startup chatter so it never pollutes anything.
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")


def main():
    if len(sys.argv) < 3:
        print("usage: analyze.py <wav_path> <out_json> [min_conf]", file=sys.stderr)
        sys.exit(2)
    wav, out = sys.argv[1], sys.argv[2]
    min_conf = float(sys.argv[3]) if len(sys.argv) > 3 else 0.25

    from birdnetlib import Recording
    from birdnetlib.analyzer import Analyzer

    analyzer = Analyzer()
    rec = Recording(analyzer, wav, min_conf=min_conf)
    rec.analyze()

    detections = [
        {
            "scientific_name": d.get("scientific_name"),
            "common_name": d.get("common_name"),
            "confidence": round(float(d.get("confidence", 0.0)), 4),
            "start_s": float(d.get("start_time", 0.0)),
            "end_s": float(d.get("end_time", 0.0)),
        }
        for d in rec.detections
    ]
    with open(out, "w") as f:
        json.dump({"detections": detections}, f)


if __name__ == "__main__":
    main()
