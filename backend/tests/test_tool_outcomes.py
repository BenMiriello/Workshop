"""A tool step carries its own outcome and how long it took.

Before this, a `tool` segment was write-once: name and input, nothing else. So a
settled turn could not tell a command that failed from a file that was read,
nothing could time a step, and grouping had no "succeeded" predicate to key on.
The four fields added here (`id`, `at`, `ok`, `ms`, plus `error` on failure) are
what the whole disclosure rule turns on, and the client mirrors them exactly in
`frontend/src/components/chat/segments.ts` so a live turn and a reloaded one
render the same rows. See docs/decisions/0015 §2/§3/§5.
"""
import json

from services import build_streaming as bs


def _turn() -> bs.Turn:
    return bs.Turn(id="t1", thread_id="th1", agent_slug="a")


def _tool_result(tool_use_id: str, text: str = "", is_error: bool = False, parent=None) -> dict:
    msg = {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": tool_use_id,
        "content": text, "is_error": is_error,
    }]}
    ev = {"type": "user", "message": msg}
    if parent:
        ev["parent_tool_use_id"] = parent
    return ev


def test_parser_extracts_is_error_off_the_result_block():
    kind, payload = bs._parse_stream_event(json.dumps(_tool_result("t1", "boom", is_error=True)))
    assert kind == "tool_result"
    assert payload["tool_use_id"] == "t1"
    assert payload["is_error"] is True
    # And the success case is explicitly False, not missing.
    _, ok = bs._parse_stream_event(json.dumps(_tool_result("t1", "fine")))
    assert ok["is_error"] is False


def test_an_open_tool_is_running_and_a_closed_one_is_not():
    turn = _turn()
    bs._segment_ingest(turn, "tool", {"name": "Bash", "input": "make", "id": "t1"})
    seg = turn.segments[0]
    assert seg["t"] == "tool" and seg["id"] == "t1"
    assert isinstance(seg["at"], float)      # open: the client reads this as running
    assert "ok" not in seg

    bs._segment_ingest(turn, "tool_result", {"tool_use_id": "t1", "text": "done", "is_error": False})
    assert seg["ok"] is True
    assert "at" not in seg                   # closed: nothing left to tick against
    assert isinstance(seg["ms"], int)


def test_a_failure_keeps_its_text_and_a_success_does_not():
    turn = _turn()
    bs._segment_ingest(turn, "tool", {"name": "Bash", "input": "make", "id": "t1"})
    bs._segment_ingest(turn, "tool_result", {"tool_use_id": "t1", "text": "boom", "is_error": True})
    assert turn.segments[0]["ok"] is False
    assert turn.segments[0]["error"] == "boom"

    bs._segment_ingest(turn, "tool", {"name": "Read", "input": "x.ts", "id": "t2"})
    bs._segment_ingest(turn, "tool_result", {"tool_use_id": "t2", "text": "a" * 5000})
    # A successful result is plumbing the operator already sees the effect of;
    # storing every one would put the whole tool output history in the message.
    assert "error" not in turn.segments[1]


def test_a_result_for_an_unknown_step_changes_nothing():
    turn = _turn()
    bs._segment_ingest(turn, "tool", {"name": "Bash", "input": "make", "id": "t1"})
    before = json.dumps(turn.segments, sort_keys=True)
    bs._segment_ingest(turn, "tool_result", {"tool_use_id": "nope", "text": "x"})
    assert json.dumps(turn.segments, sort_keys=True) == before


def test_a_nested_step_is_registered_so_its_own_result_can_close_it():
    turn = _turn()
    bs._segment_ingest(turn, "tool", {"agent": "Explore", "input": "look", "id": "a1"})
    bs._segment_ingest(turn, "tool", {"name": "Read", "input": "x.ts", "id": "n1", "parent": "a1"})
    step = turn.segments[0]["steps"][0]
    assert step["t"] == "tool" and step["id"] == "n1"
    # Registered in the SAME map as top-level calls. tool_use_id is unique
    # across the turn, so one map cannot confuse a delegate's step for the
    # parent's, and `_close_open_tools` reaches both on an interrupt.
    assert turn.tool_segs["n1"] is step


def test_an_interrupted_turn_leaves_the_outcome_unknown_not_failed():
    # Painting every step of a stopped turn red would say the tools failed when
    # what actually happened is that the process holding them went away.
    turn = _turn()
    bs._segment_ingest(turn, "tool", {"name": "Bash", "input": "sleep 600", "id": "t1"})
    bs._close_open_tools(turn)
    seg = turn.segments[0]
    assert "ok" not in seg
    assert "at" not in seg                   # nothing renders a live clock for it
    assert turn.tool_segs == {}


def test_a_legacy_segment_without_an_id_still_records_the_call():
    # The CLI does not always give a tool_use id; the step must still appear.
    turn = _turn()
    bs._segment_ingest(turn, "tool", {"name": "Bash", "input": "ls"})
    assert turn.segments[0] == {"t": "tool", "name": "Bash", "input": "ls"}


def test_a_delegates_step_closes_like_a_top_level_one():
    """A nested step used to be appended bare, with no id and no start, so
    `_close_tool` could never find it. Every step inside an opened chip carried
    no outcome, and an absent outcome reads as succeeded: a delegate whose
    search failed looked exactly like one that had not."""
    turn = _turn()
    bs._segment_ingest(turn, "tool", {"name": "Agent", "input": "go", "id": "a1", "agent": "Explore"})
    bs._segment_ingest(turn, "tool", {"name": "Read", "input": "x.ts", "id": "n1", "parent": "a1"})

    step = turn.segments[0]["steps"][0]
    assert step["id"] == "n1"
    assert isinstance(step.get("at"), float)

    bs._segment_ingest(turn, "tool_result", {
        "tool_use_id": "n1", "text": "no such file", "is_error": True, "parent": "a1",
    })
    assert step["ok"] is False
    assert step["error"] == "no such file"
    assert "at" not in step
    assert isinstance(step["ms"], int)

    # The delegation itself is still running: only the Agent tool's OWN result
    # reports back.
    assert turn.segments[0]["done"] is False


def test_a_delegates_step_does_not_close_another_delegates():
    turn = _turn()
    bs._segment_ingest(turn, "tool", {"name": "Agent", "input": "one", "id": "a1", "agent": "Explore"})
    bs._segment_ingest(turn, "tool", {"name": "Agent", "input": "two", "id": "a2", "agent": "Plan"})
    bs._segment_ingest(turn, "tool", {"name": "Read", "input": "p", "id": "n1", "parent": "a1"})
    bs._segment_ingest(turn, "tool", {"name": "Read", "input": "q", "id": "n2", "parent": "a2"})
    bs._segment_ingest(turn, "tool_result", {
        "tool_use_id": "n2", "text": "boom", "is_error": True, "parent": "a2",
    })
    assert "ok" not in turn.segments[0]["steps"][0]
    assert turn.segments[1]["steps"][0]["ok"] is False
