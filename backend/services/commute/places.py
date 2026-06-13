"""Learn the user's significant PLACES from location history — no coordinates
ever hardcoded.

We detect stays across all history (segment.detect_stays), cluster nearby stays
into places, and label them by WHEN the user is there: the place with the most
night dwell is home; the most weekday-daytime dwell (that isn't home) is work.
Everything else is an unlabelled recurring place the user can rename.

Persisted to .runtime/commute/places.json. User renames survive re-learning
(matched to the nearest prior place). The commute scorer reads home/work coords
from here, so the "already at destination" logic works WITHOUT the operator ever
pasting coordinates into Settings.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from . import config, timefmt
from .geo import Point, haversine_m, to_points
from .segment import detect_stays

PLACE_RADIUS_M = 130.0       # stays within this of a place centroid = same place
MIN_VISITS = 2               # a place needs >=2 visits to be "significant"
NIGHT_HOURS = set(range(22, 24)) | set(range(0, 6))   # 10pm–6am local
WORK_HOURS = set(range(9, 18))                         # 9am–6pm local


def _cluster_stays(stays: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Greedy spatial clustering of stay centroids into places."""
    clusters: list[dict[str, Any]] = []
    for st in stays:
        best = None
        best_d = PLACE_RADIUS_M
        for c in clusters:
            d = haversine_m(st["lat"], st["lon"], c["lat"], c["lon"])
            if d < best_d:
                best, best_d = c, d
        if best is None:
            clusters.append({
                "lat": st["lat"], "lon": st["lon"],
                "dwell_s": st["dwell_s"], "visits": 1,
                "night_s": 0.0, "work_s": 0.0,
                "first_ts": st["start_ts"], "last_ts": st["end_ts"],
                "_stays": [st],
            })
        else:
            # dwell-weighted centroid update
            w0, w1 = best["dwell_s"], st["dwell_s"]
            tot = w0 + w1 or 1.0
            best["lat"] = (best["lat"] * w0 + st["lat"] * w1) / tot
            best["lon"] = (best["lon"] * w0 + st["lon"] * w1) / tot
            best["dwell_s"] += st["dwell_s"]
            best["visits"] += 1
            best["last_ts"] = max(best["last_ts"], st["end_ts"])
            best["first_ts"] = min(best["first_ts"], st["start_ts"])
            best["_stays"].append(st)
    # Attribute dwell to night/work buckets by the LOCAL hour at the stay midpoint.
    for c in clusters:
        for st in c["_stays"]:
            mid = st["start_ts"] + (st["end_ts"] - st["start_ts"]) / 2
            local = timefmt.local(mid)
            hr = local.hour
            if hr in NIGHT_HOURS:
                c["night_s"] += st["dwell_s"]
            if hr in WORK_HOURS and local.weekday() < 5:
                c["work_s"] += st["dwell_s"]
        c.pop("_stays", None)
    return clusters


def _label(clusters: list[dict[str, Any]]) -> None:
    """Assign auto_label home/work to the best candidates; rest None."""
    for c in clusters:
        c["auto_label"] = None
    significant = [c for c in clusters if c["visits"] >= MIN_VISITS]
    if not significant:
        significant = clusters
    if significant:
        home = max(significant, key=lambda c: c["night_s"])
        if home["night_s"] > 0 or len(significant) == 1:
            home["auto_label"] = "home"
        rest = [c for c in significant if c is not home]
        if rest:
            work = max(rest, key=lambda c: c["work_s"])
            if work["work_s"] > 0:
                work["auto_label"] = "work"


def _merge_user_labels(new: list[dict[str, Any]], prior: list[dict[str, Any]]) -> None:
    """Carry user_label + stable id from the nearest prior place (<PLACE_RADIUS_M)."""
    used: set[int] = set()
    next_id = 1 + max((p.get("id", 0) for p in prior), default=0)
    for c in new:
        match = None
        best_d = PLACE_RADIUS_M
        for i, p in enumerate(prior):
            if i in used:
                continue
            d = haversine_m(c["lat"], c["lon"], p["lat"], p["lon"])
            if d < best_d:
                match, best_d, match_i = p, d, i
        if match is not None:
            used.add(match_i)
            c["id"] = match["id"]
            c["user_label"] = match.get("user_label")
        else:
            c["id"] = next_id
            next_id += 1
            c["user_label"] = None


def learn(max_records: int = 20000) -> dict[str, Any]:
    """Re-derive places from location history; persist + return places.json."""
    from .. import location as location_svc
    records = location_svc.recent_records(max_records)
    points = to_points(records)
    stays = detect_stays(points)
    clusters = _cluster_stays(stays)
    _label(clusters)
    prior = load().get("places", [])
    _merge_user_labels(clusters, prior)
    # Shape + sort by total dwell desc.
    places = []
    for c in sorted(clusters, key=lambda c: c["dwell_s"], reverse=True):
        places.append({
            "id": c["id"],
            "lat": round(c["lat"], 6),
            "lon": round(c["lon"], 6),
            "radius_m": PLACE_RADIUS_M,
            "auto_label": c["auto_label"],
            "user_label": c.get("user_label"),
            "label": c.get("user_label") or c["auto_label"] or f"Place {c['id']}",
            "visits": c["visits"],
            "dwell_min": round(c["dwell_s"] / 60.0, 1),
            "night_min": round(c["night_s"] / 60.0, 1),
            "work_min": round(c["work_s"] / 60.0, 1),
            "first_ts": c["first_ts"].isoformat() if isinstance(c["first_ts"], datetime) else c["first_ts"],
            "last_ts": c["last_ts"].isoformat() if isinstance(c["last_ts"], datetime) else c["last_ts"],
        })
    doc = {"updated": _now_iso(), "places": places}
    _save(doc)
    return doc


def _now_iso() -> str:
    from datetime import timezone
    return datetime.now(timezone.utc).isoformat()


def _path():
    return config.runtime_path("places.json")


def load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {"places": []}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"places": []}


def _save(doc: dict[str, Any]) -> None:
    p = _path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2))
    tmp.replace(p)


def set_label(place_id: int, label: str) -> dict[str, Any]:
    """Operator rename. Persists user_label; recomputes display label."""
    doc = load()
    for pl in doc.get("places", []):
        if pl.get("id") == place_id:
            pl["user_label"] = label.strip() or None
            pl["label"] = pl["user_label"] or pl.get("auto_label") or f"Place {place_id}"
    _save(doc)
    return doc


def endpoint_coords(name: str) -> tuple[float, float] | None:
    """Learned coords for 'home'/'work' (or a user_label match). Falls back to
    schedule.json if a coord was manually set there. Returns None if unknown."""
    name = (name or "").lower()
    doc = load()
    for pl in doc.get("places", []):
        labels = {str(pl.get("auto_label") or "").lower(),
                  str(pl.get("user_label") or "").lower()}
        if name in labels:
            return (pl["lat"], pl["lon"])
    # fall back to any manually-set coord in schedule.json
    return config.get_endpoint_coords(name)


def nearest(lat: float, lon: float, max_m: float = PLACE_RADIUS_M) -> dict[str, Any] | None:
    """The learned place within max_m of a point, or None."""
    best, best_d = None, max_m
    for pl in load().get("places", []):
        d = haversine_m(lat, lon, pl["lat"], pl["lon"])
        if d < best_d:
            best, best_d = pl, d
    return best
