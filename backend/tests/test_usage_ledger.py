"""Tests for the usage ledger accounting + weighted max-min budget allocator."""
from datetime import datetime, timedelta, timezone

import pytest

from services import usage_ledger as ul


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(ul, "LEDGER_PATH", tmp_path / "usage.jsonl")
    monkeypatch.setattr(ul, "BUDGET_CONFIG_PATH", tmp_path / "usage-budget.json")
    # can_admit reads the Jobs store for the in-flight reserve (F8a); isolate it to
    # an empty temp log so admission tests never depend on live sweeps.
    from services import jobs
    monkeypatch.setattr(jobs, "JOBS_LOG", tmp_path / "jobs.jsonl")
    return ul


def _iso(dt):
    return dt.isoformat()


# --- Accounting -------------------------------------------------------------

def test_window_usage_sums_only_in_window(ledger):
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    # In window (< 5h old)
    ledger.record("alpha", 100, ts=_iso(now - timedelta(hours=1)))
    ledger.record("alpha", 50, ts=_iso(now - timedelta(hours=2)))
    ledger.record("beta", 30, ts=_iso(now - timedelta(minutes=30)))
    # Out of window (> 5h old) -> excluded
    ledger.record("alpha", 9999, ts=_iso(now - timedelta(hours=6)))

    u = ledger.window_usage(now=now)
    assert u["app"] == 180
    assert u["by_project"] == {"alpha": 150, "beta": 30}


def test_chat_rows_are_accounted_but_not_admitted(ledger):
    """Chat spend is REPORTED and is excluded from ADMISSION.

    This is the bug that stopped the automated loop: chat turns write role="chat"
    rows to the same ledger, admission summed the window with no role filter, and
    the operator's own conversation refused every autonomous sweep. Live at the
    time: 19.7M chat tokens against a 3.25M autonomous ceiling, so `admit` said no
    to everything. A ledger full of chat rows must never refuse a worker."""
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    ledger.update_config(app_total=1_000_000, autonomy_budget_fraction=1.0)
    # One conversation, wildly over the whole app budget.
    for _ in range(20):
        ledger.record("workshop", 500_000, role="chat", chat="thread-1",
                      ts=_iso(now - timedelta(minutes=10)))

    # Reporting still sees every token.
    assert ledger.window_usage(now=now)["app"] == 10_000_000
    assert ledger.status(now=now)["app_used"] == 10_000_000
    # Admission does not.
    assert ledger.window_usage(
        now=now, exclude_roles=ledger.ADMISSION_EXCLUDED_ROLES)["app"] == 0
    ok, reason = ledger.can_admit("workshop", now=now)
    assert ok, reason


def test_worker_rows_still_gate_admission(ledger):
    """Excluding chat must not make admission toothless: real autonomous spend
    still refuses, or the exclusion would have traded one lie for another."""
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    ledger.update_config(app_total=1_000_000, autonomy_budget_fraction=1.0)
    ledger.record("workshop", 900_000, role="worker", ts=_iso(now - timedelta(minutes=5)))
    ledger.record("workshop", 5_000_000, role="chat", ts=_iso(now - timedelta(minutes=5)))
    ok, reason = ledger.can_admit("workshop", now=now)
    assert not ok
    # The reason states the real numbers, not the reserved fraction. The old
    # string said "autonomous budget 65% used", where 65 was the config fraction.
    assert "900000" in reason and "1000000" in reason
    assert "65%" not in reason and "%" not in reason
    assert "—" not in reason


def test_a_row_with_no_role_still_counts_toward_admission(ledger):
    """Ledger rows predating the role field are autonomous spend; dropping them
    from admission would silently uncap every historical sweep."""
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    ledger.update_config(app_total=1_000_000, autonomy_budget_fraction=1.0)
    ledger.record("workshop", 900_000, ts=_iso(now - timedelta(minutes=5)))
    ok, _ = ledger.can_admit("workshop", now=now)
    assert not ok


def test_prune_drops_old_entries(ledger):
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    ledger.record("alpha", 10, ts=_iso(now - timedelta(hours=1)))
    ledger.record("alpha", 10, ts=_iso(now - timedelta(hours=9)))
    removed = ledger.prune(now=now)
    assert removed == 1
    assert ledger.window_usage(now=now)["app"] == 10


# --- Allocation (weighted max-min progressive fill) -------------------------

def test_weighted_share_no_floor_no_ceil(ledger):
    cfg = {
        "app_total": 400,
        "default_project": {"weight": 1.0, "floor": 0, "ceil": None},
        "projects": {"a": {"weight": 1.0}, "b": {"weight": 3.0}},
    }
    b = ledger.plan_budgets({}, ["a", "b"], cfg)
    # 1:3 weight over a 400 pool -> 100 / 300
    assert round(b["a"]) == 100
    assert round(b["b"]) == 300


def test_ceil_caps_and_leaves_pool_unused(ledger):
    cfg = {
        "app_total": 400,
        "default_project": {"weight": 1.0, "floor": 0, "ceil": None},
        "projects": {"a": {"weight": 1.0, "ceil": 50}, "b": {"weight": 1.0, "ceil": 50}},
    }
    b = ledger.plan_budgets({}, ["a", "b"], cfg)
    # Both capped at 50; the remaining 300 of the pool is genuinely idle.
    assert round(b["a"]) == 50 and round(b["b"]) == 50


def test_floor_guaranteed_then_share_remainder(ledger):
    cfg = {
        "app_total": 300,
        "default_project": {"weight": 1.0, "floor": 0, "ceil": None},
        "projects": {"a": {"weight": 1.0, "floor": 100}, "b": {"weight": 1.0, "floor": 0}},
    }
    b = ledger.plan_budgets({}, ["a", "b"], cfg)
    # a floor 100 reserved; remaining pool 200 split 1:1 -> a=100+100, b=100
    assert round(b["a"]) == 200
    assert round(b["b"]) == 100


def test_idle_project_floor_is_reserved_not_lent(ledger):
    # b is NOT demanding; its floor is still reserved out of the pool.
    cfg = {
        "app_total": 300,
        "default_project": {"weight": 1.0, "floor": 0, "ceil": None},
        "projects": {"a": {"weight": 1.0}, "b": {"weight": 1.0, "floor": 100}},
    }
    b = ledger.plan_budgets({}, ["a"], cfg)
    # pool = 300 - 100 (b's reserved floor) = 200, all to the sole demander a.
    assert round(b["a"]) == 200


def test_proportional_scaledown_when_floors_oversubscribed(ledger):
    cfg = {
        "app_total": 100,
        "default_project": {"weight": 1.0, "floor": 0, "ceil": None},
        "projects": {"a": {"floor": 90}, "b": {"floor": 90}},
    }
    b = ledger.plan_budgets({}, ["a", "b"], cfg)
    # floors sum 180 > 100 -> scaled by 100/180; each ~50, total == app_total.
    assert round(b["a"]) == 50 and round(b["b"]) == 50
    assert round(b["a"] + b["b"]) == 100


def test_status_reports_used_and_remaining(ledger):
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    monkey_cfg = {
        "app_total": 400,
        "default_project": {"weight": 1.0, "floor": 0, "ceil": None},
        "projects": {"a": {"weight": 1.0}, "b": {"weight": 1.0}},
    }
    ul.BUDGET_CONFIG_PATH.write_text(__import__("json").dumps(monkey_cfg))
    ledger.record("a", 120, ts=_iso(now - timedelta(hours=1)))
    st = ledger.status(now=now)
    assert st["app_used"] == 120
    assert st["app_remaining"] == 280
    # a used 120 of its 200 allowance -> 80 remaining.
    assert st["projects"]["a"]["used"] == 120
    assert st["projects"]["a"]["remaining"] == 80


# --- Admission enforces the allocation (review F10) --------------------------

def test_can_admit_denies_when_scope_over_allowance(ledger):
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    cfg = {
        "app_total": 400,
        "nominal_task_tokens": 10,
        "default_project": {"weight": 1.0, "floor": 0, "ceil": None},
        "projects": {"a": {"weight": 1.0}, "b": {"weight": 1.0}},
    }
    ul.BUDGET_CONFIG_PATH.write_text(__import__("json").dumps(cfg))
    # With a+b as demanders each gets a 200 allowance. a has spent 210 (over its
    # share) while the app-wide total (210 < 400 - 10 reserve) still has room, so
    # only the per-scope allocation should deny.
    ledger.record("a", 210, ts=_iso(now - timedelta(hours=1)))
    ok_a, reason_a = ul.can_admit("a", now=now)
    assert ok_a is False and "allocation" in reason_a
    # b has spent nothing -> still admissible under its own 200 allowance.
    ok_b, _ = ul.can_admit("b", now=now)
    assert ok_b is True


def test_can_admit_default_config_only_app_cap_gates(ledger):
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    # fraction 1.0 isolates this test to the app-cap-only path (its intent);
    # the interactive-headroom reserve is exercised by its own test.
    cfg = {"app_total": 400, "nominal_task_tokens": 10, "projects": {},
           "autonomy_budget_fraction": 1.0}
    ul.BUDGET_CONFIG_PATH.write_text(__import__("json").dumps(cfg))
    # Default weights/floors: a lone scope's allowance == app_total, so a scope
    # well under the app cap is admitted (per-scope allocation does not bite).
    ledger.record("solo", 300, ts=_iso(now - timedelta(hours=1)))
    ok, _ = ul.can_admit("solo", now=now)
    assert ok is True


def test_can_admit_reserves_interactive_headroom(ledger):
    # With the default 0.65 fraction, autonomous work is admitted only under the
    # reserved ceiling (app_total * fraction), leaving room for operator chats.
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    cfg = {"app_total": 1000, "nominal_task_tokens": 10, "projects": {},
           "autonomy_budget_fraction": 0.65}
    ul.BUDGET_CONFIG_PATH.write_text(__import__("json").dumps(cfg))
    ledger.record("solo", 500, ts=_iso(now - timedelta(hours=1)))
    ok, _ = ul.can_admit("solo", now=now)
    assert ok is True  # 500 < 650 ceiling
    ledger.record("solo", 200, ts=_iso(now - timedelta(minutes=30)))
    ok, reason = ul.can_admit("solo", now=now)
    # 700 > 650 ceiling. The refusal states the real numbers: it used to print
    # the reserved FRACTION ("autonomous budget 65% used"), which is a config
    # value, not usage, and told the operator nothing about why it stopped.
    assert ok is False
    assert "700" in reason and "650" in reason
