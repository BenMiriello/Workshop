"""Compute the operator-facing commute brief.

Given the current time + the loaded schedule, legs, targets, and the latest
poller state, produce a brief with:
    - which schedule applies right now (weekday-am, weekday-pm, or none)
    - target arrive-by time + flex window
    - current expected leg (based on elapsed time since target_depart)
    - rough ETA to destination given default leg durations + GTFS-RT delays
    - late_risk: bool + reason
    - alerts: any in-effect service alerts relevant to the route

This is INTENTIONALLY conservative — operator's rule is never alert on
EARLY, only LATE. We hold ETA assumptions loose and let the LLM decision
layer in `llm.py` make the final "should I interject" call.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from math import asin, cos, radians, sin, sqrt

from . import config, poller, timefmt
from .. import location as location_svc


# How close (meters) to home/work counts as "at destination" — generous to
# cover building entrance vs GPS scatter. Mountain View row of houses ≈ 50m.
AT_DESTINATION_RADIUS_M = 200.0
# How fresh location data has to be before we trust it for "at destination"
# checks. Older than this and we fall back to time-only logic.
LOCATION_STALE_AFTER_S = 30 * 60


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in meters between two lat/lng points."""
    R = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * R * asin(sqrt(a))


def _location_status(sch: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Compare operator's latest known position to the schedule's destination.

    Returns a dict the brief surfaces directly:
        {"available": False, "reason": ...}                 — no coords / no data / stale
        {"available": True, "at_destination": bool,
         "distance_m": float, "source": str, "age_s": int}

    The caller uses `at_destination` to flip the brief from "on track" to
    "already arrived" so the UI doesn't lie about an in-progress commute
    when the operator's phone says they're already there.
    """
    direction = sch.get("direction")
    dest_name = "work" if direction == "to-work" else "home"
    dest = config.get_endpoint_coords(dest_name)
    if not dest:
        return {"available": False, "reason": f"no coords set for {dest_name} (Settings → Commute)"}
    rec = location_svc.latest()
    if not rec:
        return {"available": False, "reason": "no location pings yet (set up Owntracks)"}
    try:
        ping_at = datetime.fromisoformat(rec["ts"].replace("Z", "+00:00"))
    except Exception:
        return {"available": False, "reason": "location record has no parseable ts"}
    age_s = int((now - ping_at).total_seconds())
    if age_s > LOCATION_STALE_AFTER_S:
        return {"available": False, "reason": f"last ping {age_s // 60}m ago — too stale to trust"}
    try:
        dist = _haversine_m(float(rec["lat"]), float(rec["lng"]), dest[0], dest[1])
    except (KeyError, TypeError, ValueError):
        return {"available": False, "reason": "ping missing lat/lng"}
    return {
        "available": True,
        "at_destination": dist <= AT_DESTINATION_RADIUS_M,
        "distance_m": round(dist, 1),
        "source": rec.get("source", "unknown"),
        "age_s": age_s,
    }


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def active_schedule(now: datetime | None = None) -> dict[str, Any] | None:
    """Return the schedule entry that's "in play" right now.

    AM window: from one hour before target_depart_home until target_arrive_work + 60min flex
    PM window: from 30 min before target_depart_work until target_arrive_home + 60min flex

    Outside both: returns None.
    """
    now = now or _now_utc()
    if not timefmt.is_weekday(now):
        return None
    day = timefmt.day_key(now)
    for direction in ("to-work", "to-home"):
        sch = config.get_schedule_for_day(direction, day)
        if not sch:
            continue
        if direction == "to-work":
            start = timefmt.parse_hhmm_to_local_today(sch["target_depart_home"], now) - timedelta(hours=1)
            end = timefmt.parse_hhmm_to_local_today(sch["target_arrive_work"], now) + timedelta(minutes=60)
        else:
            start = timefmt.parse_hhmm_to_local_today(sch["target_depart_work"], now) - timedelta(minutes=30)
            end = timefmt.parse_hhmm_to_local_today(sch["target_arrive_home"], now) + timedelta(minutes=60)
        if start <= now <= end:
            return sch
    return None


def expected_leg_index(sch: dict[str, Any], legs: list[dict[str, Any]], now: datetime | None = None) -> int:
    """Best guess (0-indexed) of which leg the operator should be on right now,
    based on cumulative default_minutes from target_depart."""
    now = now or _now_utc()
    if sch["direction"] == "to-work":
        depart = timefmt.parse_hhmm_to_local_today(sch["target_depart_home"], now)
    else:
        depart = timefmt.parse_hhmm_to_local_today(sch["target_depart_work"], now)
    elapsed_min = (now - depart).total_seconds() / 60
    if elapsed_min < 0:
        return -1  # before departure
    cumulative = 0
    for i, leg in enumerate(legs):
        cumulative += int(leg.get("default_minutes", 0))
        if elapsed_min <= cumulative:
            return i
    return len(legs) - 1  # past last leg


def projected_arrival(sch: dict[str, Any], legs: list[dict[str, Any]], rt_delay_min: float, now: datetime | None = None) -> datetime:
    """Naive ETA: target_depart + sum(default_minutes) + realtime_delay.

    `rt_delay_min` is the worst-case observed Caltrain/Muni delay along the
    operator's route, in minutes. If positive, adds to ETA.
    """
    now = now or _now_utc()
    if sch["direction"] == "to-work":
        depart = timefmt.parse_hhmm_to_local_today(sch["target_depart_home"], now)
    else:
        depart = timefmt.parse_hhmm_to_local_today(sch["target_depart_work"], now)
    total_min = sum(int(l.get("default_minutes", 0)) for l in legs) + max(0.0, rt_delay_min)
    return depart + timedelta(minutes=total_min)


def realtime_status_summary() -> dict[str, Any]:
    """Honest summary of realtime data availability.

    Returns one of:
        {"available": False, "reason": "needs_511_key", ...}  -- key missing
        {"available": False, "reason": "stale", "age_s": ...} -- last poll too old
        {"available": True, "caltrain_age_s": .., "sfmta_age_s": ..} -- data live

    The brief consumer should refuse to claim "on track" when available=False.
    """
    state = poller.read_state()
    status = state.get("status") or {}
    ct_status = status.get("caltrain.gtfs_rt") or {}
    sf_status = status.get("sfmta.gtfs_rt") or {}
    if ct_status.get("status") == "needs_key" or sf_status.get("status") == "needs_key":
        return {
            "available": False,
            "reason": "needs_511_key",
            "note": "set WORKSHOP_511_API_KEY (free at https://511.org/open-data/token) — until then all transit timing is static schedule only",
        }
    if ct_status.get("status") == "missing_deps":
        return {"available": False, "reason": "missing_deps",
                "note": ct_status.get("note", "")}
    now = _now_utc()
    ages: dict[str, float] = {}
    for label, st in (("caltrain", ct_status), ("sfmta", sf_status)):
        ts = st.get("ts")
        if ts:
            try:
                ages[label] = (now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
            except ValueError:
                pass
    if not ages:
        return {"available": False, "reason": "never_polled"}
    max_age = max(ages.values())
    if max_age > 600:  # 10 min stale = effectively unavailable
        return {"available": False, "reason": "stale", "max_age_s": int(max_age), "ages": ages}
    return {"available": True, "ages_s": {k: int(v) for k, v in ages.items()}}


def leg_realtime(leg: dict[str, Any]) -> dict[str, Any]:
    """Return realtime delay info for a single leg, or a 'no data' marker.

    Reads the poller's per-agency trip_updates and picks the worst positive
    delay for stops matching the leg's source_keys. Returns:
        {"available": False, "reason": "no_source_keys"|"needs_key"|"no_match"|...}
        {"available": True, "delay_min": 3.2, "agency": "caltrain"}
    """
    src_keys = leg.get("source_keys") or []
    if not src_keys:
        return {"available": False, "reason": "no_source_keys"}
    summary = realtime_status_summary()
    if not summary.get("available"):
        return {"available": False, "reason": summary.get("reason", "unknown")}
    state = poller.read_state()
    worst = 0.0
    matched = False
    agency_for_leg = None
    if "caltrain.gtfs_rt" in src_keys:
        agency_for_leg = "caltrain"
        feeds = (state.get("caltrain") or {}).get("feeds") or {}
        tu = feeds.get("trip_updates") or {}
        for trip in tu.get("trips") or []:
            for stop in trip.get("stops", []):
                d = stop.get("arrival_delay") or stop.get("departure_delay") or 0
                if d:
                    matched = True
                    if d > 0:
                        worst = max(worst, d / 60.0)
    if "sfmta.gtfs_rt" in src_keys:
        agency_for_leg = agency_for_leg or "sfmta"
        feeds = (state.get("sfmta") or {}).get("feeds") or {}
        tu = feeds.get("trip_updates") or {}
        for trip in tu.get("trips") or []:
            for stop in trip.get("stops", []):
                d = stop.get("arrival_delay") or stop.get("departure_delay") or 0
                if d:
                    matched = True
                    if d > 0:
                        worst = max(worst, d / 60.0)
    if not matched:
        return {"available": False, "reason": "no_match", "agency": agency_for_leg}
    return {"available": True, "delay_min": round(worst, 1), "agency": agency_for_leg}


def build_leg_timeline(sch: dict[str, Any], legs: list[dict[str, Any]],
                       now: datetime | None = None) -> list[dict[str, Any]]:
    """Compute per-leg depart/arrive timeline.

    For each leg, returns:
        {
          "step": int, "mode": str, "from": str, "to": str,
          "depart_at": ISO, "depart_local": "7:10 AM",
          "arrive_at": ISO, "arrive_local": "7:20 AM",
          "default_minutes": int,
          "realtime": {"available": bool, ...},
          "transfer_in_min": int | None,  # gap from prior leg's arrive (None for leg 1)
          "tight": bool,                  # True if transfer gap < ~2 min
          "status": "before"|"current"|"past",
        }

    Times are UTC ISO + a pre-formatted local "h:MM AM/PM" string for cheap
    Discord rendering. Realtime delay (if available) is ADDED to that leg's
    arrive_at and ripples forward to subsequent legs.
    """
    now = now or _now_utc()
    if sch["direction"] == "to-work":
        cursor = timefmt.parse_hhmm_to_local_today(sch["target_depart_home"], now)
    else:
        cursor = timefmt.parse_hhmm_to_local_today(sch["target_depart_work"], now)
    timeline: list[dict[str, Any]] = []
    prev_arrive: datetime | None = None
    for leg in legs:
        depart_at = cursor
        dur = int(leg.get("default_minutes", 0))
        rt = leg_realtime(leg)
        delay_min = rt.get("delay_min", 0) if rt.get("available") else 0
        arrive_at = depart_at + timedelta(minutes=dur + max(0, delay_min))
        # Determine status relative to now.
        if now < depart_at:
            status = "before"
        elif now <= arrive_at:
            status = "current"
        else:
            status = "past"
        transfer_in_min = None
        tight = False
        if prev_arrive is not None:
            # depart_at == prev_arrive by construction (cursor walks forward),
            # so transfer_in_min is always 0 in the static model. The useful
            # transfer signal lives in upcoming/leg-specific scheduled
            # departures (Caltrain timetable, Muni headway) — exposed as a
            # separate `next_scheduled_depart` field when realtime is available.
            transfer_in_min = 0
            tight = False
        timeline.append({
            "step": leg.get("step"),
            "mode": leg.get("mode"),
            "from": leg.get("from"),
            "to": leg.get("to"),
            "default_minutes": dur,
            "depart_at": depart_at.isoformat(),
            "depart_local": timefmt.local_clock(depart_at),
            "arrive_at": arrive_at.isoformat(),
            "arrive_local": timefmt.local_clock(arrive_at),
            "preferred_train": leg.get("preferred_train"),
            "preferred_route": leg.get("preferred_route"),
            "realtime": rt,
            "transfer_in_min": transfer_in_min,
            "tight": tight,
            "status": status,
            "source_keys": leg.get("source_keys", []),
        })
        prev_arrive = arrive_at
        cursor = arrive_at
    return timeline


def worst_rt_delay_min() -> float:
    """Scan the latest Caltrain trip_updates for the largest positive
    arrival_delay on stops the operator cares about. SFMTA additions go here
    once 511 key is wired.

    Returns delay in minutes (>=0). Returns 0 when no realtime data.
    """
    state = poller.read_state()
    worst = 0.0
    feeds = (state.get("caltrain") or {}).get("feeds") or {}
    tu = feeds.get("trip_updates") or {}
    for trip in tu.get("trips") or []:
        for stop in trip.get("stops", []):
            d = stop.get("arrival_delay") or stop.get("departure_delay") or 0
            if d and d > 0:
                worst = max(worst, d / 60.0)
    # SFMTA delay would feed in here when 511 key is set
    sfmta_feeds = (state.get("sfmta") or {}).get("feeds") or {}
    sfmta_tu = sfmta_feeds.get("trip_updates") or {}
    for trip in sfmta_tu.get("trips") or []:
        for stop in trip.get("stops", []):
            d = stop.get("arrival_delay") or stop.get("departure_delay") or 0
            if d and d > 0:
                worst = max(worst, d / 60.0)
    return worst


def evaluate(now: datetime | None = None) -> dict[str, Any]:
    """Produce the full commute brief used by the API + alerter + LLM.

    Empty-but-shaped when no schedule is active (e.g. weekend, middle of night)
    — never raises, always returns a dict the UI can render.
    """
    now = now or _now_utc()
    sch = active_schedule(now)
    if not sch:
        return {
            "ts": now.isoformat(),
            "ts_local": timefmt.local_iso(now),
            "active": False,
            "reason": "no schedule active right now",
            "alerts": poller.read_alerts(),
            "location": {"available": False, "reason": "no schedule active"},
        }
    loc = _location_status(sch, now)
    # If we know the operator is already at the destination, the commute is
    # done — don't pretend it's still in progress. This was the "on track at
    # home" bug: weekday-pm window is active, operator is at home, but the
    # brief used to keep saying "on track to arrive 7:45 PM" anyway.
    if loc.get("available") and loc.get("at_destination"):
        dest = "work" if sch["direction"] == "to-work" else "home"
        return {
            "ts": now.isoformat(),
            "ts_local": timefmt.local_iso(now),
            "active": False,
            "reason": f"already at {dest} — {sch['direction']} commute complete or not started today",
            "schedule_id": sch["id"],
            "direction": sch["direction"],
            "alerts": poller.read_alerts(),
            "location": loc,
        }
    legs = config.get_legs_by_id(sch["legs_id"]) or []
    leg_i = expected_leg_index(sch, legs, now)
    rt_delay = worst_rt_delay_min()
    rt_summary = realtime_status_summary()
    timeline = build_leg_timeline(sch, legs, now)
    # Projected arrive = last leg's arrive_at (already includes per-leg delay).
    if timeline:
        try:
            arr = datetime.fromisoformat(timeline[-1]["arrive_at"])
        except ValueError:
            arr = projected_arrival(sch, legs, rt_delay, now)
    else:
        arr = projected_arrival(sch, legs, rt_delay, now)
    target_arr_hhmm = sch.get("target_arrive_work") or sch.get("target_arrive_home")
    target_arr = timefmt.parse_hhmm_to_local_today(target_arr_hhmm, now)
    flex_min = int(sch.get("flex_minutes_late", 15))
    flex_deadline = target_arr + timedelta(minutes=flex_min)
    late_risk = arr > flex_deadline
    late_by_min = max(0.0, (arr - target_arr).total_seconds() / 60.0)
    # Leave-by = depart_at of leg 1 (the moment the operator needs to leave
    # their current location). Surfaced explicitly so the brief can say
    # "leave by 7:10 AM" instead of leaving the operator to compute it.
    leave_by_at = timeline[0]["depart_at"] if timeline else None
    leave_by_local = timeline[0]["depart_local"] if timeline else None
    # Next leg = first leg with status=="before" (i.e. hasn't departed yet)
    next_leg = next((l for l in timeline if l["status"] == "before"), None)
    current_leg = next((l for l in timeline if l["status"] == "current"), None)
    return {
        "ts": now.isoformat(),
        "ts_local": timefmt.local_iso(now),
        "active": True,
        "schedule_id": sch["id"],
        "direction": sch["direction"],
        "target_arrive": target_arr.isoformat(),
        "target_arrive_local": timefmt.local_clock(target_arr),
        "flex_minutes_late": flex_min,
        "flex_deadline": flex_deadline.isoformat(),
        "flex_deadline_local": timefmt.local_clock(flex_deadline),
        "projected_arrive": arr.isoformat(),
        "projected_arrive_local": timefmt.local_clock(arr),
        "leave_by": leave_by_at,
        "leave_by_local": leave_by_local,
        "expected_leg_index": leg_i,
        "expected_leg": legs[leg_i] if 0 <= leg_i < len(legs) else None,
        "current_leg": current_leg,
        "next_leg": next_leg,
        "leg_count": len(legs),
        "rt_delay_min": round(rt_delay, 1),
        "realtime_status": rt_summary,
        "timeline": timeline,
        "late_risk": late_risk,
        "late_by_min": round(late_by_min, 1),
        "alerts": poller.read_alerts(),
        "legs": legs,
        "location": loc,
    }
