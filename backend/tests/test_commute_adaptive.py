"""Tests for the adaptive commute layer: segmentation, places, transit match,
journeys, patterns, message contract, reasoner fallback."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from services.commute import segment as seg


# --- synthetic track helpers -------------------------------------------------
def _walk_offset(lat: float, lon: float, bearing_deg: float, dist_m: float) -> tuple[float, float]:
    """Move dist_m along a bearing from lat/lon (flat-earth ok at city scale)."""
    R = 6371000.0
    br = math.radians(bearing_deg)
    dlat = (dist_m * math.cos(br)) / R
    dlon = (dist_m * math.sin(br)) / (R * math.cos(math.radians(lat)))
    return lat + math.degrees(dlat), lon + math.degrees(dlon)


def _track():
    """Build home→walk→rail→walk→work with realistic speeds, 1 ping / 30 s."""
    t = datetime(2026, 6, 10, 7, 0, 0, tzinfo=timezone.utc)
    lat, lon = 37.40, -122.10
    recs: list[dict] = []
    step = timedelta(seconds=30)

    def emit(la, lo, speed):
        nonlocal t
        recs.append({"ts": t.isoformat(), "lat": la, "lng": lo,
                     "speed_mps": speed, "accuracy_m": 8.0})
        t += step

    # Home stay: 12 min standing still.
    for _ in range(24):
        emit(lat, lon, 0.0)
    # Walk to station: 6 min @ ~1.4 m/s, bearing 90.
    for _ in range(12):
        lat, lon = _walk_offset(lat, lon, 90, 1.4 * 30)
        emit(lat, lon, 1.4)
    # Rail: 24 min @ ~22 m/s, bearing 0 (north).
    for _ in range(48):
        lat, lon = _walk_offset(lat, lon, 0, 22.0 * 30)
        emit(lat, lon, 22.0)
    # Walk from station: 5 min @ ~1.3 m/s.
    for _ in range(10):
        lat, lon = _walk_offset(lat, lon, 90, 1.3 * 30)
        emit(lat, lon, 1.3)
    # Work stay: 15 min still.
    for _ in range(30):
        emit(lat, lon, 0.0)
    return recs


def test_segment_finds_two_stays_and_three_legs():
    out = seg.segment(_track())
    kinds = [s["kind"] for s in out["segments"]]
    stays = [s for s in out["segments"] if s["kind"] == "stay"]
    moves = [s for s in out["segments"] if s["kind"] == "move"]
    assert len(stays) == 2, kinds
    # walk, wheels, walk
    assert len(moves) == 3, [(m["mode"], m["dist_m"]) for m in moves]
    assert [m["mode"] for m in moves] == ["walk", "wheels", "walk"]
    assert out["coverage"] > 0.9


def test_segment_wheels_leg_is_the_fast_long_one():
    out = seg.segment(_track())
    moves = [s for s in out["segments"] if s["kind"] == "move"]
    wheels = next(m for m in moves if m["mode"] == "wheels")
    assert wheels["peak_speed_mps"] > 10
    assert wheels["dist_m"] > 5000  # ~31 km of rail


def test_segment_empty_and_tiny_inputs():
    assert seg.segment([])["segments"] == []
    assert seg.segment([{"ts": "2026-06-10T07:00:00Z", "lat": 37.4, "lng": -122.1}])["segments"] == []


def test_segment_stays_are_home_and_work_distinct():
    out = seg.segment(_track())
    stays = [s for s in out["segments"] if s["kind"] == "stay"]
    from services.commute.geo import haversine_m
    d = haversine_m(stays[0]["lat"], stays[0]["lon"], stays[1]["lat"], stays[1]["lon"])
    assert d > 10000  # home and work are far apart


# --- place learning ----------------------------------------------------------
def _dwell(recs, la, lo, start_utc, minutes):
    """Emit a stay: a point every 2 min for `minutes` at (la,lo)."""
    t = start_utc
    end = start_utc + timedelta(minutes=minutes)
    while t <= end:
        recs.append({"ts": t.isoformat(), "lat": la, "lng": lo, "speed_mps": 0.0})
        t += timedelta(minutes=2)


def test_places_learns_home_and_work(tmp_path, monkeypatch):
    from services.commute import places
    from services import location as location_svc

    HOME = (37.40, -122.10)
    WORK = (37.79, -122.40)
    recs: list[dict] = []
    # 3 weekdays (Jun 8/9/10 2026 = Mon/Tue/Wed). PDT = UTC-7.
    for day in (8, 9, 10):
        # Home overnight: 23:00 prev → 06:00 local  == 06:00→13:00 UTC
        _dwell(recs, *HOME, datetime(2026, 6, day, 6, 0, tzinfo=timezone.utc), 60)
        # Work daytime: 09:00→16:00 local == 16:00→23:00 UTC
        _dwell(recs, *WORK, datetime(2026, 6, day, 16, 30, tzinfo=timezone.utc), 90)

    monkeypatch.setattr(location_svc, "recent_records", lambda *a, **k: recs)
    monkeypatch.setattr(places, "_path", lambda: tmp_path / "places.json")

    doc = places.learn()
    by_label = {p["auto_label"]: p for p in doc["places"] if p["auto_label"]}
    assert "home" in by_label and "work" in by_label
    from services.commute.geo import haversine_m
    assert haversine_m(by_label["home"]["lat"], by_label["home"]["lon"], *HOME) < 100
    assert haversine_m(by_label["work"]["lat"], by_label["work"]["lon"], *WORK) < 100
    # endpoint_coords resolves learned home
    assert places.endpoint_coords("home") is not None


# --- transit matching --------------------------------------------------------
def _vehicle_rows_from_wheels(records, route_id="CT-EXP", agency="caltrain"):
    """Build a vehicle track that mirrors the wheels leg of a segmented track."""
    out = seg.segment(records)
    wheels = next(s for s in out["segments"] if s.get("mode") == "wheels")
    rows = []
    for la, lo, ts in wheels["path"]:
        epoch = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        rows.append({"poll_ts": ts, "agency": agency, "trip_id": "trip-123",
                     "route_id": route_id, "lat": la, "lon": lo, "vts": epoch,
                     "stop_id": "STOP_A"})
    return rows


def test_transit_match_matches_colocated_vehicle():
    from services.commute import transit_match
    out = seg.segment(_track())
    wheels = next(s for s in out["segments"] if s.get("mode") == "wheels")
    rows = _vehicle_rows_from_wheels(_track())
    m = transit_match.match_leg(wheels, rows)
    assert m["matched"] is True
    assert m["route_id"] == "CT-EXP"
    assert m["score"] >= 0.8


def test_transit_match_rejects_distant_vehicle():
    from services.commute import transit_match
    out = seg.segment(_track())
    wheels = next(s for s in out["segments"] if s.get("mode") == "wheels")
    # vehicle far away (different city)
    rows = []
    for la, lo, ts in wheels["path"]:
        epoch = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        rows.append({"poll_ts": ts, "agency": "caltrain", "trip_id": "other",
                     "route_id": "X", "lat": la + 1.0, "lon": lo + 1.0, "vts": epoch})
    assert transit_match.match_leg(wheels, rows)["matched"] is False


def test_classify_wheels_drive_when_no_match():
    from services.commute import transit_match
    out = seg.segment(_track())
    wheels = next(s for s in out["segments"] if s.get("mode") == "wheels")
    leg = transit_match.classify_wheels(wheels, [])
    assert leg["mode"] == "drive"


# --- journeys ----------------------------------------------------------------
def test_build_journey_structure_and_transfers(monkeypatch):
    from services.commute import journeys, places
    monkeypatch.setattr(places, "nearest", lambda lat, lon, **k: None)
    rows = _vehicle_rows_from_wheels(_track())
    j = journeys.build_journey(_track(), rows)
    assert j is not None
    modes = [leg["mode"] for leg in j["legs"]]
    assert modes == ["walk", "rail", "walk"]      # wheels matched → rail
    assert j["total_min"] > 30
    assert len(j["transfers"]) == 2               # walk|rail, rail|walk boundaries
    assert j["partial"] is False
    assert j["data_quality"]["matched_legs"] == 1


def test_build_journey_none_without_movement():
    from services.commute import journeys
    recs = [{"ts": datetime(2026, 6, 10, 7, i, tzinfo=timezone.utc).isoformat(),
             "lat": 37.4, "lng": -122.1, "speed_mps": 0.0} for i in range(0, 20, 2)]
    assert journeys.build_journey(recs, []) is None


def test_places_user_label_survives_relearn(tmp_path, monkeypatch):
    from services.commute import places
    from services import location as location_svc
    recs: list[dict] = []
    _dwell(recs, 37.40, -122.10, datetime(2026, 6, 8, 6, 0, tzinfo=timezone.utc), 60)
    _dwell(recs, 37.40, -122.10, datetime(2026, 6, 9, 6, 0, tzinfo=timezone.utc), 60)
    monkeypatch.setattr(location_svc, "recent_records", lambda *a, **k: recs)
    monkeypatch.setattr(places, "_path", lambda: tmp_path / "places.json")
    doc = places.learn()
    pid = doc["places"][0]["id"]
    places.set_label(pid, "The Cabin")
    doc2 = places.learn()
    assert any(p["user_label"] == "The Cabin" for p in doc2["places"])


# --- message contract --------------------------------------------------------
def test_message_contract_clamps_and_coerces():
    from services.commute import messages
    m = messages.make_message(kind="bogus", severity="nope", headline="x" * 500,
                              confidence="??", detail="d", reasoning="r")
    assert m["kind"] == "brief" and m["severity"] == "info" and m["confidence"] == "medium"
    assert len(m["headline"]) <= 160 and m["id"]


def test_message_validate_from_llm_dict():
    from services.commute import messages
    m = messages.validate({"kind": "alert", "severity": "urgent", "headline": "Late",
                           "action": "go now", "evidence": {"a": 1}})
    assert m["kind"] == "alert" and m["action"] == "go now" and m["evidence"] == {"a": 1}


# --- reasoner ladder ---------------------------------------------------------
def test_reasoner_deterministic_on_track(monkeypatch):
    from services.commute import reason
    monkeypatch.setattr(reason, "REASONER", "deterministic")
    brief = {"active": True, "direction": "to-work", "leave_by_local": "7:10 AM",
             "target_arrive_local": "8:45 AM", "projected_arrive_local": "8:39 AM",
             "late_risk": False, "late_by_min": 0.0,
             "realtime_status": {"available": True}, "timeline": [], "alerts": [],
             "location": {"available": False}}
    m = reason.compose(brief, "pre-1h")
    assert m["severity"] == "ok" and m["kind"] == "brief"
    assert "on track" in m["headline"].lower() and m["source"].endswith("deterministic")


def test_reasoner_deterministic_late_risk_is_alert(monkeypatch):
    from services.commute import reason
    monkeypatch.setattr(reason, "REASONER", "deterministic")
    brief = {"active": True, "direction": "to-home", "late_risk": True, "late_by_min": 25.0,
             "target_arrive_local": "6:45 PM", "projected_arrive_local": "7:10 PM",
             "realtime_status": {"available": True}, "timeline": [], "alerts": [],
             "location": {"available": True}}
    m = reason.compose(brief, "during")
    assert m["kind"] == "alert" and m["severity"] == "urgent"


def test_reasoner_blind_never_claims_on_track(monkeypatch):
    # The 7:57-on-the-train bug: no realtime AND stale location. Absence of a
    # late signal must NOT be rendered as "On track" — say we can't see.
    from services.commute import reason
    monkeypatch.setattr(reason, "REASONER", "deterministic")
    brief = {"active": True, "direction": "to-home", "leave_by_local": "6:00 PM",
             "target_arrive_local": "7:29 PM", "projected_arrive_local": "7:29 PM",
             "late_risk": False, "late_by_min": 0.0,
             "realtime_status": {"available": False},
             "location": {"available": False, "reason": "last ping 116m ago — too stale to trust"},
             "timeline": [], "alerts": []}
    m = reason.compose(brief, "during")
    assert "on track" not in m["headline"].lower()
    assert m["severity"] == "caution" and m["confidence"] == "low"
    assert "blind" in m["headline"].lower()


def test_reasoner_overdue_late_cites_clock_and_caveats(monkeypatch):
    # Late detected from the wall clock alone (no realtime), with stale location.
    from services.commute import reason
    monkeypatch.setattr(reason, "REASONER", "deterministic")
    brief = {"active": True, "direction": "to-home", "late_risk": True, "late_by_min": 28.0,
             "arrival_overdue": True, "target_arrive_local": "7:29 PM",
             "projected_arrive_local": "7:57 PM",
             "realtime_status": {"available": False},
             "location": {"available": False, "reason": "last ping 116m ago — too stale to trust"},
             "current_leg": {"mode": "rail"}, "timeline": [], "alerts": []}
    m = reason.compose(brief, "during")
    assert m["kind"] == "alert" and m["severity"] == "urgent"
    assert "running late" in m["headline"].lower()
    assert "clock" in m["reasoning"].lower() and "no live transit data" in m["reasoning"].lower()


def test_reasoner_on_track_drops_leave_by_once_underway(monkeypatch):
    from services.commute import reason
    monkeypatch.setattr(reason, "REASONER", "deterministic")
    brief = {"active": True, "direction": "to-work", "leave_by_local": "7:10 AM",
             "target_arrive_local": "8:45 AM", "projected_arrive_local": "8:39 AM",
             "late_risk": False, "late_by_min": 0.0,
             "realtime_status": {"available": True}, "location": {"available": True},
             "current_leg": {"mode": "rail"}, "timeline": [], "alerts": []}
    m = reason.compose(brief, "during")
    assert "on track" in m["headline"].lower() and "leave by" not in m["headline"].lower()


def test_reasoner_falls_back_when_claude_unavailable(monkeypatch):
    from services.commute import reason
    monkeypatch.setattr(reason, "REASONER", "claude")
    monkeypatch.setattr(reason, "_call_claude", lambda p: None)
    monkeypatch.setattr(reason, "_call_qwen", lambda p: None)
    brief = {"active": False, "reason": "weekend", "next_schedule": None,
             "realtime_status": {}, "location": {}}
    m = reason.compose(brief)
    assert m["source"] == "reasoner:deterministic" and m["severity"] == "ok"


# --- patterns ----------------------------------------------------------------
def test_patterns_compute_and_miss_rate(monkeypatch):
    from services.commute import patterns
    js = [
        {"direction": "to-work", "partial": False, "total_min": 95, "late_by_min": 2,
         "legs": [{"mode": "walk", "minutes": 6, "avg_speed_mps": 1.4},
                  {"mode": "rail", "minutes": 62}, {"mode": "transit", "minutes": 12}],
         "transfers": [{"from_mode": "rail", "to_mode": "transit", "wait_s": 90, "tight": True, "long_wait": False}]},
        {"direction": "to-work", "partial": False, "total_min": 130, "late_by_min": 33,
         "legs": [{"mode": "walk", "minutes": 7, "avg_speed_mps": 1.3},
                  {"mode": "rail", "minutes": 64}, {"mode": "transit", "minutes": 12}],
         "transfers": [{"from_mode": "rail", "to_mode": "transit", "wait_s": 1900, "tight": False, "long_wait": True}]},
    ]
    monkeypatch.setattr(patterns.journeys_mod, "recent", lambda n=60: js)
    out = patterns.compute()
    d = out["directions"]["to-work"]
    assert d["n"] == 2
    assert d["transfers"]["miss_rate"] == 0.5
    assert d["total_min"]["p50"] is not None
    summ = patterns.summarize_for_reasoner("to-work")
    assert summ["stats"]["n"] == 2 and len(summ["recent_anomalies"]) >= 1
