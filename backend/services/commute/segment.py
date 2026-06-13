"""Trip + leg segmentation from raw location pings.

ZERO hardcoding of the user's commute: this derives, from the phone's location
stream alone, where the user dwells (stays) and how they move between dwells
(moves), and splits each move into mode-homogeneous legs (still / walk / wheels)
by speed signature. "wheels" (any fast ground travel) is left coarse here;
distinguishing drive vs rail vs bus is done later by transit_match against the
live vehicle feed — again, learned, never configured.

The algorithm is a classic stay-point detector (Li et al. 2008) followed by a
speed-class run-length segmentation with hysteresis + small-segment merging.
Heuristic + explainable on purpose — no ML, no training data, no per-user config.

Outputs plain dicts (JSON-serialisable) so journeys.py can persist them.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .geo import Point, centroid, fill_speeds, haversine_m, to_points

# --- tunables (general human-mobility constants, NOT user-specific) ----------
STAY_RADIUS_M = 80.0        # within this of a rolling centroid = "here"
STAY_MIN_S = 240.0          # dwell >= 4 min to count as a stay (a place visit)
WALK_MAX_MPS = 2.3          # ~8.3 km/h — brisk walk ceiling
STILL_MAX_MPS = 0.4         # below this you're standing
MIN_SEG_S = 45.0            # legs shorter than this get merged into a neighbour
SMOOTH_WIN = 3              # median window (points) for speed smoothing
BIG_GAP_S = 600.0           # a >10 min gap between pings is a coverage hole


def _smooth_speeds(points: list[Point]) -> list[float]:
    """Rolling median of per-point speed to kill GPS spikes."""
    sp = [p.speed if p.speed is not None else 0.0 for p in points]
    n = len(sp)
    if n <= SMOOTH_WIN:
        return sp
    out = []
    half = SMOOTH_WIN // 2
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        window = sorted(sp[lo:hi])
        out.append(window[len(window) // 2])
    return out


def _speed_class(mps: float) -> str:
    if mps < STILL_MAX_MPS:
        return "still"
    if mps < WALK_MAX_MPS:
        return "walk"
    return "wheels"


def detect_stays(points: list[Point]) -> list[dict[str, Any]]:
    """Return stays as dicts: {start_idx,end_idx,start_ts,end_ts,lat,lon,
    dwell_s,n}. A stay is a maximal run staying within STAY_RADIUS_M of its
    anchor for at least STAY_MIN_S."""
    stays: list[dict[str, Any]] = []
    n = len(points)
    i = 0
    while i < n:
        j = i + 1
        while j < n:
            d = haversine_m(points[i].lat, points[i].lon, points[j].lat, points[j].lon)
            if d > STAY_RADIUS_M:
                break
            j += 1
        dwell = (points[j - 1].ts - points[i].ts).total_seconds()
        if dwell >= STAY_MIN_S and (j - i) >= 2:
            grp = points[i:j]
            clat, clon = centroid(grp)
            stays.append({
                "start_idx": i, "end_idx": j - 1,
                "start_ts": points[i].ts, "end_ts": points[j - 1].ts,
                "lat": clat, "lon": clon, "dwell_s": dwell, "n": j - i,
            })
            i = j
        else:
            i += 1
    return stays


def _segment_move(points: list[Point], smooth: list[float],
                  lo: int, hi: int) -> list[dict[str, Any]]:
    """Split a move (points[lo:hi+1]) into mode-homogeneous legs by speed class,
    with small-segment merging so a one-ping blip doesn't spawn a leg."""
    if hi <= lo:
        return []
    classes = [_speed_class(smooth[k]) for k in range(lo, hi + 1)]
    # Run-length encode into raw segments.
    segs: list[dict[str, Any]] = []
    run_start = lo
    for k in range(lo + 1, hi + 1):
        if classes[k - lo] != classes[run_start - lo]:
            segs.append({"cls": classes[run_start - lo], "lo": run_start, "hi": k - 1})
            run_start = k
    segs.append({"cls": classes[run_start - lo], "lo": run_start, "hi": hi})

    # Merge short runs into the neighbour with the more "significant" class
    # (wheels > walk > still), so transient noise doesn't fragment a leg.
    def dur(s: dict[str, Any]) -> float:
        return (points[s["hi"]].ts - points[s["lo"]].ts).total_seconds()

    rank = {"still": 0, "walk": 1, "wheels": 2}
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for idx, s in enumerate(segs):
            if dur(s) >= MIN_SEG_S:
                continue
            # merge into the higher-ranked adjacent neighbour
            left = segs[idx - 1] if idx > 0 else None
            right = segs[idx + 1] if idx + 1 < len(segs) else None
            target = None
            if left and right:
                target = left if rank[left["cls"]] >= rank[right["cls"]] else right
            else:
                target = left or right
            if target is None:
                continue
            target["lo"] = min(target["lo"], s["lo"])
            target["hi"] = max(target["hi"], s["hi"])
            segs.pop(idx)
            changed = True
            break

    # Coalesce now-adjacent same-class segments.
    coalesced: list[dict[str, Any]] = []
    for s in segs:
        if coalesced and coalesced[-1]["cls"] == s["cls"]:
            coalesced[-1]["hi"] = s["hi"]
        else:
            coalesced.append(dict(s))

    legs: list[dict[str, Any]] = []
    for s in coalesced:
        if s["cls"] == "still":
            continue  # a still stretch inside a move is a brief pause, not a leg
        legs.append(_leg_dict(points, smooth, s["lo"], s["hi"],
                              "walk" if s["cls"] == "walk" else "wheels"))
    return legs


def _leg_dict(points: list[Point], smooth: list[float], lo: int, hi: int,
              mode: str) -> dict[str, Any]:
    seg = points[lo:hi + 1]
    dist = 0.0
    max_gap = 0.0
    for k in range(lo + 1, hi + 1):
        dist += haversine_m(points[k - 1].lat, points[k - 1].lon, points[k].lat, points[k].lon)
        gap = (points[k].ts - points[k - 1].ts).total_seconds()
        max_gap = max(max_gap, gap)
    dur_s = (seg[-1].ts - seg[0].ts).total_seconds()
    sub = smooth[lo:hi + 1]
    return {
        "kind": "move",
        "mode": mode,                       # coarse: walk | wheels (refined later)
        "start_ts": seg[0].ts.isoformat(),
        "end_ts": seg[-1].ts.isoformat(),
        "start_pt": [seg[0].lat, seg[0].lon],
        "end_pt": [seg[-1].lat, seg[-1].lon],
        "dist_m": round(dist, 1),
        "duration_s": round(dur_s, 1),
        "avg_speed_mps": round(dist / dur_s, 2) if dur_s > 0 else 0.0,
        "peak_speed_mps": round(max(sub), 2) if sub else 0.0,
        "n_points": hi - lo + 1,
        "max_gap_s": round(max_gap, 1),
        "path": [[p.lat, p.lon, p.ts.isoformat()] for p in seg],
    }


def segment(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Segment a time-ordered slice of location records into a trip:
    an alternating-ish sequence of stays and move-legs.

    Returns {segments: [...], n_points, span_s, coverage, max_gap_s}.
    `segments` is chronological; stays carry kind="stay", moves kind="move".
    """
    points = to_points(records)
    if len(points) < 2:
        return {"segments": [], "n_points": len(points), "span_s": 0.0,
                "coverage": 0.0, "max_gap_s": 0.0}
    fill_speeds(points)
    smooth = _smooth_speeds(points)
    stays = detect_stays(points)

    segments: list[dict[str, Any]] = []
    cursor = 0
    for st in stays:
        if st["start_idx"] > cursor:
            segments.extend(_segment_move(points, smooth, cursor, st["start_idx"]))
        segments.append({
            "kind": "stay",
            "mode": "stay",
            "start_ts": st["start_ts"].isoformat(),
            "end_ts": st["end_ts"].isoformat(),
            "lat": round(st["lat"], 6),
            "lon": round(st["lon"], 6),
            "dwell_s": round(st["dwell_s"], 1),
            "n_points": st["n"],
        })
        cursor = st["end_idx"]
    if cursor < len(points) - 1:
        segments.extend(_segment_move(points, smooth, cursor, len(points) - 1))

    span_s = (points[-1].ts - points[0].ts).total_seconds()
    max_gap = 0.0
    covered = 0.0
    for k in range(1, len(points)):
        gap = (points[k].ts - points[k - 1].ts).total_seconds()
        max_gap = max(max_gap, gap)
        covered += min(gap, BIG_GAP_S)
    coverage = round(covered / span_s, 3) if span_s > 0 else 0.0
    return {
        "segments": segments,
        "n_points": len(points),
        "span_s": round(span_s, 1),
        "coverage": coverage,            # fraction of the span with <10min gaps
        "max_gap_s": round(max_gap, 1),
    }
