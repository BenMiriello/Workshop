#!/usr/bin/env python3
"""Persistent BirdNET analyzer service (isolated venv). Loads the model ONCE so
each chunk analyzes in well under a second - that is what makes the live
mic-test feed possible. The Workshop backend POSTs to it on 127.0.0.1.

Runs in the analyzer venv (heavy ML deps stay out of the live backend venv).
Stdlib HTTP only (no fastapi) to keep this venv lean. Single-threaded on
purpose: the TFLite model call is serialized.

  POST /analyze   {"path": "/tmp/chunk.wav", "min_conf": 0.5,
                   "lat": 37.36, "lon": -122.1, "week": 23,
                   "sensitivity": 1.0, "sf_thresh": 0.01}
                  -> {"detections": [{scientific_name, common_name, confidence,
                                      start_s, end_s, occurrence, in_range}]}
  GET  /health    -> {"ok": true, "species": <label count>}

When lat+lon are supplied the BirdNET range model is applied: every detection
is flagged in_range (is it plausible here, this week, at sf_thresh?) and tagged
with occurrence (the eBird-derived likelihood 0..1 of the species at this
place/week). The backend uses these to drop impossible species and to keep an
occurrence prior alongside the acoustic confidence. With no lat/lon the filter
is off and occurrence/in_range come back null (caller treats range as unknown).

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

# Rebuild the TFLite interpreter with experimental_preserve_all_tensors so the
# penultimate feature layer survives invoke() and can be read back as the 1024-d
# embedding. Out of the box BirdNET frees that tensor and embedding extraction
# fails ("Tensor data is null"). Measured cost on the classification path is ~4ms
# (24ms -> 28ms), so one shared interpreter serves both /analyze and /embed -
# no second model load, no double memory. Embeddings are bit-identical whether or
# not a classify ran first (verified), because _return_embeddings re-invokes.
from tensorflow import lite as _tflite  # noqa: E402

_interp = _tflite.Interpreter(
    model_path=ANALYZER.model_path, num_threads=1,
    experimental_preserve_all_tensors=True)
_interp.allocate_tensors()
ANALYZER.interpreter = _interp
ANALYZER.input_details = _interp.get_input_details()
ANALYZER.output_details = _interp.get_output_details()
ANALYZER.input_layer_index = ANALYZER.input_details[0]["index"]
ANALYZER.output_layer_index = ANALYZER.output_details[0]["index"]

_LOCK = threading.Lock()

# Occurrence-map cache: the eBird meta-model is cheap to invoke but rebuilding
# the full ~6k-species list every 1s chunk is wasteful. Key on coarse position +
# week so a station re-uses one map all session. Recording's own range-filter
# list is cached separately inside the Analyzer (cached_species_lists).
_OCC_CACHE: dict = {}


def _occurrence_map(lat: float, lon: float, week: int) -> dict:
    """{ 'Sci_Common': occurrence_freq 0..1 } for this place/week, from the
    BirdNET species range (meta) model. Threshold near zero so we keep the real
    frequency for every plausible species (the caller decides cutoffs)."""
    key = (round(lat, 2), round(lon, 2), int(week))
    cached = _OCC_CACHE.get(key)
    if cached is not None:
        return cached
    species = ANALYZER.species_class.return_list(
        lon=lon, lat=lat, week_48=int(week), threshold=0.0001)
    m = {f'{s["scientific_name"]}_{s["common_name"]}': float(s["threshold"])
         for s in species}
    _OCC_CACHE[key] = m
    return m


def analyze(path: str, min_conf: float, lat=None, lon=None, week=None,
            sensitivity: float = 1.0, sf_thresh: float = 0.01):
    with _LOCK:
        use_range = lat is not None and lon is not None
        kwargs = dict(min_conf=min_conf, sensitivity=sensitivity,
                      return_all_detections=True)
        occ = {}
        if use_range:
            kwargs.update(lat=lat, lon=lon, filter_threshold=sf_thresh)
            if week:
                kwargs["week_48"] = int(week)
            occ = _occurrence_map(lat, lon, week or 1)
        rec = Recording(ANALYZER, path, **kwargs)
        rec.analyze()
        out = []
        for d in rec.detections:
            label = f'{d.get("scientific_name")}_{d.get("common_name")}'
            out.append({
                "scientific_name": d.get("scientific_name"),
                "common_name": d.get("common_name"),
                "confidence": round(float(d.get("confidence", 0.0)), 4),
                "start_s": float(d.get("start_time", 0.0)),
                "end_s": float(d.get("end_time", 0.0)),
                # Range model outputs (null when no location was given).
                "occurrence": round(occ.get(label, 0.0), 4) if use_range else None,
                "in_range": bool(d.get("is_predicted_for_location_and_date"))
                if use_range else None,
            })
        return out


def embed(path: str):
    """1024-d BirdNET feature embedding(s) for the chunk(s) in this WAV. Our live
    chunks are 3s so this is normally one vector; a longer file yields one per 3s
    window. This is the penultimate layer - it encodes acoustic similarity, so
    clustering these vectors separates call types WITHIN a species (the Soundscape
    Atlas / call-type route), independent of the species label."""
    with _LOCK:
        rec = Recording(ANALYZER, path)
        rec.extract_embeddings()
        return [
            {"start_s": float(e.get("start_time", 0.0)),
             "end_s": float(e.get("end_time", 0.0)),
             "vec": e["embeddings"]}
            for e in rec.embeddings
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
        elif self.path.startswith("/species"):
            # Occurrence map for a place/week, no audio - used to re-score stored
            # detections against the range model after the fact.
            try:
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                lat = float(q["lat"][0]); lon = float(q["lon"][0])
                week = int(q.get("week", ["1"])[0])
                with _LOCK:
                    m = _occurrence_map(lat, lon, week)
                self._send(200, {"occurrence": m})
            except Exception as e:  # noqa: BLE001
                self._send(500, {"error": str(e)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.startswith("/embed"):
            try:
                n = int(self.headers.get("content-length", 0))
                req = json.loads(self.rfile.read(n) or b"{}")
                vecs = embed(req["path"])
                dim = len(vecs[0]["vec"]) if vecs else 0
                self._send(200, {"embeddings": vecs, "dim": dim})
            except Exception as e:  # noqa: BLE001
                self._send(500, {"error": str(e)})
            return
        if not self.path.startswith("/analyze"):
            self._send(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("content-length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            path = req["path"]
            min_conf = float(req.get("min_conf", 0.5))
            lat = req.get("lat")
            lon = req.get("lon")
            week = req.get("week")
            sensitivity = float(req.get("sensitivity", 1.0))
            sf_thresh = float(req.get("sf_thresh", 0.01))
            dets = analyze(path, min_conf, lat=lat, lon=lon, week=week,
                           sensitivity=sensitivity, sf_thresh=sf_thresh)
            self._send(200, {"detections": dets})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    print(f"model ready ({len(ANALYZER.labels)} species); listening on "
          f"127.0.0.1:{PORT}", file=sys.stderr, flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
