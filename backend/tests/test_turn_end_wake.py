"""A turn is not over while a background task's report is still to be read.

The CLI hands a finished background task back to the model in two steps. It
writes `task_notification`, and only afterwards echoes the report as a user
message and starts a request to answer it. The driver evaluates the end of the
turn on the pass that reads the notification. If the turn had already said its
piece (the agent ended on "I'll report when it finishes"), that pass saw
nothing pending, nothing produced since the last result and the counts in
balance, and retired the process before the report was read. The report then sat
queued until the next human message.

The same happens when the task finishes BEFORE the model's closing message: the
report is queued behind the request in progress and delivered after its result.

These tests pin the rule (`wakes_owed`), the classification of which tasks come
back as a prompt, and the replay of both orders through the parser.
"""
import json

from services import turn_end
from services.build_streaming import (
    _bg_tasks_before,
    _new_wake,
    _parse_stream_event,
    _track_bg_task,
    _track_wake,
)
from services.turn_end import TurnProgress, turn_is_over


def _balanced(**over):
    base = dict(delivered=1, replayed=1, results=1, produced=True,
                is_command=False, idle_s=0.0)
    base.update(over)
    return TurnProgress(**base)


# --- the rule ----------------------------------------------------------------

def test_an_owed_report_holds_a_turn_that_otherwise_reads_as_done():
    assert turn_is_over(_balanced()) is True
    assert turn_is_over(_balanced(wakes_owed=1)) is False


def test_an_owed_report_is_bounded_by_the_ordinary_silence_bound():
    """A report the CLI never echoes must cost a minute, not the thread."""
    assert turn_is_over(_balanced(wakes_owed=1,
                                  idle_s=turn_end.STUCK_GRACE_S - 1)) is False
    assert turn_is_over(_balanced(wakes_owed=1,
                                  idle_s=turn_end.STUCK_GRACE_S)) is True


def test_stop_outranks_an_owed_report():
    assert turn_is_over(_balanced(wakes_owed=2, stopped=True)) is True


def test_it_defaults_to_zero_so_an_untracked_caller_is_unchanged():
    p = TurnProgress(delivered=1, replayed=1, results=1, produced=True,
                     is_command=False, idle_s=0.0)
    assert p.wakes_owed == 0
    assert turn_is_over(p) is True


# --- which tasks come back as a prompt ---------------------------------------

def _started(tid, backgrounded, subagent_owned=False):
    return {"subtype": "task_started", "task_id": tid,
            "backgrounded": backgrounded, "subagent_owned": subagent_owned}


def _notified(tid):
    return {"subtype": "task_notification", "task_id": tid}


def test_a_task_the_turn_backgrounded_is_owed_from_its_notification_to_its_echo():
    wake = _new_wake()
    _track_wake(wake, "meta", _started("t1", backgrounded=True))
    assert wake["owed"] == set()
    _track_wake(wake, "meta", _notified("t1"))
    assert wake["owed"] == {"t1"}
    _track_wake(wake, "user_echo", {"task": "t1"})
    assert wake["owed"] == set()


def test_a_foreground_command_reports_through_its_tool_result_and_owes_nothing():
    wake = _new_wake()
    _track_wake(wake, "meta", _started("fg", backgrounded=False))
    _track_wake(wake, "meta", _notified("fg"))
    assert wake["owed"] == set()


def test_a_subagents_own_command_reports_to_the_subagent_and_owes_nothing():
    wake = _new_wake()
    for tid, bg in (("s1", True), ("s2", False)):
        _track_wake(wake, "meta", _started(tid, backgrounded=bg, subagent_owned=True))
        _track_wake(wake, "meta", _notified(tid))
    assert wake["owed"] == set()


def test_a_notification_for_a_task_this_process_never_started_owes_nothing():
    """A resumed session re-announces the previous process's tasks."""
    wake = _new_wake()
    _track_wake(wake, "meta", _notified("old"))
    assert wake["owed"] == set()


def test_an_unrelated_echo_does_not_clear_an_owed_report():
    wake = _new_wake()
    _track_wake(wake, "meta", _started("t1", backgrounded=True))
    _track_wake(wake, "meta", _notified("t1"))
    _track_wake(wake, "user_echo", {"task": None})
    _track_wake(wake, "user_echo", {"task": "other"})
    assert wake["owed"] == {"t1"}


# --- the parser carries what the tracker needs --------------------------------

def test_the_parser_carries_the_background_and_ownership_flags():
    _k, p = _parse_stream_event(json.dumps({
        "type": "system", "subtype": "task_started", "task_id": "t1",
        "is_backgrounded": True, "owned_by_subagent": False}))
    assert (p["backgrounded"], p["subagent_owned"]) == (True, False)
    _k, p = _parse_stream_event(json.dumps({
        "type": "system", "subtype": "task_started", "task_id": "t2"}))
    assert (p["backgrounded"], p["subagent_owned"]) == (False, False)


def _notification_echo(tid):
    return {"type": "user", "isReplay": True, "parent_tool_use_id": None,
            "origin": {"kind": "task-notification"},
            "message": {"role": "user", "content": (
                f"<task-notification>\n<task-id>{tid}</task-id>\n"
                "<status>completed</status>\n</task-notification>")}}


def _peer_echo(tid):
    return {"type": "user", "isReplay": True, "isSynthetic": True,
            "parent_tool_use_id": None,
            "origin": {"kind": "peer", "senderTaskId": tid},
            "message": {"role": "user", "content": [
                {"type": "text", "text": "A report from a subagent."}]}}


def test_the_parser_names_the_task_a_report_echo_delivers():
    for ev in (_notification_echo("t1"), _peer_echo("t1")):
        kind, p = _parse_stream_event(json.dumps(ev))
        assert kind == "user_echo"
        assert p["task"] == "t1"


def test_an_ordinary_echo_names_no_task():
    kind, p = _parse_stream_event(json.dumps({
        "type": "user", "isReplay": True, "parent_tool_use_id": None,
        "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}}))
    assert kind == "user_echo"
    assert p["task"] is None


# --- both orders, replayed through the parser ---------------------------------

def _replay(events):
    """Feed CLI events through the parser and trackers the way the driver
    does, and answer `turn_is_over` after each one that follows a result."""
    live, wake = set(), _new_wake()
    replays = results = 0
    answers = []
    for ev in events:
        kind, p = _parse_stream_event(json.dumps(ev))
        if kind == "meta" and p.get("subtype") in ("task_started", "task_notification"):
            _track_bg_task(live, p)
            _track_wake(wake, kind, p)
        elif kind == "meta" and p.get("subtype") == "init":
            _track_wake(wake, kind, p)
        elif kind == "user_echo" and p.get("parent") is None:
            replays += 1
            _track_wake(wake, kind, p)
        elif kind == "result":
            results += 1
            _track_wake(wake, kind, p)
        if results:
            answers.append(turn_is_over(TurnProgress(
                delivered=1, replayed=replays, results=results, produced=True,
                is_command=False, idle_s=0.0,
                background_pending=len(live), wakes_owed=len(wake["owed"]))))
    return answers


_PROMPT_ECHO = {"type": "user", "isReplay": True, "parent_tool_use_id": None,
                "message": {"role": "user", "content": [
                    {"type": "text", "text": "the operator's message"}]}}
_STARTED = {"type": "system", "subtype": "task_started", "task_id": "t1",
            "is_backgrounded": True}
_NOTIFIED = {"type": "system", "subtype": "task_notification", "task_id": "t1",
             "status": "completed"}
_RESULT = {"type": "result", "subtype": "success"}
_INIT = {"type": "system", "subtype": "init"}
_REPORT_RESULT = {"type": "result", "subtype": "success",
                  "origin": {"kind": "task-notification"}}
_ANSWER = {"type": "assistant", "message": {"role": "assistant", "content": [
    {"type": "text", "text": "the report says"}]}}


def test_the_last_report_after_the_closing_message_is_answered_before_retiring():
    """The agent ends on "I'll report when it finishes"; the task reports."""
    answers = _replay([_PROMPT_ECHO, _STARTED, _RESULT, _NOTIFIED,
                       _notification_echo("t1"), _RESULT])
    # held at the first result (pending), at the notification (owed), and at
    # the echo (the answer's result has not landed); over once it has.
    assert answers == [False, False, False, True]


def test_a_report_that_lands_before_the_closing_message_is_still_delivered():
    """The task finishes while the model is still writing; the CLI queues the
    report behind that request and delivers it after the result."""
    answers = _replay([_PROMPT_ECHO, _STARTED, _NOTIFIED, _RESULT,
                       _notification_echo("t1"), _RESULT])
    assert answers == [False, False, True]


def test_a_restart_remembers_a_report_announced_and_not_yet_delivered(tmp_path):
    """A turn adopted at a journal offset rebuilds what it was owed."""
    head_events = [_PROMPT_ECHO, _STARTED, _RESULT, _NOTIFIED]
    head = "".join(json.dumps(e) + "\n" for e in head_events).encode()
    journal = tmp_path / "stream.jsonl"
    journal.write_bytes(head + (json.dumps(_notification_echo("t1")) + "\n").encode())

    wake = _new_wake()
    assert _bg_tasks_before(journal, len(head), wake) == set()
    assert wake["owed"] == {"t1"}

    wake = _new_wake()
    _bg_tasks_before(journal, journal.stat().st_size, wake)
    assert wake["owed"] == set()


def test_on_an_idle_session_the_report_starts_its_own_request():
    """The shape recorded from the real CLI: no echo. The report starts a fresh
    request (`init`) and that request's result carries the report's origin."""
    answers = _replay([_PROMPT_ECHO, _STARTED, _RESULT, _NOTIFIED, _INIT,
                       _ANSWER, _REPORT_RESULT])
    assert answers == [False, False, False, False, True]


def test_a_report_that_arrives_after_a_request_started_waits_for_the_next():
    """Only reports owed when a request starts are answered by it."""
    wake = _new_wake()
    for tid in ("t1", "t2"):
        _track_wake(wake, "meta", _started(tid, backgrounded=True))
    _track_wake(wake, "meta", _notified("t1"))
    _track_wake(wake, "meta", {"subtype": "init"})
    _track_wake(wake, "meta", _notified("t2"))
    _track_wake(wake, "result", {"origin": "task-notification"})
    assert wake["owed"] == {"t2"}
    _track_wake(wake, "meta", {"subtype": "init"})
    _track_wake(wake, "result", {"origin": "task-notification"})
    assert wake["owed"] == set()


def test_an_ordinary_result_does_not_clear_an_owed_report():
    wake = _new_wake()
    _track_wake(wake, "meta", _started("t1", backgrounded=True))
    _track_wake(wake, "meta", _notified("t1"))
    _track_wake(wake, "meta", {"subtype": "init"})
    _track_wake(wake, "result", {"origin": None})
    assert wake["owed"] == {"t1"}


def test_the_parser_carries_the_result_origin():
    _k, p = _parse_stream_event(json.dumps(_REPORT_RESULT))
    assert p["origin"] == "task-notification"
    _k, p = _parse_stream_event(json.dumps(_RESULT))
    assert p["origin"] is None
