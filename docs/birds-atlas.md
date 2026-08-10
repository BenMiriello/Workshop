# Soundscape Atlas — call-type clustering (design + ops)

The Atlas turns the browser-path detections into a learned acoustic map and uses
it to assign a **call type** (song / contact / alarm / flight / begging / other /
unknown) to each detection. This is the call-type-classification work stream; it
shares the charting layer with phenology and feeds predator/mobbing detection
(phase 2).

## Why it works

BirdNET classifies **species**, not call type. But its penultimate-layer
**embedding** (1024-d) encodes acoustic *similarity*, so within one species the
embedding separates song vs call vs drumming into distinct clusters — and even
surfaces novel sound classes the model was never trained on. So the route to
call type is: clip → embedding → cluster → **label each cluster once by hand** →
auto-tag new detections by nearest cluster. (Sources: Sci. of Total Env.
S1574954123000249; Frontiers Ecol. Evol. 2024.1409407; OpenSoundScape.)

## Pipeline

```
detection (stored) ──► analyzer /embed ──► detection_embedding.vec (the fingerprint)
                                              │
              live nearest-centroid tag ◄─────┤  (between builds: call_type by nearest cluster,
                                              │   'unknown' beyond a cosine threshold)
   nightly atlas_build.py (analyzer venv):    ▼
     per-species UMAP+HDBSCAN → call-type clusters (centroids, viz coords)
     global UMAP → species-coloured overview
     carries hand-labels forward by centroid match
     prunes the exemplar clip pool to N-nearest-centroid per cluster
     bumps atlas_version → live backend reloads centroids
```

### Where the audio comes from
The browser path keeps **no audio** by default, so the embedding is captured at
detection time (while the chunk WAV is still in hand) and is the durable record —
we keep the fingerprint, not the audio. To *name* a cluster by ear you need to
hear it, so there is an **independent exemplar-clip pool** (`atlas_clip`, its own
toggle `atlas_clip_save`, its own small budget) that keeps a few clips per
cluster. It runs alongside or instead of the per-species recordings system. The
labelling UI plays exemplars from this pool ∪ any clips the recordings system
already saved.

## Components

| Piece | File |
|---|---|
| `/embed` endpoint (shared preserve-all-tensors interpreter) | `apps/birds/analyzer/service.py` |
| Embedding capture + live tag + clip pool | `backend/services/birds_analyze.py` |
| Batch cluster build (heavy ML, analyzer venv) | `apps/birds/analyzer/atlas_build.py` |
| Serving routes (`/birds/atlas*`) | `backend/routes/birds.py` |
| Labelling UI (`/apps/birds/atlas`) | `frontend/src/pages/apps/birds/Atlas.tsx` |

### Tables (in the browser DB)
- `detection_embedding(det_id, vec BLOB, dim, model, cluster_id, call_type, ct_dist)`
- `atlas_cluster(id, scope, species, hdbscan_label, label, size, centroid BLOB, ...)`
- `atlas_point(det_id, scope, x, y, z, cluster_id)` — viz coords
- `atlas_clip(id, det_id, scientific_name, cluster_id, ts, path, bytes, exemplar)`

## Key decisions
- **Embedding interpreter**: BirdNET's TFLite frees the penultimate tensor, so
  embeddings need `experimental_preserve_all_tensors=True`. Cost on the classify
  path is ~4ms, so one shared interpreter serves `/analyze` + `/embed` (no second
  model load). Warm embed ≈ 30ms; only stored detections are embedded.
- **2D first** (the informative map is the embedding space, not 3D-for-3D); 3D
  fly-through is a later eye-candy add.
- **Per-species clusters** are the call-type mechanism; the **global** map is the
  overview (new blob = new sound).
- **Nightly rebuild + nearest-centroid live tagging**, not online clustering.
  Labels carry forward across rebuilds by centroid match.
- **'unknown' bucket** = beyond a cosine distance from every labelled centroid —
  the fuzzy-boundary handling.
- Heavy ML (UMAP/HDBSCAN) stays in the analyzer venv, never the lean backend venv.

## Ops

Activate after deploying new code (the running services hold old code until
restarted):

```
# analyzer — exposes /embed (the watchdog's analyzer-restart pkill is broken, so
# start it by hand; ~14s model load)
pkill -9 -f "[s]ervice.py"
cd ~/workshop/birdnet/analyzer && setsid nohup .venv/bin/python service.py >/tmp/birdnet-analyzer.log 2>&1 & disown

# backend — exposes /birds/atlas* + activates embedding capture. Killing it lets
# the running watchdog restart it with the preserved service env.
pkill -f 'uvicorn main:app.*8000'
```

Then build the first Atlas (also reachable as the **Rebuild** button on the page,
or wire a nightly cron):

```
cd ~/workshop/birdnet/analyzer && .venv/bin/python atlas_build.py
```

`atlas_build.py` needs `umap-learn` (installed in the analyzer venv); HDBSCAN
comes from sklearn. It is safe to run against the live DB while detections flow —
it computes in memory and writes in one short transaction.
