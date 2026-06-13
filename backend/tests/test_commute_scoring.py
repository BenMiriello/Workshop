"""Tests for backend/services/commute/scoring.py.

Mocks external I/O (config, poller, location_svc) so tests run offline with
no filesystem dependencies. All time fixtures use real UTC datetimes pinned to
a known weekday (2026-01-05 Mon) or weekend (2026-01-03 Sat) in PST.

PST = UTC-8 during January:
  7:30 AM PST Mon = 2026-01-05 15:30 UTC
  5:30 PM PST Mon = 2026-01-06 01:30 UTC  (still local Monday)
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Backend modules are top-level imports (matches how main.py runs the app);
# add backend/ to sys.path so `services.commute.scoring` resolves regardless
# of which cwd pytest is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.commute import scoring  # noqa: E402

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

MON_AM = datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc)   # 7:30 AM PST Mon
MON_PM = datetime(2026, 1, 6, 1, 30, tzinfo=timezone.utc)    # 5:30 PM PST Mon
SAT_AM = datetime(2026, 1, 3, 15, 30, tzinfo=timezone.utc)   # 7:30 AM PST Sat

WEEKDAY_AM_SCHEDULE = {
    "id": "weekday-am",
    "direction": "to-work",
    "days": ["mon", "tue", "wed", "thu", "fri"],
    "target_depart_home": "07:10",
    "target_arrive_work": "08:30",
    "flex_minutes_late": 15,
    "legs_id": "to-work-standard",
}

WEEKDAY_PM_SCHEDULE = {
    "id": "weekday-pm",
    "direction": "to-home",
    "days": ["mon", "tue", "wed", "thu", "fri"],
    "target_depart_work": "17:30",
    "target_arrive_home": "18:45",
    "flex_minutes_late": 15,
    "legs_id": "to-home-standard",
}

SAMPLE_LEGS = [
    {
        "step": 1,
        "mode": "walk",
        "from": "Home",
        "to": "Station",
        "default_minutes": 5,
        "source_keys": [],
    },
    {
        "step": 2,
        "mode": "caltrain",
        "from": "Station",
        "to": "SF",
        "default_minutes": 60,
        "source_keys": ["caltrain.gtfs_rt"],
    },
    {
        "step": 3,
        "mode": "muni",
        "from": "SF",
        "to": "Office",
        "default_minutes": 15,
        "source_keys": ["sfmta.gtfs_rt"],
    },
]

TOTAL_LEG_MINUTES = 5 + 60 + 15  # 80


# ---------------------------------------------------------------------------
# _haversine_m
# ---------------------------------------------------------------------------

class TestHaversine:
    def test_same_point_is_zero(self):
        assert scoring._haversine_m(37.4, -122.1, 37.4, -122.1) == pytest.approx(0.0, abs=1e-6)

    def test_known_distance(self):
        # Mountain View Caltrain to SF Caltrain — ~55 km
        mv = (37.3947, -122.0764)
        sf = (37.7762, -122.3942)
        d = scoring._haversine_m(mv[0], mv[1], sf[0], sf[1])
        assert 50_000 < d < 65_000

    def test_symmetry(self):
        a = (37.0, -122.0)
        b = (38.0, -121.0)
        assert scoring._haversine_m(*a, *b) == pytest.approx(scoring._haversine_m(*b, *a))


# ---------------------------------------------------------------------------
# active_schedule
# ---------------------------------------------------------------------------

def _make_config_mock(am_entry=None, pm_entry=None):
    """Return a side_effect fn for config.get_schedule_for_day."""
    def _get(direction, day):
        if direction == "to-work":
            return am_entry
        if direction == "to-home":
            return pm_entry
        return None
    return _get


class TestActiveSchedule:
    def test_weekend_returns_none(self):
        with patch.object(scoring.config, "get_schedule_for_day", return_value=None):
            result = scoring.active_schedule(SAT_AM)
        assert result is None

    def test_weekday_inside_am_window(self):
        # 7:30 AM is inside [6:10 AM, 9:30 AM] window for the AM schedule
        with patch.object(scoring.config, "get_schedule_for_day",
                          side_effect=_make_config_mock(am_entry=WEEKDAY_AM_SCHEDULE)):
            result = scoring.active_schedule(MON_AM)
        assert result is not None
        assert result["direction"] == "to-work"

    def test_weekday_inside_pm_window(self):
        # 5:30 PM is inside [5:00 PM, 7:45 PM] window for the PM schedule
        with patch.object(scoring.config, "get_schedule_for_day",
                          side_effect=_make_config_mock(pm_entry=WEEKDAY_PM_SCHEDULE)):
            result = scoring.active_schedule(MON_PM)
        assert result is not None
        assert result["direction"] == "to-home"

    def test_outside_all_windows_returns_none(self):
        # 7:30 AM Mon, no schedules active
        with patch.object(scoring.config, "get_schedule_for_day", return_value=None):
            result = scoring.active_schedule(MON_AM)
        assert result is None

    def test_am_takes_precedence_when_both_could_match(self):
        # Only AM schedule returned by mock — should pick it
        with patch.object(scoring.config, "get_schedule_for_day",
                          side_effect=_make_config_mock(
                              am_entry=WEEKDAY_AM_SCHEDULE,
                              pm_entry=WEEKDAY_PM_SCHEDULE,
                          )):
            result = scoring.active_schedule(MON_AM)
        assert result["direction"] == "to-work"


# ---------------------------------------------------------------------------
# expected_leg_index
# ---------------------------------------------------------------------------

class TestExpectedLegIndex:
    def _depart_time(self) -> datetime:
        # to-work: depart at 7:10 AM PST = 15:10 UTC on 2026-01-05
        return datetime(2026, 1, 5, 15, 10, tzinfo=timezone.utc)

    def test_before_departure(self):
        before = self._depart_time() - timedelta(minutes=30)
        idx = scoring.expected_leg_index(WEEKDAY_AM_SCHEDULE, SAMPLE_LEGS, before)
        assert idx == -1

    def test_on_first_leg(self):
        # 2 minutes after depart — still on walk (leg 0, 5 min)
        t = self._depart_time() + timedelta(minutes=2)
        idx = scoring.expected_leg_index(WEEKDAY_AM_SCHEDULE, SAMPLE_LEGS, t)
        assert idx == 0

    def test_on_second_leg(self):
        # 10 minutes after depart — past walk (5 min), on Caltrain (60 min)
        t = self._depart_time() + timedelta(minutes=10)
        idx = scoring.expected_leg_index(WEEKDAY_AM_SCHEDULE, SAMPLE_LEGS, t)
        assert idx == 1

    def test_on_last_leg(self):
        # 70 minutes after depart — past walk+caltrain, on Muni
        t = self._depart_time() + timedelta(minutes=70)
        idx = scoring.expected_leg_index(WEEKDAY_AM_SCHEDULE, SAMPLE_LEGS, t)
        assert idx == 2

    def test_past_all_legs(self):
        # 120 minutes after depart — past everything
        t = self._depart_time() + timedelta(minutes=120)
        idx = scoring.expected_leg_index(WEEKDAY_AM_SCHEDULE, SAMPLE_LEGS, t)
        assert idx == len(SAMPLE_LEGS) - 1


# ---------------------------------------------------------------------------
# projected_arrival
# ---------------------------------------------------------------------------

class TestProjectedArrival:
    def _depart_time(self) -> datetime:
        return datetime(2026, 1, 5, 15, 10, tzinfo=timezone.utc)  # 7:10 AM PST

    def test_no_delay(self):
        t = self._depart_time()
        arr = scoring.projected_arrival(WEEKDAY_AM_SCHEDULE, SAMPLE_LEGS, 0.0, t)
        expected = t + timedelta(minutes=TOTAL_LEG_MINUTES)
        assert arr == expected

    def test_with_positive_delay(self):
        t = self._depart_time()
        arr = scoring.projected_arrival(WEEKDAY_AM_SCHEDULE, SAMPLE_LEGS, 5.0, t)
        expected = t + timedelta(minutes=TOTAL_LEG_MINUTES + 5)
        assert arr == expected

    def test_negative_delay_clamped_to_zero(self):
        t = self._depart_time()
        arr = scoring.projected_arrival(WEEKDAY_AM_SCHEDULE, SAMPLE_LEGS, -10.0, t)
        expected = t + timedelta(minutes=TOTAL_LEG_MINUTES)
        assert arr == expected


class TestNextScheduled:
    def test_finds_next_weekday_am_from_weekend(self):
        def _sched(direction, day):
            if direction == "to-work" and day == "mon":
                return WEEKDAY_AM_SCHEDULE
            return None
        with patch.object(scoring.config, "get_schedule_for_day", side_effect=_sched), \
             patch.object(scoring.config, "get_legs_by_id", return_value=SAMPLE_LEGS):
            nxt = scoring.next_scheduled(SAT_AM)  # Sat 2026-01-03 → next is Mon 01-05
        assert nxt is not None
        assert nxt["direction"] == "to-work"
        assert nxt["day"] == "mon"
        assert nxt["leave_by_local"] == "7:10 AM"  # legacy depart field, unchanged

    def test_none_when_nothing_scheduled(self):
        with patch.object(scoring.config, "get_schedule_for_day", return_value=None):
            assert scoring.next_scheduled(SAT_AM) is None


# ---------------------------------------------------------------------------
# resolve_times — anchor model
# ---------------------------------------------------------------------------

class TestResolveTimes:
    def test_arrive_anchored_derives_depart(self):
        # arrive 08:45, Σlegs=80, buffer=5 → depart = 08:45 − 85min = 07:20
        sch = {"direction": "to-work", "anchor": "arrive",
               "anchor_time": "08:45", "buffer_minutes": 5}
        depart, arrive = scoring.resolve_times(sch, SAMPLE_LEGS, MON_AM)
        assert (arrive - depart) == timedelta(minutes=TOTAL_LEG_MINUTES + 5)
        assert scoring.timefmt.local_clock(arrive) == "8:45 AM"
        assert scoring.timefmt.local_clock(depart) == "7:20 AM"

    def test_depart_anchored_derives_arrive(self):
        # depart 18:00, Σlegs=80 → arrive = 19:20
        sch = {"direction": "to-home", "anchor": "depart", "anchor_time": "18:00"}
        depart, arrive = scoring.resolve_times(sch, SAMPLE_LEGS, MON_PM)
        assert (arrive - depart) == timedelta(minutes=TOTAL_LEG_MINUTES)
        assert scoring.timefmt.local_clock(depart) == "6:00 PM"
        assert scoring.timefmt.local_clock(arrive) == "7:20 PM"

    def test_arrive_anchored_no_buffer(self):
        sch = {"direction": "to-work", "anchor": "arrive", "anchor_time": "09:00"}
        depart, arrive = scoring.resolve_times(sch, SAMPLE_LEGS, MON_AM)
        assert (arrive - depart) == timedelta(minutes=TOTAL_LEG_MINUTES)

    def test_legacy_two_field_fallback(self):
        # Pre-anchor schedule: both fields read independently, unchanged behavior.
        depart, arrive = scoring.resolve_times(WEEKDAY_AM_SCHEDULE, SAMPLE_LEGS, MON_AM)
        assert scoring.timefmt.local_clock(depart) == "7:10 AM"
        assert scoring.timefmt.local_clock(arrive) == "8:30 AM"


# ---------------------------------------------------------------------------
# _location_status
# ---------------------------------------------------------------------------

HOME_COORDS = (37.394, -122.076)
WORK_COORDS = (37.787, -122.398)


class TestLocationStatus:
    def _now(self):
        return MON_AM

    def test_no_coords_configured(self):
        with patch.object(scoring.places, "endpoint_coords", return_value=None):
            result = scoring._location_status(WEEKDAY_AM_SCHEDULE, self._now())
        assert result["available"] is False
        assert "coords" in result["reason"]

    def test_no_location_pings(self):
        with patch.object(scoring.places, "endpoint_coords", return_value=WORK_COORDS), \
             patch.object(scoring.location_svc, "latest", return_value=None):
            result = scoring._location_status(WEEKDAY_AM_SCHEDULE, self._now())
        assert result["available"] is False

    def test_stale_location(self):
        stale_ts = (self._now() - timedelta(hours=2)).isoformat()
        rec = {"ts": stale_ts, "lat": 37.394, "lng": -122.076, "source": "owntracks"}
        with patch.object(scoring.places, "endpoint_coords", return_value=WORK_COORDS), \
             patch.object(scoring.location_svc, "latest", return_value=rec):
            result = scoring._location_status(WEEKDAY_AM_SCHEDULE, self._now())
        assert result["available"] is False
        assert "stale" in result["reason"]

    def test_at_destination(self):
        # Place operator at work (within 200m)
        fresh_ts = (self._now() - timedelta(minutes=5)).isoformat()
        rec = {"ts": fresh_ts, "lat": WORK_COORDS[0], "lng": WORK_COORDS[1], "source": "owntracks"}
        with patch.object(scoring.places, "endpoint_coords", return_value=WORK_COORDS), \
             patch.object(scoring.location_svc, "latest", return_value=rec):
            result = scoring._location_status(WEEKDAY_AM_SCHEDULE, self._now())
        assert result["available"] is True
        assert result["at_destination"] is True

    def test_not_at_destination(self):
        # Operator at home, destination is work
        fresh_ts = (self._now() - timedelta(minutes=5)).isoformat()
        rec = {"ts": fresh_ts, "lat": HOME_COORDS[0], "lng": HOME_COORDS[1], "source": "owntracks"}
        with patch.object(scoring.places, "endpoint_coords", return_value=WORK_COORDS), \
             patch.object(scoring.location_svc, "latest", return_value=rec):
            result = scoring._location_status(WEEKDAY_AM_SCHEDULE, self._now())
        assert result["available"] is True
        assert result["at_destination"] is False
        assert result["distance_m"] > 1000


# ---------------------------------------------------------------------------
# realtime_status_summary
# ---------------------------------------------------------------------------

EMPTY_STATE = {}
NEEDS_KEY_STATE = {
    "status": {
        "caltrain.gtfs_rt": {"status": "needs_key"},
        "sfmta.gtfs_rt": {},
    }
}


def _fresh_state(age_s: int = 60) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    return {
        "status": {
            "caltrain.gtfs_rt": {"status": "ok", "ts": ts},
            "sfmta.gtfs_rt": {"status": "ok", "ts": ts},
        }
    }


class TestRealtimeStatusSummary:
    def test_needs_key(self):
        with patch.object(scoring.poller, "read_state", return_value=NEEDS_KEY_STATE):
            result = scoring.realtime_status_summary()
        assert result["available"] is False
        assert result["reason"] == "needs_511_key"

    def test_never_polled(self):
        with patch.object(scoring.poller, "read_state", return_value=EMPTY_STATE):
            result = scoring.realtime_status_summary()
        assert result["available"] is False
        assert result["reason"] == "never_polled"

    def test_stale_data(self):
        state = _fresh_state(age_s=700)  # > 600s threshold
        with patch.object(scoring.poller, "read_state", return_value=state):
            result = scoring.realtime_status_summary()
        assert result["available"] is False
        assert result["reason"] == "stale"

    def test_fresh_data(self):
        state = _fresh_state(age_s=30)
        with patch.object(scoring.poller, "read_state", return_value=state):
            result = scoring.realtime_status_summary()
        assert result["available"] is True


# ---------------------------------------------------------------------------
# worst_rt_delay_min
# ---------------------------------------------------------------------------

class TestWorstRtDelayMin:
    def test_no_data_returns_zero(self):
        with patch.object(scoring.poller, "read_state", return_value={}):
            assert scoring.worst_rt_delay_min() == 0.0

    def test_picks_worst_caltrain_delay(self):
        # worst_rt_delay_min gates on realtime availability, so the state must
        # carry a fresh per-source status block or the gate zeroes the delay.
        state = {
            "caltrain": {
                "feeds": {
                    "trip_updates": {
                        "trips": [
                            {"stops": [{"arrival_delay": 120}]},  # 2 min
                            {"stops": [{"arrival_delay": 300}]},  # 5 min — worst
                        ]
                    }
                }
            },
            "status": _fresh_state(age_s=30)["status"],
        }
        with patch.object(scoring.poller, "read_state", return_value=state):
            result = scoring.worst_rt_delay_min()
        assert result == pytest.approx(5.0)

    def test_ignores_negative_delays(self):
        state = {
            "caltrain": {
                "feeds": {
                    "trip_updates": {
                        "trips": [{"stops": [{"arrival_delay": -60}]}]
                    }
                }
            },
            "status": _fresh_state(age_s=30)["status"],
        }
        with patch.object(scoring.poller, "read_state", return_value=state):
            assert scoring.worst_rt_delay_min() == 0.0


# ---------------------------------------------------------------------------
# evaluate — integration-level
# ---------------------------------------------------------------------------

def _patch_all(am_sch=None, pm_sch=None, legs=None, loc_rec=None,
               work_coords=None, home_coords=None, rt_state=None, alerts=None):
    """Context managers for all external I/O used by evaluate()."""
    def _sch_side_effect(direction, day):
        return am_sch if direction == "to-work" else pm_sch

    def _coords(name):
        return work_coords if name == "work" else home_coords

    return [
        patch.object(scoring.config, "get_schedule_for_day", side_effect=_sch_side_effect),
        patch.object(scoring.config, "get_legs_by_id", return_value=legs or []),
        patch.object(scoring.places, "endpoint_coords", side_effect=_coords),
        patch.object(scoring.location_svc, "latest", return_value=loc_rec),
        patch.object(scoring.poller, "read_state", return_value=rt_state or {}),
        patch.object(scoring.poller, "read_alerts", return_value=alerts or []),
    ]


class TestEvaluate:
    def test_no_active_schedule(self):
        with patch.object(scoring.config, "get_schedule_for_day", return_value=None), \
             patch.object(scoring.poller, "read_alerts", return_value=[]):
            result = scoring.evaluate(SAT_AM)
        assert result["active"] is False
        assert "no schedule" in result["reason"]

    def test_already_at_destination(self):
        fresh_ts = (MON_AM - timedelta(minutes=2)).isoformat()
        loc_rec = {"ts": fresh_ts, "lat": WORK_COORDS[0], "lng": WORK_COORDS[1], "source": "owntracks"}
        patches = _patch_all(
            am_sch=WEEKDAY_AM_SCHEDULE,
            legs=SAMPLE_LEGS,
            loc_rec=loc_rec,
            work_coords=WORK_COORDS,
            home_coords=HOME_COORDS,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = scoring.evaluate(MON_AM)
        assert result["active"] is False
        assert "already at" in result["reason"]

    def test_active_commute_on_track(self):
        # Operator at home, heading to work — no late risk with zero delay
        fresh_ts = (MON_AM - timedelta(minutes=2)).isoformat()
        loc_rec = {"ts": fresh_ts, "lat": HOME_COORDS[0], "lng": HOME_COORDS[1], "source": "owntracks"}
        patches = _patch_all(
            am_sch=WEEKDAY_AM_SCHEDULE,
            legs=SAMPLE_LEGS,
            loc_rec=loc_rec,
            work_coords=WORK_COORDS,
            home_coords=HOME_COORDS,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = scoring.evaluate(MON_AM)
        assert result["active"] is True
        assert result["direction"] == "to-work"
        assert "projected_arrive" in result
        assert "timeline" in result
        assert len(result["timeline"]) == len(SAMPLE_LEGS)

    def test_late_risk_with_large_delay(self):
        # Large enough RT delay to push past flex deadline
        fresh_ts = (MON_AM - timedelta(minutes=2)).isoformat()
        loc_rec = {"ts": fresh_ts, "lat": HOME_COORDS[0], "lng": HOME_COORDS[1], "source": "owntracks"}
        # Caltrain delay of 60 min — will blow past the 15-min flex window
        # Status timestamps must be fresh relative to _now_utc() (not MON_AM),
        # because realtime_status_summary() calls _now_utc() internally.
        _rt_now = datetime.now(timezone.utc)
        big_delay_state = {
            "caltrain": {
                "feeds": {
                    "trip_updates": {
                        "trips": [{"stops": [{"arrival_delay": 3600}]}]  # 60 min
                    }
                }
            },
            "sfmta": {"feeds": {"trip_updates": {"trips": []}}},
            "status": {
                "caltrain.gtfs_rt": {
                    "status": "ok",
                    "ts": (_rt_now - timedelta(seconds=30)).isoformat(),
                },
                "sfmta.gtfs_rt": {
                    "status": "ok",
                    "ts": (_rt_now - timedelta(seconds=30)).isoformat(),
                },
            },
        }
        patches = _patch_all(
            am_sch=WEEKDAY_AM_SCHEDULE,
            legs=SAMPLE_LEGS,
            loc_rec=loc_rec,
            work_coords=WORK_COORDS,
            home_coords=HOME_COORDS,
            rt_state=big_delay_state,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = scoring.evaluate(MON_AM)
        assert result["active"] is True
        assert result["late_risk"] is True
        assert result["late_by_min"] > 15

    def test_result_shape_keys_present(self):
        fresh_ts = (MON_AM - timedelta(minutes=2)).isoformat()
        loc_rec = {"ts": fresh_ts, "lat": HOME_COORDS[0], "lng": HOME_COORDS[1], "source": "owntracks"}
        patches = _patch_all(
            am_sch=WEEKDAY_AM_SCHEDULE,
            legs=SAMPLE_LEGS,
            loc_rec=loc_rec,
            work_coords=WORK_COORDS,
            home_coords=HOME_COORDS,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = scoring.evaluate(MON_AM)
        required_keys = {
            "ts", "ts_local", "active", "direction", "schedule_id",
            "target_arrive", "flex_minutes_late", "projected_arrive",
            "leave_by", "expected_leg_index", "timeline", "late_risk",
            "late_by_min", "alerts", "location",
        }
        assert required_keys.issubset(result.keys())
