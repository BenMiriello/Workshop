"""Tests for backend/services/commute/alerter.py.

Focus: the late-risk dispatch policy. In particular, the location-freshness
gate added 2026-06-03 after a false-positive alert fired with 11-day-stale
location data — see TODO.md and `maybe_alert` docstring.

We mock `scoring.evaluate`, `send_discord`, and all on-disk cooldown reads so
the tests run hermetically with no filesystem or network I/O.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# Backend modules are top-level imports (matches how main.py runs the app);
# add backend/ to sys.path so `services.commute.alerter` resolves regardless
# of which cwd pytest is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.commute import alerter  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _brief(*, active=True, late_risk=True, location_available=True,
           location_reason=None, schedule_id="weekday-am",
           expected_leg_step=1, late_by_min=20.0) -> dict[str, Any]:
    """Build a brief matching the shape `scoring.evaluate` returns."""
    loc: dict[str, Any]
    if location_available:
        loc = {
            "available": True,
            "at_destination": False,
            "distance_m": 5000.0,
            "source": "owntracks-bm",
            "age_s": 120,
        }
    else:
        loc = {
            "available": False,
            "reason": location_reason or "no location pings yet (set up Owntracks)",
        }
    return {
        "ts": "2026-06-03T15:20:00+00:00",
        "ts_local": "2026-06-03T08:20:00-07:00",
        "active": active,
        "schedule_id": schedule_id,
        "direction": "to-work",
        "target_arrive": "2026-06-03T15:30:00+00:00",
        "target_arrive_local": "8:30 AM",
        "flex_minutes_late": 15,
        "flex_deadline": "2026-06-03T15:45:00+00:00",
        "flex_deadline_local": "8:45 AM",
        "projected_arrive": "2026-06-03T15:50:00+00:00",
        "projected_arrive_local": "8:50 AM",
        "leave_by_local": "7:10 AM",
        "expected_leg": {"step": expected_leg_step, "mode": "caltrain", "from": "X", "to": "Y"},
        "late_risk": late_risk,
        "late_by_min": late_by_min,
        "location": loc,
        "rt_delay_min": 25.0,
        "realtime_status": {"available": True, "ages_s": {"caltrain": 30, "sfmta": 30}},
        "timeline": [],
    }


def _run(coro):
    """Synchronously execute an async coroutine. Uses asyncio.run() to avoid
    the deprecated implicit-event-loop path; safe because each test starts
    + tears down its own loop."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# location-freshness gate (the bug this commit fixes)
# ---------------------------------------------------------------------------

class TestLocationFreshnessGate:
    """A late-risk alert MUST NOT fire when we have no recent location ping.

    Background: on 2026-06-03 at 08:20 PT the commute alerter dispatched a
    "you're 28m late" alert despite Owntracks pings having been dead for 11
    days. The schedule + RT delay math alone produced a late_risk=True, and
    nothing downstream stopped the dispatch. This test prevents regression.
    """

    def test_stale_location_blocks_late_risk_dispatch(self):
        brief = _brief(late_risk=True, location_available=False,
                       location_reason="last ping 15840m ago — too stale to trust")
        with patch.object(alerter, "send_discord", new=AsyncMock(return_value={"ok": True})) as send, \
             patch.object(alerter, "_leg_on_cooldown", return_value=False), \
             patch.object(alerter, "_mark_leg_cooldown"), \
             patch.object(alerter, "_append_log"), \
             patch.object(alerter, "_queue_masini_notify"):
            result = _run(alerter.maybe_alert(brief))
        assert result["sent"] is False
        assert "location-stale gate" in result["reason"]
        send.assert_not_awaited()

    def test_no_location_signal_blocks_late_risk_dispatch(self):
        brief = _brief(late_risk=True, location_available=False,
                       location_reason="no location pings yet (set up Owntracks)")
        with patch.object(alerter, "send_discord", new=AsyncMock(return_value={"ok": True})) as send, \
             patch.object(alerter, "_leg_on_cooldown", return_value=False), \
             patch.object(alerter, "_mark_leg_cooldown"), \
             patch.object(alerter, "_append_log"), \
             patch.object(alerter, "_queue_masini_notify"):
            result = _run(alerter.maybe_alert(brief))
        assert result["sent"] is False
        assert "location-stale gate" in result["reason"]
        send.assert_not_awaited()

    def test_fresh_location_allows_late_risk_dispatch(self):
        brief = _brief(late_risk=True, location_available=True)
        with patch.object(alerter, "send_discord", new=AsyncMock(return_value={"ok": True})) as send, \
             patch.object(alerter, "_leg_on_cooldown", return_value=False), \
             patch.object(alerter, "_mark_leg_cooldown"), \
             patch.object(alerter, "_append_log"), \
             patch.object(alerter, "_queue_masini_notify"):
            result = _run(alerter.maybe_alert(brief))
        assert result["sent"] is True
        send.assert_awaited_once()

    def test_force_bypasses_location_gate(self):
        """emergency_research_triggers (line shutdown, etc.) must still get
        through even when location is stale — operator opts in to "tell me
        even if you can't see me."""
        brief = _brief(late_risk=False, location_available=False,
                       location_reason="last ping 12d ago")
        with patch.object(alerter, "send_discord", new=AsyncMock(return_value={"ok": True})) as send, \
             patch.object(alerter, "_leg_on_cooldown", return_value=False), \
             patch.object(alerter, "_mark_leg_cooldown"), \
             patch.object(alerter, "_append_log"), \
             patch.object(alerter, "_queue_masini_notify"):
            result = _run(alerter.maybe_alert(brief, force=True, reason="line_shutdown"))
        assert result["sent"] is True
        send.assert_awaited_once()


# ---------------------------------------------------------------------------
# Existing policy preserved
# ---------------------------------------------------------------------------

class TestExistingPolicyStillHolds:
    """Sanity checks that the new gate didn't accidentally widen or narrow
    the rest of the policy."""

    def test_no_active_schedule_returns_false(self):
        brief = _brief(active=False)
        with patch.object(alerter, "send_discord", new=AsyncMock(return_value={"ok": True})) as send:
            result = _run(alerter.maybe_alert(brief))
        assert result["sent"] is False
        assert "no active schedule" in result["reason"]
        send.assert_not_awaited()

    def test_not_late_risk_returns_false(self):
        brief = _brief(late_risk=False, location_available=True)
        with patch.object(alerter, "send_discord", new=AsyncMock(return_value={"ok": True})) as send:
            result = _run(alerter.maybe_alert(brief))
        assert result["sent"] is False
        assert "not late risk" in result["reason"]
        send.assert_not_awaited()

    def test_leg_cooldown_blocks_dispatch(self):
        brief = _brief(late_risk=True, location_available=True)
        with patch.object(alerter, "send_discord", new=AsyncMock(return_value={"ok": True})) as send, \
             patch.object(alerter, "_leg_on_cooldown", return_value=True):
            result = _run(alerter.maybe_alert(brief))
        assert result["sent"] is False
        assert "cooldown" in result["reason"]
        send.assert_not_awaited()


# ---------------------------------------------------------------------------
# _clean_reason — small helper, but the operator has reported "the fle" type
# mid-word truncations before. Keep these tests so regressions are caught.
# ---------------------------------------------------------------------------

class TestCleanReason:
    def test_strips_llm_prefix(self):
        assert alerter._clean_reason("LLM[during]: Train is late.") == "Train is late."

    def test_completes_to_last_sentence(self):
        out = alerter._clean_reason("LLM[during]: Foo. Bar partial of the fle")
        assert out == "Foo."

    def test_trims_dangling_partial_word(self):
        out = alerter._clean_reason("Major delays detected on the fle")
        # No period exists, so we trim the last partial word and add ellipsis.
        assert out.endswith("…")
        assert "the fle" not in out

    def test_empty_input_returns_empty(self):
        assert alerter._clean_reason("") == ""


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
