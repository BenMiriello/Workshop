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

from . import config, places, poller, timefmt
from .. import location as location_svc


# How close (meters) to home/work counts as "at destination" — generous to
# cover building entrance vs GPS scatter. Mountain View row of houses ≈ 50m.
AT_DESTINATION_RADIUS_M = 200.0
# How fresh location data has to be before we trust it for "at destination"
# checks. Older than this and we fall back to time-only logic.
LOCATION_STALE_AFTER_S = 30 * 60

# A single transit leg cannot realistically run more than ~2 hours late and
# still be the leg you're "on". GTFS-RT feeds routinely carry stale or
# rolled-over StopTimeUpdates whose `delay` field is computed against a trip
# from a PRIOR service day — these show up as exactly 86400s (24h) or other
# absurd values. Anything past this ceiling is a feed artifact, not a real
# delay, and is discarded. (2026-05-25 bug: a 86400s SFMTA delay on a trip
# dated 20260524 produced a phantom "1440 min late / 1434m behind target".)
MAX_PLAUSIBLE_DELAY_MIN = 120.0


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
    # Prefer LEARNED place coords (place-learning derives home/work from dwell
    # history) so this works without the operator ever pasting coordinates;
    # falls back to any manually-set schedule coord.
    dest = places.endpoint_coords(dest_name)
    if not dest:
        return {"available": False, "reason": f"no learned/known coords for {dest_name} yet (needs location history)"}
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


def _legs_total_minutes(legs: list[dict[str, Any]]) -> int:
    return sum(int(l.get("default_minutes", 0)) for l in (legs or []))


def resolve_times(sch: dict[str, Any], legs: list[dict[str, Any]],
                  now: datetime | None = None) -> tuple[datetime, datetime]:
    """Resolve a schedule entry to concrete (depart, target_arrive) datetimes.

    Anchor model — each schedule sets exactly ONE end it actually cares about,
    and the other is DERIVED from the leg durations so the two can never
    contradict each other:
        anchor="arrive":  you set arrive-by; depart = arrive − Σlegs − buffer
        anchor="depart":  you set depart-at; arrive = depart + Σlegs
    `buffer_minutes` (arrive-anchored only) is padding so the plan aims to land
    a few minutes early rather than exactly on the deadline.

    Legacy fallback: pre-anchor schedules carried BOTH target_depart_* and
    target_arrive_* as independent hand-set fields. Those are read as-is so
    schedules saved before this change keep working until re-saved through the
    new editor. New code/UI always writes anchor + anchor_time.
    """
    now = now or _now_utc()
    total = _legs_total_minutes(legs)
    anchor = sch.get("anchor")
    anchor_time = sch.get("anchor_time")
    if anchor in ("arrive", "depart") and anchor_time:
        at = timefmt.parse_hhmm_to_local_today(anchor_time, now)
        if anchor == "arrive":
            buffer = max(0, int(sch.get("buffer_minutes", 0) or 0))
            return at - timedelta(minutes=total + buffer), at
        return at, at + timedelta(minutes=total)
    # ---- legacy two-field fallback ----
    if sch.get("direction") == "to-work":
        depart_hhmm = sch.get("target_depart_home")
        arrive_hhmm = sch.get("target_arrive_work")
    else:
        depart_hhmm = sch.get("target_depart_work")
        arrive_hhmm = sch.get("target_arrive_home")
    depart = timefmt.parse_hhmm_to_local_today(depart_hhmm, now) if depart_hhmm else now
    target_arrive = (timefmt.parse_hhmm_to_local_today(arrive_hhmm, now)
                     if arrive_hhmm else depart + timedelta(minutes=total))
    return depart, target_arrive


def _window_legs(sch: dict[str, Any]) -> list[dict[str, Any]]:
    """Legs needed to resolve an anchor-mode schedule's window. Only loaded for
    anchor-mode entries — legacy entries resolve from their own fields, so we
    skip the config read (keeps active_schedule() free of disk I/O in tests)."""
    if sch.get("anchor"):
        return config.get_legs_by_id(sch.get("legs_id")) or []
    return []


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
        depart, target_arrive = resolve_times(sch, _window_legs(sch), now)
        # Window opens 1h before leave-by (covers the pre-commute briefs) and
        # closes 1h past the target arrival (covers a late-running trip).
        start = depart - timedelta(hours=1)
        end = target_arrive + timedelta(minutes=60)
        if start <= now <= end:
            return sch
    return None


def next_scheduled(now: datetime | None = None, horizon_days: int = 8) -> dict[str, Any] | None:
    """The soonest upcoming schedule occurrence whose leave-by is still in the
    future. Scans today → +horizon_days, both directions, and returns a compact
    descriptor the UI shows when nothing is active right now ("next commute:
    Mon, leave by 7:11 AM"). Returns None if nothing is scheduled in range."""
    now = now or _now_utc()
    for offset in range(horizon_days):
        day_dt = now + timedelta(days=offset)
        day = timefmt.day_key(day_dt)
        candidates: list[tuple[datetime, datetime, dict[str, Any]]] = []
        for direction in ("to-work", "to-home"):
            sch = config.get_schedule_for_day(direction, day)
            if not sch:
                continue
            legs = config.get_legs_by_id(sch.get("legs_id")) or []
            # Anchor resolution to THAT calendar day by resolving as-of day_dt.
            depart, arrive = resolve_times(sch, legs, day_dt)
            candidates.append((depart, arrive, sch))
        for depart, arrive, sch in sorted(candidates, key=lambda c: c[0]):
            if depart > now:
                return {
                    "schedule_id": sch.get("id"),
                    "direction": sch.get("direction"),
                    "day": day,
                    "date_local": timefmt.local(depart).strftime("%a %b %-d"),
                    "leave_by": depart.isoformat(),
                    "leave_by_local": timefmt.local_clock(depart),
                    "target_arrive": arrive.isoformat(),
                    "target_arrive_local": timefmt.local_clock(arrive),
                }
    return None


def expected_leg_index(sch: dict[str, Any], legs: list[dict[str, Any]], now: datetime | None = None) -> int:
    """Best guess (0-indexed) of which leg the operator should be on right now,
    based on cumulative default_minutes from target_depart."""
    now = now or _now_utc()
    depart, _ = resolve_times(sch, legs, now)
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
    depart, _ = resolve_times(sch, legs, now)
    total_min = _legs_total_minutes(legs) + max(0.0, rt_delay_min)
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


def _service_day_str(now: datetime | None = None) -> str:
    """Today's GTFS service date in operator-local tz as 'YYYYMMDD'.

    GTFS-RT `trip.start_date` is the service date the trip belongs to. We only
    trust StopTimeUpdates whose start_date matches the current local service
    day; yesterday's rolled-over entries are stale and carry bogus delays.
    """
    local_now = timefmt.local(now or _now_utc())
    return local_now.strftime("%Y%m%d")


def _worst_positive_delay_from_feed(
    tu: dict[str, Any],
    service_day: str,
    route_ids: list[str] | None = None,
) -> tuple[float, bool]:
    """Scan one agency's trip_updates for the worst PLAUSIBLE positive delay
    on the operator's actual route(s).

    Three filters, in order:
      1. Service-day: trip.start_date must equal today's local service date.
         Skips rolled-over / cross-midnight entries with full-day phantom
         delays (the 86400s / 1440min bug, 2026-05-25).
      2. Route: if `route_ids` is given, only trips on those routes count. The
         feed carries every route the agency runs (642 SFMTA trips); without
         this filter an unrelated route's 60-min delay leaks into a 12-min
         leg's ETA. route_id matching is prefix-tolerant so "T" also matches a
         "TBUS" rail-replacement-bus substitution.
      3. Sanity ceiling: discard any per-stop delay > MAX_PLAUSIBLE_DELAY_MIN
         as a feed artifact regardless of the above.

    Returns (worst_delay_min, matched_any). `matched_any` distinguishes "no
    relevant same-day route data" from "route is on-time".
    """
    norm_routes = [r.upper() for r in (route_ids or [])]

    def _route_matches(rid: str) -> bool:
        if not norm_routes:
            return True  # no filter requested
        rid_u = (rid or "").upper()
        # exact, or feed route is a substitution variant of ours (T -> TBUS)
        return any(rid_u == r or rid_u.startswith(r) or r.startswith(rid_u)
                   for r in norm_routes if rid_u)

    worst = 0.0
    matched = False
    for trip in tu.get("trips") or []:
        start_date = str(trip.get("start_date") or "")
        if start_date and start_date != service_day:
            continue
        if not _route_matches(str(trip.get("route_id") or "")):
            continue
        for stop in trip.get("stops", []):
            raw = stop.get("arrival_delay")
            if raw is None:
                raw = stop.get("departure_delay")
            if not raw:
                continue
            delay_min = raw / 60.0
            if abs(delay_min) > MAX_PLAUSIBLE_DELAY_MIN:
                continue
            matched = True
            if delay_min > 0:
                worst = max(worst, delay_min)
    return worst, matched


def leg_realtime(leg: dict[str, Any]) -> dict[str, Any]:
    """Return realtime delay info for a single leg, or a 'no data' marker.

    Reads the poller's per-agency trip_updates and picks the worst PLAUSIBLE
    positive delay for the current service day on the leg's source_keys.
    Returns:
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
    service_day = _service_day_str()
    worst = 0.0
    matched = False
    agency_for_leg = None
    route_ids = leg.get("route_ids") or []
    if "caltrain.gtfs_rt" in src_keys:
        agency_for_leg = "caltrain"
        feeds = (state.get("caltrain") or {}).get("feeds") or {}
        tu = feeds.get("trip_updates") or {}
        w, m = _worst_positive_delay_from_feed(tu, service_day, route_ids)
        worst = max(worst, w)
        matched = matched or m
    if "sfmta.gtfs_rt" in src_keys:
        agency_for_leg = agency_for_leg or "sfmta"
        feeds = (state.get("sfmta") or {}).get("feeds") or {}
        tu = feeds.get("trip_updates") or {}
        w, m = _worst_positive_delay_from_feed(tu, service_day, route_ids)
        worst = max(worst, w)
        matched = matched or m
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
    cursor, _ = resolve_times(sch, legs, now)
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


def worst_rt_delay_min(legs: list[dict[str, Any]] | None = None) -> float:
    """Scan the latest Caltrain + SFMTA trip_updates for the largest PLAUSIBLE
    positive delay on the operator's actual routes for the current service day.

    Applies the same service-day filter, route filter, and sanity ceiling as
    `leg_realtime` (see `_worst_positive_delay_from_feed`) so neither stale
    cross-midnight entries nor unrelated routes inject a phantom delay into the
    ETA. When `legs` is given, only those legs' `route_ids` are considered per
    agency; otherwise all routes on the agency feed are eligible (legacy).

    Returns delay in minutes (>=0). Returns 0 when no realtime data.
    """
    # Gate on availability so we never report a live delay while the brief is
    # simultaneously labelling times "static schedule" (stale/no-key). Keeps
    # rt_delay_min consistent with the per-leg realtime markers.
    if not realtime_status_summary().get("available"):
        return 0.0

    state = poller.read_state()
    service_day = _service_day_str()

    def _routes_for(source_key: str) -> list[str] | None:
        if legs is None:
            return None
        ids: list[str] = []
        for leg in legs:
            if source_key in (leg.get("source_keys") or []):
                ids.extend(leg.get("route_ids") or [])
        # If the leg(s) for this source declared no route_ids, fall back to
        # "no filter" so we don't silently drop a leg that lacks route metadata.
        return ids or None

    worst = 0.0
    ct_tu = ((state.get("caltrain") or {}).get("feeds") or {}).get("trip_updates") or {}
    w, _ = _worst_positive_delay_from_feed(ct_tu, service_day, _routes_for("caltrain.gtfs_rt"))
    worst = max(worst, w)
    sf_tu = ((state.get("sfmta") or {}).get("feeds") or {}).get("trip_updates") or {}
    w, _ = _worst_positive_delay_from_feed(sf_tu, service_day, _routes_for("sfmta.gtfs_rt"))
    worst = max(worst, w)
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
            "next_schedule": next_scheduled(now),
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
    rt_delay = worst_rt_delay_min(legs)
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
    _, target_arr = resolve_times(sch, legs, now)
    flex_min = int(sch.get("flex_minutes_late", 15))
    flex_deadline = target_arr + timedelta(minutes=flex_min)
    # We only reach here when the commute is active AND we have NOT confirmed
    # the operator is at the destination (that case early-returns above). So
    # the earliest he could realistically arrive is *now* — once the wall clock
    # passes the static schedule's arrival with no at-destination confirmation,
    # "arrived 7:29" is moot and he is, in fact, late. Using max(arr, now) makes
    # "past your arrival but no realtime/location to prove it" read as late.
    # This signal needs neither a GTFS-RT feed nor a fresh GPS ping — the
    # schedule plus the clock are enough. When realtime IS available and arr is
    # a genuine future projection, effective_arr == arr and nothing changes.
    effective_arr = max(arr, now)
    arrival_overdue = now > arr  # wall clock already past the projected arrival
    late_risk = effective_arr > flex_deadline
    late_by_min = max(0.0, (effective_arr - target_arr).total_seconds() / 60.0)
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
        "projected_arrive": effective_arr.isoformat(),
        "projected_arrive_local": timefmt.local_clock(effective_arr),
        "scheduled_arrive_local": timefmt.local_clock(arr),
        "arrival_overdue": arrival_overdue,
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
