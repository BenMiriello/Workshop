#!/usr/bin/env python3
"""Persistent BirdNET analyzer service (isolated venv). Loads the model ONCE so
each chunk analyzes in well under a second - that is what makes the live
mic-test feed possible. The Workshop backend POSTs to it on 127.0.0.1.

Runs in the analyzer venv (heavy ML deps stay out of the live backend venv).
Stdlib HTTP only (no fastapi) to keep this venv lean. Single-threaded on
purpose: the TFLite model call is serialized.

  POST /analyze   {"path": "/tmp/chunk.wav", "min_conf": 0.25}
                  -> {"detections": [{scientific_name, common_name, confidence, start_s, end_s}]}
  GET  /health    -> {"ok": true, "species": <label count>}

Listens on 127.0.0.1:${BIRDNET_ANALYZER_PORT:-8012}.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

PORT = int(os.environ.get("BIRDNET_ANALYZER_PORT", "8012"))

print("loading BirdNET model...", file=sys.stderr, flush=True)
from birdnetlib import Recording
from birdnetlib.analyzer import Analyzer

ANALYZER = Analyzer()
_LOCK = threading.Lock()
print(f"model ready ({len(ANALYZER.labels)} species); listening on 127.0.0.1:{PORT}",
      file=sys.stderr, flush=True)


def analyze(path: str, min_conf: float):
    # No lat/long/date -> range filter off. The test tool detects any species and
    # is not tied to the operator's home location.
    with _LOCK:
        rec = Recording(ANALYZER, path, min_conf=min_conf)
        rec.analyze()
        return [
            {
                "scientific_name": d.get("scientific_name"),
                "common_name": d.get("common_name"),
                "confidence": round(float(d.get("confidence", 0.0)), 4),
                "start_s": float(d.get("start_time", 0.0)),
                "end_s": float(d.get("end_time", 0.0)),
            }
            for d in rec.detections
        ]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send(200, {"ok": True, "species": len(ANALYZER.labels)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/analyze"):
            self._send(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("content-length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            path = req["path"]
            min_conf = float(req.get("min_conf", 0.25))
            dets = analyze(path, min_conf)
            self._send(200, {"detections": dets})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
