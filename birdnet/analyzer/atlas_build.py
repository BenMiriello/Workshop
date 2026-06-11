#!/usr/bin/env python3
"""Soundscape Atlas — batch cluster build (runs in the analyzer venv).

Reads the 1024-d BirdNET embeddings the backend captured for every stored
detection, forms acoustic clusters, and writes them back for the Atlas UI:

  - PER-SPECIES clusters (scope='species'): one UMAP+HDBSCAN run per species with
    enough detections. These are the CALL-TYPE groups — within one species the
    embedding separates song / contact / alarm / flight / begging. Each cluster
    gets a centroid (mean embedding) used for live nearest-centroid tagging and
    for carrying a hand-label forward across rebuilds.
  - A GLOBAL map (scope='global'): one UMAP of everything, for the fly-through
    overview coloured by species; HDBSCAN gives blobs (new blob = new sound).

It also writes the 2D viz coordinates (atlas_point), refreshes each detection's
authoritative call_type (detection_embedding), prunes the independent exemplar
clip pool down to the N clips nearest each cluster centroid, and bumps
atlas_version so the live backend reloads its centroid cache.

This is heavy ML (UMAP/HDBSCAN), kept OUT of the live backend venv on purpose —
same split as the analyzer service. Compute happens in memory; the DB is touched
only for a short read and a short write, so it coexists with live writes
(busy_timeout handles brief contention).

Run:  .venv/bin/python atlas_build.py [--db PATH] [--min-species-n N] [--dry-run]
"""
from __future__ import annotations

import argparse
import array
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone

import numpy as np

BROWSER_DB = os.environ.get("BIRDNET_BROWSER_DB",
                            "/home/clawd-a/birdnet/data/browser-stations.db")
ATLAS_CLIPS_DIR = os.environ.get("BIRDNET_ATLAS_CLIPS_DIR",
                                 "/home/clawd-a/birdnet/data/atlas-clips")

# A species needs at least this many embedded detections before we try to split
# it into call types — fewer than this and clusters are noise, so it stays one
# (unlabelled) group.
MIN_SPECIES_N = 20
# When matching a freshly-built cluster to a previous one to carry its hand-label
# forward: accept if the centroids are within this cosine distance.
LABEL_CARRY_DIST = 0.12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unpack(blob: bytes) -> np.ndarray:
    a = array.array("f")
    a.frombytes(blob)
    if sys.byteorder == "big":
        a.byteswap()
    return np.array(a, dtype=np.float32)


def _pack(vec: np.ndarray) -> bytes:
    a = array.array("f", [float(x) for x in vec])
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


def _load(c: sqlite3.Connection):
    """Return (ids, vecs Nx1024, species list, det_id->row meta) for bird
    detections that are in-range and not rejected — the Atlas universe."""
    rows = c.execute(
        "SELECT de.det_id, de.vec, bd.scientific_name AS sci, bd.common_name AS common, "
        "bd.location_id AS loc, bd.ts AS ts "
        "FROM detection_embedding de JOIN browser_detections bd ON bd.id=de.det_id "
        "WHERE de.vec IS NOT NULL AND bd.is_bird=1 "
        "AND (bd.in_range IS NULL OR bd.in_range=1) "
        "AND (bd.status IS NULL OR bd.status<>'rejected')").fetchall()
    ids, vecs, species, meta = [], [], [], {}
    for r in rows:
        ids.append(r["det_id"])
        vecs.append(_unpack(r["vec"]))
        species.append(r["sci"])
        meta[r["det_id"]] = {"sci": r["sci"], "common": r["common"],
                             "loc": r["loc"], "ts": r["ts"]}
    X = np.vstack(vecs) if vecs else np.zeros((0, 1024), dtype=np.float32)
    return ids, X, species, meta


def _umap(X: np.ndarray, n_components: int, seed: int = 42):
    """UMAP reduce with cosine metric. n_neighbors/min_dist follow the repertoire
    literature (Sainburg 2020, Thomas 2022). Falls back to PCA-ish identity for
    tiny N where UMAP is undefined."""
    import umap
    n = len(X)
    n_neighbors = max(2, min(15, n - 1))
    reducer = umap.UMAP(n_components=min(n_components, max(1, n - 1)),
                        n_neighbors=n_neighbors, min_dist=0.1,
                        metric="cosine", random_state=seed)
    return reducer.fit_transform(X)


def _hdbscan(Z: np.ndarray, min_cluster_size: int):
    from sklearn.cluster import HDBSCAN
    mcs = max(5, min(min_cluster_size, len(Z)))
    return HDBSCAN(min_cluster_size=mcs, metric="euclidean").fit_predict(Z)


def _old_labels(c: sqlite3.Connection):
    """Previous clusters keyed by (scope, species) -> list of (label, unit
    centroid), so a rebuilt cluster can inherit a hand-label by centroid match."""
    out: dict = {}
    for r in c.execute("SELECT scope, species, label, centroid FROM atlas_cluster "
                       "WHERE centroid IS NOT NULL"):
        # 'unknown' was a legacy system bucket, never a real hand label — don't let
        # it carry forward onto a real cluster. Only genuine labels propagate.
        if not r["label"] or r["label"] == "unknown":
            continue
        out.setdefault((r["scope"], r["species"]), []).append(
            (r["label"], _unit(_unpack(r["centroid"]))))
    return out


def _carry_label(scope, species, centroid_unit, old):
    best, bestd = None, 9.9
    for label, oc in old.get((scope, species), []):
        d = 1.0 - float(np.dot(centroid_unit, oc))
        if d < bestd:
            best, bestd = label, d
    return best if bestd <= LABEL_CARRY_DIST else None


def build(db: str, min_species_n: int = MIN_SPECIES_N, dry_run: bool = False) -> dict:
    c = sqlite3.connect(db, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=15000")
    ids, X, species, meta = _load(c)
    n = len(ids)
    if n < min_species_n:
        return {"ok": False, "reason": f"only {n} embedded detections; need >= {min_species_n}"}

    old = _old_labels(c)
    # cluster_id is assigned as we go; new atlas_cluster rows are collected then
    # inserted in one transaction. We map a temp local index -> final row payload.
    clusters: list[dict] = []          # {scope,species,hdbscan_label,label,size,centroid,members:[det_id]}
    points: list[tuple] = []           # (det_id, scope, x, y, cluster_local_idx)
    det_tag: dict = {}                 # det_id -> cluster_local_idx (species scope, authoritative)

    def add_cluster(scope, sp, hlabel, member_ids, member_vecs, xy):
        centroid = np.mean(member_vecs, axis=0)
        cu = _unit(centroid)
        label = _carry_label(scope, sp, cu, old)
        idx = len(clusters)
        clusters.append({"scope": scope, "species": sp, "hdbscan_label": int(hlabel),
                         "label": label, "size": len(member_ids),
                         "centroid": centroid, "members": member_ids})
        for did, (x, y) in zip(member_ids, xy):
            points.append((did, scope, float(x), float(y), idx))
            if scope == "species":
                det_tag[did] = idx
        return idx

    # ---- GLOBAL overview map -------------------------------------------------
    Zg = _umap(X, 2, seed=42)
    gl = _hdbscan(_umap(X, 5, seed=7), min_cluster_size=max(8, n // 25))
    for lab in sorted(set(int(l) for l in gl)):
        sel = [i for i in range(n) if int(gl[i]) == lab]
        # noise (-1) still gets points (so the map is complete) but no centroid row
        if lab == -1:
            for i in sel:
                points.append((ids[i], "global", float(Zg[i][0]), float(Zg[i][1]), None))
            continue
        add_cluster("global", None, lab, [ids[i] for i in sel],
                    [X[i] for i in sel], [Zg[i] for i in sel])

    # ---- PER-SPECIES call-type clusters -------------------------------------
    by_species: dict = {}
    for i, sp in enumerate(species):
        by_species.setdefault(sp, []).append(i)
    species_built = 0
    for sp, idxs in by_species.items():
        if len(idxs) < min_species_n:
            continue
        species_built += 1
        Xs = X[idxs]
        Zs = _umap(Xs, 2, seed=42)
        lab = _hdbscan(_umap(Xs, min(5, len(idxs) - 1), seed=7),
                       min_cluster_size=max(5, len(idxs) // 15))
        for cl in sorted(set(int(l) for l in lab)):
            sub = [j for j, L in enumerate(lab) if int(L) == cl]
            member_ids = [ids[idxs[j]] for j in sub]
            member_vecs = [Xs[j] for j in sub]
            xy = [Zs[j] for j in sub]
            if cl == -1:
                # Acoustic outliers within the species — NOT a call type. Keep the
                # points on the map but UNGROUPED (cluster_id NULL, no atlas_cluster
                # row), so the panel never shows a meaningless "unknown" bucket with
                # every detection dumped under it. They render neutral; a real
                # call-type cluster is the only thing that becomes labellable.
                for j in sub:
                    points.append((ids[idxs[j]], "species", float(Zs[j][0]), float(Zs[j][1]), None))
                continue
            add_cluster("species", sp, cl, member_ids, member_vecs, xy)

    summary = {"ok": True, "detections": n, "species_clustered": species_built,
               "clusters": len([x for x in clusters if x["scope"] == "species"]),
               "global_clusters": len([x for x in clusters if x["scope"] == "global"])}
    if dry_run:
        c.close()
        return summary

    _write(c, clusters, points, det_tag, meta)
    c.close()
    return summary


def _write(c, clusters, points, det_tag, meta):
    now = _now()
    # local idx -> final cluster_id, filled as we insert.
    cur = c.execute("BEGIN")
    c.execute("DELETE FROM atlas_point")
    c.execute("DELETE FROM atlas_cluster")
    idmap: dict = {}
    for idx, cl in enumerate(clusters):
        r = c.execute(
            "INSERT INTO atlas_cluster(scope,species,hdbscan_label,label,size,centroid,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (cl["scope"], cl["species"], cl["hdbscan_label"], cl["label"], cl["size"],
             _pack(cl["centroid"]), now, now))
        idmap[idx] = r.lastrowid
    c.executemany(
        "INSERT INTO atlas_point(det_id,scope,x,y,z,cluster_id) VALUES(?,?,?,?,?,?)",
        [(did, scope, x, y, None, (idmap[ci] if ci is not None else None))
         for (did, scope, x, y, ci) in points])

    # Authoritative call_type per detection from its species cluster.
    label_of = {idx: clusters[idx]["label"] for idx in range(len(clusters))}
    for did, ci in det_tag.items():
        c.execute("UPDATE detection_embedding SET cluster_id=?, call_type=? WHERE det_id=?",
                  (idmap[ci], label_of[ci], did))

    _prune_clips(c, clusters, idmap, det_tag, meta)

    ver = c.execute("SELECT value FROM app_settings WHERE key='atlas_version'").fetchone()
    nextv = (int(ver["value"]) + 1) if ver and (ver["value"] or "").isdigit() else 1
    summary = {"detections": sum(1 for p in points if p[1] == "species"),
               "species_clusters": sum(1 for cl in clusters if cl["scope"] == "species"),
               "global_clusters": sum(1 for cl in clusters if cl["scope"] == "global")}
    for k, v in (("atlas_version", str(nextv)),
                 ("atlas_last_build", now),
                 ("atlas_last_summary", json.dumps(summary))):
        c.execute("INSERT INTO app_settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    c.commit()


def _prune_clips(c, clusters, idmap, det_tag, meta):
    """Keep the exemplars-per-cluster clips nearest each species centroid; delete
    the rest of the clips that belonged to a clustered species. Clips whose
    detection wasn't clustered are left for a later build / FIFO budget."""
    ex_n = 8
    row = c.execute("SELECT value FROM app_settings WHERE key='atlas_exemplars_per_cluster'").fetchone()
    if row and (row["value"] or "").isdigit():
        ex_n = max(1, int(row["value"]))

    have = {r["det_id"]: r for r in c.execute(
        "SELECT id, det_id, path FROM atlas_clip")}
    # group clip-bearing detections by their species cluster, ranked by centroid distance
    by_cluster: dict = {}
    centroids = {idx: _unit(clusters[idx]["centroid"]) for idx in range(len(clusters))}
    # need each detection's embedding distance; reload vecs for clip-bearing dets
    for did in have:
        ci = det_tag.get(did)
        if ci is None:
            continue
        rv = c.execute("SELECT vec FROM detection_embedding WHERE det_id=?", (did,)).fetchone()
        if not rv or rv["vec"] is None:
            continue
        d = 1.0 - float(np.dot(_unit(_unpack(rv["vec"])), centroids[ci]))
        by_cluster.setdefault(ci, []).append((d, did))

    keep_ids: set = set()
    for ci, lst in by_cluster.items():
        lst.sort(key=lambda t: t[0])
        for _, did in lst[:ex_n]:
            clip = have[did]
            keep_ids.add(clip["id"])
            c.execute("UPDATE atlas_clip SET exemplar=1, cluster_id=? WHERE id=?",
                      (idmap[ci], clip["id"]))
    # delete the losers among clustered species (their only job was to seed exemplars)
    clustered_dets = set(det_tag.keys())
    for did, clip in have.items():
        if did in clustered_dets and clip["id"] not in keep_ids:
            try:
                os.unlink(os.path.join(ATLAS_CLIPS_DIR, clip["path"]))
            except OSError:
                pass
            c.execute("DELETE FROM atlas_clip WHERE id=?", (clip["id"],))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=BROWSER_DB)
    ap.add_argument("--min-species-n", type=int, default=MIN_SPECIES_N)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    out = build(a.db, a.min_species_n, a.dry_run)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
