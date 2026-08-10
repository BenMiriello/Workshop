#!/usr/bin/env python3
"""One-shot backfill: embed historical saved clips so the Atlas has enough data
to build. Only STORED detections ever got embedded going forward; the recordings
table holds 100+ saved clips from before /embed went live. Re-run each through the
analyzer's /embed and store the vector. Idempotent — skips det_ids already embedded.

Run from backend/:  .venv/bin/python scripts/atlas_backfill_embeddings.py
"""
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import paths  # noqa: E402

DB = str(paths.env_path("BIRDNET_BROWSER_DB", "apps", "birds", "browser-stations.db"))
CLIPS_DIR = paths.env_path("BIRDNET_CLIPS_DIR", "apps", "birds", "clips")
ANALYZER_URL = os.environ.get("BIRDNET_ANALYZER_URL", "http://127.0.0.1:8012")
EMBED_MODEL = "birdnet-global-6k-v2.4"


def call_embed(path: str):
    body = json.dumps({"path": path}).encode()
    req = urllib.request.Request(f"{ANALYZER_URL}/embed", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        embs = json.loads(r.read()).get("embeddings", [])
    return embs[0]["vec"] if embs else None


def pack_vec(vec) -> bytes:
    import array
    a = array.array("f", vec)
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def resolve(path: str) -> str | None:
    for root in (CLIPS_DIR, CLIPS_DIR.parent, CLIPS_DIR.parent.parent):
        cand = root / path
        if cand.exists():
            return str(cand)
    if os.path.isabs(path) and os.path.exists(path):
        return path
    return None


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    embedded = {r[0] for r in c.execute("SELECT det_id FROM detection_embedding")}
    rows = c.execute(
        "SELECT r.det_id, r.path, d.scientific_name, d.ts "
        "FROM recordings r JOIN browser_detections d ON d.id = r.det_id "
        "WHERE r.path IS NOT NULL"
    ).fetchall()
    todo = [r for r in rows if r["det_id"] not in embedded]
    print(f"{len(rows)} recordings, {len(embedded)} already embedded, {len(todo)} to backfill")
    ok = fail = 0
    for r in todo:
        ap = resolve(r["path"])
        if not ap:
            print(f"  det {r['det_id']}: audio missing ({r['path']})")
            fail += 1
            continue
        try:
            vec = call_embed(ap)
        except Exception as e:  # noqa: BLE001
            print(f"  det {r['det_id']}: embed error {e}")
            fail += 1
            continue
        if not vec:
            print(f"  det {r['det_id']}: no embedding returned")
            fail += 1
            continue
        c.execute(
            "INSERT OR REPLACE INTO detection_embedding"
            "(det_id,vec,dim,model,cluster_id,call_type,ct_dist,embedded_at) "
            "VALUES(?,?,?,?,NULL,NULL,NULL,?)",
            (r["det_id"], pack_vec(vec), len(vec), EMBED_MODEL, r["ts"]))
        ok += 1
        if ok % 20 == 0:
            c.commit()
            print(f"  ...{ok} embedded")
    c.commit()
    total = c.execute("SELECT count(*) FROM detection_embedding").fetchone()[0]
    print(f"done: {ok} embedded, {fail} failed. detection_embedding total = {total}")
    c.close()


if __name__ == "__main__":
    main()
