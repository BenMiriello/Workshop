"""Match a fast ('wheels') leg to the transit vehicle that ran it.

Given a leg's GPS path + the rolling vehicle-position history (poller.vehicles
.jsonl), find the vehicle trip whose track best coincides with the user's in
space AND time. A good match means the leg was that train/bus → we learn the
agency, route, trip, and boarding/alighting stop. No match → it was a private
drive. Nothing here is configured; the route/agency is read off the matched feed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .geo import haversine_m, parse_ts

MATCH_RADIUS_M = 500.0     # user within this of the vehicle counts as "aboard"
MAX_TIME_SKEW_S = 200.0    # vehicle fix must be within this of the sample time
MIN_SCORE = 0.5            # >=50% of samples aboard => matched
MAX_SAMPLES = 30           # cap path sampling for cost


def _row_time(r: dict[str, Any]) -> float | None:
    v = r.get("vts")
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    ts = parse_ts(r.get("poll_ts"))
    return ts.timestamp() if ts else None


def _trip_tracks(vehicle_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group vehicle rows by trip_id into time-sorted tracks."""
    tracks: dict[str, dict[str, Any]] = {}
    for r in vehicle_rows:
        tid = r.get("trip_id")
        t = _row_time(r)
        if not tid or t is None or r.get("lat") is None or r.get("lon") is None:
            continue
        tr = tracks.setdefault(tid, {"trip_id": tid, "route_id": r.get("route_id"),
                                     "agency": r.get("agency"), "rows": []})
        tr["rows"].append((t, float(r["lat"]), float(r["lon"]), r.get("stop_id")))
    for tr in tracks.values():
        tr["rows"].sort(key=lambda x: x[0])
    return tracks


def _pos_at(track_rows: list[tuple], t: float) -> tuple[float, float, Any] | None:
    """Vehicle position nearest in time to t, within MAX_TIME_SKEW_S."""
    best = None
    best_dt = MAX_TIME_SKEW_S
    for (vt, la, lo, stop) in track_rows:
        dt = abs(vt - t)
        if dt < best_dt:
            best, best_dt = (la, lo, stop), dt
    return best


def match_leg(leg: dict[str, Any], vehicle_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the best transit match for a leg, or {"matched": False}."""
    path = leg.get("path") or []
    if len(path) < 2 or not vehicle_rows:
        return {"matched": False, "reason": "no_path_or_history"}
    # Sample the path (downsample to MAX_SAMPLES).
    stride = max(1, len(path) // MAX_SAMPLES)
    samples: list[tuple[float, float, float]] = []
    for p in path[::stride]:
        ts = parse_ts(p[2])
        if ts:
            samples.append((ts.timestamp(), p[0], p[1]))
    if not samples:
        return {"matched": False, "reason": "no_timed_samples"}

    tracks = _trip_tracks(vehicle_rows)
    best_trip = None
    best_score = 0.0
    best_board = best_alight = None
    for tr in tracks.values():
        hits = 0
        board = alight = None
        for (t, la, lo) in samples:
            pos = _pos_at(tr["rows"], t)
            if pos is None:
                continue
            if haversine_m(la, lo, pos[0], pos[1]) <= MATCH_RADIUS_M:
                hits += 1
                if board is None:
                    board = pos[2]
                alight = pos[2]
        score = hits / len(samples)
        if score > best_score:
            best_score, best_trip = score, tr
            best_board, best_alight = board, alight
    if best_trip is None or best_score < MIN_SCORE:
        return {"matched": False, "best_score": round(best_score, 2),
                "reason": "below_threshold"}
    return {
        "matched": True,
        "trip_id": best_trip["trip_id"],
        "route_id": best_trip["route_id"],
        "agency": best_trip["agency"],
        "score": round(best_score, 2),
        "boarding_stop_id": best_board,
        "alighting_stop_id": best_alight,
    }


def classify_wheels(leg: dict[str, Any], vehicle_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Refine a coarse 'wheels' leg into a concrete mode using the match. Returns
    the leg dict with mode set to 'rail'|'transit'|'drive' + a `match` block."""
    out = dict(leg)
    m = match_leg(leg, vehicle_rows)
    out["match"] = m
    if m.get("matched"):
        agency = m.get("agency")
        # caltrain = heavy rail; sfmta = light rail / bus ("transit")
        out["mode"] = "rail" if agency == "caltrain" else "transit"
        out["route_id"] = m.get("route_id")
        out["agency"] = agency
    else:
        out["mode"] = "drive"
    return out
