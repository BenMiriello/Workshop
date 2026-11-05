"""The usage window the CLI reports, and the queue waiting it out.

Pins: the structured event and the prose fallback both yield a reset time; a
drained turn on the subscription waits while the window is exhausted and says
why; Send now overrides that; a turn on another provider never waits; a turn
that hits the limit arms one carry-on wake-up for after the reset.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services import (chat_drain, chat_queue, ownership, turn_admission,
                      usage_limits, wakeups)

LA = ZoneInfo("America/Los_Angeles")


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield tmp_path
    finally:
        ownership.clear_current_owner()


def _at(*a) -> float:
    return datetime(*a, tzinfo=LA).timestamp()


def test_reset_text_means_the_next_such_moment():
    now = _at(2026, 10, 24, 23, 0)
    assert usage_limits.parse_reset_text(
        "You've hit your session limit · resets 1:50am (America/Los_Angeles)",
        now=now) == _at(2026, 10, 25, 1, 50)
    assert usage_limits.parse_reset_text(
        "You've hit your session limit · resets 11:30pm (America/Los_Angeles)",
        now=now) == _at(2026, 10, 24, 23, 30)
    assert usage_limits.parse_reset_text(
        "You've hit your weekly limit · resets Oct 31, 11pm (America/Los_Angeles)",
        now=now) == _at(2026, 10, 31, 23, 0)
    assert usage_limits.parse_reset_text("no limit here", now=now) is None


def test_the_structured_event_records_every_window(owned):
    line = json.dumps({"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "resetsAt": 1792879200, "rateLimitType": "five_hour",
        "unifiedWindows": {
            "five_hour": {"utilization": 0.3, "resetsAt": 1792879200, "status": "allowed"},
            "seven_day": {"utilization": 0.05, "resetsAt": 1793426400, "status": "allowed"}}}})
    assert usage_limits.observe_line(line) is None
    w = usage_limits.read()["windows"]
    assert w["five_hour"]["resets_at"] == 1792879200
    assert w["seven_day"]["utilization"] == 0.05
    assert usage_limits.next_reset(now=1792870000) == 1792879200 + usage_limits.RESET_MARGIN_S


def test_a_rejected_event_blocks_until_its_reset(owned):
    line = json.dumps({"type": "rate_limit_event", "rate_limit_info": {
        "status": "rejected", "resetsAt": 2000000000, "rateLimitType": "five_hour"}})
    assert usage_limits.observe_line(line) == 2000000000
    assert usage_limits.blocked_until(now=1999999000) == 2000000000 + usage_limits.RESET_MARGIN_S
    assert usage_limits.blocked_until(now=2000000100) is None


def test_the_synthetic_limit_reply_blocks_and_arms_one_carry_on(owned, monkeypatch):
    tid = "a" * 32
    line = json.dumps({"type": "assistant", "message": {"model": "<synthetic>", "content": [
        {"type": "text", "text": "You've hit your session limit · resets 9:40pm (America/Los_Angeles)"}]}})
    hit = usage_limits.observe_line(line)
    assert hit and usage_limits.blocked_until()
    turn_admission.carry_on_after_limit(tid, hit)
    turn_admission.carry_on_after_limit(tid, hit + 60)
    armed = wakeups.read(tid)
    assert len(armed) == 1
    assert armed[0]["seed"].startswith("[USAGE LIMIT RESET")


def test_a_later_allowed_turn_clears_a_stale_block(owned):
    usage_limits.observe_reply(
        "You've hit your session limit · resets 9:40pm (America/Los_Angeles)")
    assert usage_limits.blocked_until()
    usage_limits.observe_event({"status": "allowed", "rateLimitType": "five_hour",
                                "resetsAt": 1})
    assert usage_limits.blocked_until() is None


def _drain_with(monkeypatch, tid, *, on_subscription=True, force=False):
    started = []

    async def starter(thread_id, batch):
        started.append([e["client_id"] for e in batch])
        return True

    monkeypatch.setattr(chat_drain, "_starter", starter)
    monkeypatch.setattr(chat_drain.chat_store, "active_turn_id", lambda t: None)
    monkeypatch.setattr(chat_drain.chat_store, "get_thread", lambda t: {"id": t})
    monkeypatch.setattr(turn_admission, "on_subscription", lambda m: on_subscription)
    ok = asyncio.run(chat_drain.drain(tid, force=force))
    return ok, started


def test_a_queued_turn_waits_out_an_exhausted_window(owned, monkeypatch):
    tid = "b" * 32
    usage_limits.observe_event({"status": "rejected", "resetsAt": 4000000000,
                                "rateLimitType": "five_hour"})
    chat_queue.enqueue(tid, text="hello", client_id="c1")
    ok, started = _drain_with(monkeypatch, tid)
    assert not ok and not started
    waiting = chat_queue.state(tid)["queue_waiting"]
    assert waiting["reason"] == "usage_limit" and waiting["until"]

    ok, started = _drain_with(monkeypatch, tid, force=True)
    assert ok and started == [["c1"]]


def test_another_provider_is_not_held_by_the_window(owned, monkeypatch):
    tid = "c" * 32
    usage_limits.observe_event({"status": "rejected", "resetsAt": 4000000000,
                                "rateLimitType": "five_hour"})
    chat_queue.enqueue(tid, text="hello", client_id="c1")
    ok, started = _drain_with(monkeypatch, tid, on_subscription=False)
    assert ok and started == [["c1"]]
