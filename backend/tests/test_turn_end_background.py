"""A turn does not retire while a background subagent is still working.

WHY THIS EXISTS, measured rather than reasoned. On 2026-09-01 the real CLI was
driven with this driver's own flags and told to launch a background subagent:

    [15.4s] tool_use Agent run_in_background=true
    [15.4s] system task_started
    [18.8s] result #1                     <- the parent finished speaking here
    [47.6s] the subagent's answer arrives, on the SAME process
    [47.7s] system task_notification
    [59.3s] the model answers the notification
    [59.3s] result #2

Retiring on result #1 SIGTERMs the process GROUP, and the subagent is in it, so
it was killed 29 seconds before it would have spoken. For a year the platform
recorded this as "background subagents do not work in this deployment" and
enforced a PreToolUse hook denying them. The CLI was never the problem; this
retirement rule was. The hook is deleted and these tests are what keep the rule
honest.

The counts come off the CLI's own `system` subtypes: `task_started` on launch,
`task_notification` when it reports.
"""
import pytest

from services import turn_end
from services.turn_end import TurnProgress, turn_is_over


def _balanced(**over):
    """A turn whose counts all agree and which has spoken: the ordinary shape
    that retires immediately. Each test perturbs one thing."""
    base = dict(delivered=1, replayed=1, results=1, produced=True,
                is_command=False, idle_s=0.0)
    base.update(over)
    return TurnProgress(**base)


def test_a_balanced_turn_still_retires_at_once():
    """The control. Nothing here may slow down an ordinary turn."""
    assert turn_is_over(_balanced()) is True


def test_a_pending_background_subagent_holds_the_turn_open():
    """The exact moment above: result #1 has landed, the subagent has not
    reported, and the balance says the turn is done. It is not."""
    assert turn_is_over(_balanced(background_pending=1)) is False


def test_it_retires_once_the_subagent_reports():
    """`task_notification` brings the count back to zero and the turn ends
    normally."""
    assert turn_is_over(_balanced(background_pending=0)) is True


def test_several_outstanding_subagents_all_have_to_report():
    p = _balanced(background_pending=3)
    assert turn_is_over(p) is False
    assert turn_is_over(_balanced(background_pending=1)) is False
    assert turn_is_over(_balanced(background_pending=0)) is True


def test_stop_still_ends_the_turn_immediately():
    """The operator's decision outranks anything the turn is owed. Without this
    a long background task would make Stop feel broken."""
    assert turn_is_over(_balanced(background_pending=2, stopped=True)) is True


def test_a_subagent_that_never_reports_is_bounded_by_silence():
    """The backstop. A background task that dies without reporting must not hold
    the thread forever, so total silence ends the turn anyway."""
    assert turn_is_over(_balanced(background_pending=1,
                                  idle_s=turn_end.BACKGROUND_GRACE_S - 1)) is False
    assert turn_is_over(_balanced(background_pending=1,
                                  idle_s=turn_end.BACKGROUND_GRACE_S)) is True


def test_the_background_bound_is_far_longer_than_the_ordinary_one():
    """60s is the ordinary wedged-turn bound and it would kill exactly the case
    this supports: the measured run went 29 seconds between the parent's result
    and the subagent's first output, and a real research task runs for minutes."""
    assert turn_end.BACKGROUND_GRACE_S > turn_end.STUCK_GRACE_S
    assert turn_end.BACKGROUND_GRACE_S >= 300


def test_a_pending_subagent_outranks_the_ordinary_silence_bound():
    """A turn holding a background task must not be retired at STUCK_GRACE_S.
    This is the regression that would quietly restore the old behaviour: the
    turn would look 'wedged' after a minute of the subagent thinking."""
    p = _balanced(background_pending=1, idle_s=turn_end.STUCK_GRACE_S + 1)
    assert turn_is_over(p) is False


def test_it_defaults_to_zero_so_an_untracked_caller_is_unchanged():
    """The field is optional. A caller that does not count background tasks gets
    exactly the previous behaviour rather than a turn that never retires."""
    p = TurnProgress(delivered=1, replayed=1, results=1, produced=True,
                     is_command=False, idle_s=0.0)
    assert p.background_pending == 0
    assert turn_is_over(p) is True


@pytest.mark.parametrize("pending", [-1, -5])
def test_a_negative_count_never_holds_the_turn(pending):
    """The driver clamps at zero, because a `task_notification` can belong to a
    task started by a PREVIOUS process (the resume case). Should one reach here
    anyway, it must not read as 'nothing pending' in a way that differs from
    zero, nor hold the turn."""
    assert turn_is_over(_balanced(background_pending=pending)) is True


# --- the wiring, so the counts cannot silently stop being fed ----------------
#
# `turn_end` is pure arithmetic; it is only correct if the driver actually
# increments those counts. These pin the seam between the CLI's vocabulary and
# ours. Both subtypes below were observed on the real stream (see this module's
# header), so a rename on either side fails here rather than quietly restoring
# "background subagents do not work".

def test_the_driver_watches_exactly_the_two_lifecycle_events():
    from services.build_streaming import _BG_TASK_EVENTS

    assert _BG_TASK_EVENTS == {"task_started", "task_notification"}


def test_those_events_survive_the_stream_parser_with_their_subtype():
    """The driver reads `("meta", {"subtype": ...})`. If the parser stopped
    carrying the subtype, the counts would sit at zero and every background
    subagent would be killed again, with nothing failing."""
    import json

    from services.build_streaming import _BG_TASK_EVENTS, _parse_stream_event

    for subtype in _BG_TASK_EVENTS:
        kind, payload = _parse_stream_event(
            json.dumps({"type": "system", "subtype": subtype}))
        assert kind == "meta"
        assert payload["subtype"] == subtype


def test_the_other_background_chatter_is_not_counted():
    """`task_updated` and `background_tasks_changed` ride along with the two that
    matter. Counting them would double-count a single subagent and hold the turn
    open forever."""
    from services.build_streaming import _BG_TASK_EVENTS

    assert "task_updated" not in _BG_TASK_EVENTS
    assert "background_tasks_changed" not in _BG_TASK_EVENTS


# BY ID, NOT BY COUNT. Turn 00c1a114 (2026-09-25) resumed a session whose
# previous process had left eight agents behind. The CLI delivered a
# notification for each before the new turn's own work, the old
# started-minus-reported count absorbed them, and it read zero pending while
# this turn's agents were running. The turn was retired mid-run.

def _event(subtype, task_id):
    return {"subtype": subtype, "task_id": task_id}


def test_a_notification_for_a_task_this_process_never_started_is_ignored():
    from services.build_streaming import _track_bg_task

    live: set = set()
    for tid in ("old1", "old2", "old3"):
        _track_bg_task(live, _event("task_notification", tid))
    _track_bg_task(live, _event("task_started", "mine"))

    assert live == {"mine"}


def test_a_task_resumed_after_it_reported_is_pending_again():
    from services.build_streaming import _track_bg_task

    live: set = set()
    _track_bg_task(live, _event("task_started", "a"))
    _track_bg_task(live, _event("task_notification", "a"))
    _track_bg_task(live, _event("task_started", "a"))

    assert live == {"a"}


def test_the_parser_carries_the_task_id():
    import json

    from services.build_streaming import _BG_TASK_EVENTS, _parse_stream_event

    for subtype in _BG_TASK_EVENTS:
        _kind, payload = _parse_stream_event(
            json.dumps({"type": "system", "subtype": subtype, "task_id": "t1"}))
        assert payload["task_id"] == "t1"


def test_an_adopted_turn_remembers_the_tasks_running_before_its_offset(tmp_path):
    """A restart adopts the turn at a journal offset instead of replaying it, so
    what was running before that point has to be read back, or the next result
    retires the turn and kills its agents."""
    import json

    from services.build_streaming import _bg_tasks_before

    lines = [
        {"type": "system", "subtype": "task_notification", "task_id": "stale"},
        {"type": "system", "subtype": "task_started", "task_id": "a"},
        {"type": "system", "subtype": "task_started", "task_id": "b"},
        {"type": "system", "subtype": "task_notification", "task_id": "a"},
    ]
    head = "".join(json.dumps(x) + "\n" for x in lines).encode()
    tail = (json.dumps({"type": "system", "subtype": "task_notification",
                        "task_id": "b"}) + "\n").encode()
    journal = tmp_path / "stream.jsonl"
    journal.write_bytes(head + tail)

    assert _bg_tasks_before(journal, len(head)) == {"b"}
    assert _bg_tasks_before(journal, 0) == set()
