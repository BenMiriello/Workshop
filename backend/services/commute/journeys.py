"""Per-commute JOURNEY records — the empirical spine everything learns from.

A journey is one observed home↔work trip, assembled from the day's location
segments, with each fast leg matched to the transit vehicle that ran it, real
transfer waits between legs, the actual vs target arrival, and explicit
data-quality so downstream reasoning knows how much to trust it.

Nothing is configured: origin/destination are matched to LEARNED places, modes
are inferred, trains are matched off the live feed. Persisted append-only to
.runtime/commute/journeys.jsonl, deduped by (date, direction).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from . import config, places, poller, scoring, segment, timefmt, transit_match
from .geo import parse_ts

TIGHT_TRANSFER_S = 120.0     # < 2 min between legs = a tight/blown connection
LONG_WAIT_S = 600.0          # > 10 min waiting = a missed-connection signature
MIN_COVERAGE = 0.6           # below this the journey is marked partial


def _place_label(lat: float, lon: float) -> str:
    pl = places.nearest(lat, lon)
    return pl["label"] if pl else "unknown"


def build_journey(records: list[dict[str, Any]],
                  vehicle_rows: list[dict[str, Any]] | None = None,
                  schedule: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Assemble a journey from a time-ordered slice of location records.
    Returns None if there isn't enough signal (no movement / single stay)."""
    vehicle_rows = vehicle_rows or []
    s = segment.segment(records)
    segs = s["segments"]
    moves = [x for x in segs if x["kind"] == "move"]
    stays = [x for x in segs if x["kind"] == "stay"]
    if not moves:
        return None

    # Refine each wheels leg to drive/rail/transit via the vehicle feed.
    legs: list[dict[str, Any]] = []
    for m in moves:
        leg = transit_match.classify_wheels(m, vehicle_rows) if m["mode"] == "wheels" else dict(m)
        leg["from_place"] = _place_label(*leg["start_pt"])
        leg["to_place"] = _place_label(*leg["end_pt"])
        # drop the heavy path from the persisted leg (keep endpoints)
        leg.pop("path", None)
        legs.append(leg)

    origin = stays[0] if stays else None
    dest = stays[-1] if len(stays) >= 2 else None
    depart_ts = parse_ts(origin["end_ts"]) if origin else parse_ts(moves[0]["start_ts"])
    arrive_ts = parse_ts(dest["start_ts"]) if dest else parse_ts(moves[-1]["end_ts"])

    origin_label = _place_label(origin["lat"], origin["lon"]) if origin else legs[0]["from_place"]
    dest_label = _place_label(dest["lat"], dest["lon"]) if dest else legs[-1]["to_place"]
    direction = None
    if origin_label == "home" and dest_label == "work":
        direction = "to-work"
    elif origin_label == "work" and dest_label == "home":
        direction = "to-home"

    # Transfers: the wait gap between consecutive legs (real, not assumed-zero).
    transfers: list[dict[str, Any]] = []
    for i in range(len(legs) - 1):
        a, b = legs[i], legs[i + 1]
        gap = (parse_ts(b["start_ts"]) - parse_ts(a["end_ts"])).total_seconds()
        involves_transit = a["mode"] in ("rail", "transit") or b["mode"] in ("rail", "transit")
        transfers.append({
            "at_place": a["to_place"],
            "from_mode": a["mode"], "to_mode": b["mode"],
            "wait_s": round(gap, 1),
            "tight": involves_transit and gap < TIGHT_TRANSFER_S,
            "long_wait": involves_transit and gap > LONG_WAIT_S,
        })

    total_min = round((arrive_ts - depart_ts).total_seconds() / 60.0, 1) if (arrive_ts and depart_ts) else None

    # Compare to the schedule target for this direction/day, if we can resolve one.
    target_arrive = late_by = None
    if direction and depart_ts:
        sch = schedule or config.get_schedule_for_day(direction, timefmt.day_key(depart_ts))
        if sch:
            sch_legs = config.get_legs_by_id(sch.get("legs_id")) or []
            _, tgt = scoring.resolve_times(sch, sch_legs, depart_ts)
            target_arrive = tgt.isoformat()
            if arrive_ts:
                late_by = round((arrive_ts - tgt).total_seconds() / 60.0, 1)

    coverage = s["coverage"]
    partial = coverage < MIN_COVERAGE or len(stays) < 2
    dq = {
        "coverage": coverage,
        "max_gap_s": s["max_gap_s"],
        "n_points": s["n_points"],
        "matched_legs": sum(1 for leg in legs if leg.get("match", {}).get("matched")),
        "transit_legs": sum(1 for leg in legs if leg["mode"] in ("rail", "transit")),
    }
    return {
        "date": timefmt.local(depart_ts).strftime("%Y-%m-%d") if depart_ts else None,
        "direction": direction,
        "origin": origin_label,
        "destination": dest_label,
        "depart_ts": depart_ts.isoformat() if depart_ts else None,
        "depart_local": timefmt.local_clock(depart_ts) if depart_ts else None,
        "arrive_ts": arrive_ts.isoformat() if arrive_ts else None,
        "arrive_local": timefmt.local_clock(arrive_ts) if arrive_ts else None,
        "total_min": total_min,
        "target_arrive": target_arrive,
        "late_by_min": late_by,
        "legs": [_leg_summary(leg) for leg in legs],
        "transfers": transfers,
        "data_quality": dq,
        "partial": partial,
        "built_ts": datetime.now(timezone.utc).isoformat(),
    }


def _leg_summary(leg: dict[str, Any]) -> dict[str, Any]:
    """Compact, JSON-friendly per-leg record for the journey log."""
    out = {
        "mode": leg["mode"],
        "from": leg.get("from_place"),
        "to": leg.get("to_place"),
        "start_local": timefmt.local_clock(parse_ts(leg["start_ts"])),
        "end_local": timefmt.local_clock(parse_ts(leg["end_ts"])),
        "minutes": round(leg["duration_s"] / 60.0, 1),
        "dist_m": leg.get("dist_m"),
        "avg_speed_mps": leg.get("avg_speed_mps"),
    }
    if leg.get("route_id"):
        out["route_id"] = leg["route_id"]
    if leg.get("match", {}).get("matched"):
        out["trip_id"] = leg["match"].get("trip_id")
        out["match_score"] = leg["match"].get("score")
        out["boarding_stop_id"] = leg["match"].get("boarding_stop_id")
        out["alighting_stop_id"] = leg["match"].get("alighting_stop_id")
    return out


# --- persistence -------------------------------------------------------------
def _path():
    return config.runtime_path("journeys.jsonl")


def all_journeys() -> list[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    out = []
    for ln in p.read_text().splitlines():
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return out


def recent(limit: int = 20) -> list[dict[str, Any]]:
    return list(reversed(all_journeys()))[:limit]


def _append(j: dict[str, Any]) -> None:
    p = _path()
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(j, separators=(",", ":")) + "\n")


def _already_recorded(date: str, direction: str | None) -> bool:
    for j in all_journeys():
        if j.get("date") == date and j.get("direction") == direction and not j.get("partial"):
            return True
    return False


def learned_boarding_stops(window: int = 40) -> list[dict[str, str]]:
    """Distinct (agency, stop_id) the user has actually boarded at, learned from
    recent matched transit legs. Feeds the StopMonitoring poller — so it watches
    HIS stops, never a hardcoded list."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for j in recent(window):
        for leg in j.get("legs", []):
            tid = leg.get("trip_id")
            if not tid or leg.get("mode") not in ("rail", "transit"):
                continue
            # boarding stop id was captured into the match during build
            bs = leg.get("boarding_stop_id")
            agency = "caltrain" if leg.get("mode") == "rail" else "sfmta"
            if bs and (agency, bs) not in seen:
                seen.add((agency, bs))
                out.append({"agency": agency, "stop_id": bs})
    return out


def finalize(now: datetime | None = None, lookback_hours: float = 18.0) -> dict[str, Any]:
    """Build + persist journeys for completed commute windows in the recent past.

    Scans the last `lookback_hours` of location data, builds a journey, and
    appends it if a confident (non-partial) journey for that (date, direction)
    isn't already recorded. Idempotent enough to run from cron each tick.
    """
    from .. import location as location_svc
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(hours=lookback_hours)
    records = location_svc.records_between(start.isoformat(), now.isoformat())
    if len(records) < 4:
        return {"built": False, "reason": "insufficient_records", "n": len(records)}
    vrows = poller.read_vehicle_history(start)
    j = build_journey(records, vrows)
    if not j:
        return {"built": False, "reason": "no_journey"}
    # Only persist CONFIDENT journeys. A partial (coverage < MIN_COVERAGE or < 2
    # stays) is never usable for pattern-learning, and since _already_recorded
    # ignores partials, falling through here re-appended the SAME junk every
    # tick — the journeys log was 24 identical dir=None, 13h rows. Skip it; the
    # cause (sparse pings / overnight-merged segmentation) is the real fix.
    if j.get("partial"):
        return {"built": False, "reason": "partial_skipped",
                "date": j.get("date"), "direction": j.get("direction")}
    if j.get("date") and _already_recorded(j["date"], j.get("direction")):
        return {"built": False, "reason": "already_recorded", "date": j["date"], "direction": j.get("direction")}
    _append(j)
    return {"built": True, "journey": j}
