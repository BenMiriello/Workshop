"""A resumed `/compact` turn is not retired while the CLI is compacting.

Turns 71a6a962 (2026-09-23) and b440dc1a (2026-09-26), both `/compact` on a
resumed session: the CLI first answered a leftover task notification (a foreign
result, before our prompt was read), then reported `status: compacting` and went
silent. A compaction of that session takes ~75s; the silence bound retired the
turn at 60s, so no boundary landed and the chat showed the local-command fallback.
"""
from services import build_streaming
from services.turn_end import (COMPACT_GRACE_S, STUCK_GRACE_S, TurnProgress,
                               turn_is_over)


def _resumed_compact(**over):
    """Our `/compact` delivered, never echoed, one foreign result seen."""
    base = dict(delivered=1, replayed=0, results=0, produced=False,
                is_command=True, idle_s=STUCK_GRACE_S + 1)
    base.update(over)
    return TurnProgress(**base)


def test_the_measured_failure_retires_without_the_flag():
    assert turn_is_over(_resumed_compact()) is True


def test_compacting_holds_the_turn_past_the_silence_bound():
    assert turn_is_over(_resumed_compact(compacting=True)) is False


def test_compacting_is_still_bounded():
    assert turn_is_over(_resumed_compact(
        compacting=True, idle_s=COMPACT_GRACE_S)) is True


def test_stop_ends_a_compacting_turn_at_once():
    assert turn_is_over(_resumed_compact(compacting=True, stopped=True)) is True


def test_the_parser_carries_the_status():
    line = '{"type":"system","subtype":"status","status":"compacting"}'
    assert build_streaming._parse_stream_event(line) == (
        "meta", {"subtype": "status", "status": "compacting"})
