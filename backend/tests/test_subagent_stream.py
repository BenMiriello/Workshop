"""A subagent's work belongs to the subagent, not to the turn that launched it.

Every event shape here was recorded from Claude Code CLI 2.1.143 (probe in
scratch/subagent-probe): the tool is emitted as `Agent`, its input carries
`description` + `prompt` (+ `subagent_type` when a named one is asked for), and
EVERY event the subagent produces carries `parent_tool_use_id` pointing back at
the Agent tool_use. That field is the whole mechanism. Without reading it:

  - the subagent's interim text is concatenated into the operator's answer,
  - its tool calls flatten into the parent's work with nothing marking whose
    they are,
  - and its PROMPT arrives in the same shape as a replayed operator message, so
    it can be mistaken for a mid-turn injection receipt.

These tests pin all three, plus the segment shape the UI renders.
"""
import json

from services import build_streaming as bs


AGENT_ID = "toolu_01LMk1hNXZA6Lp8FdAvYEHP2"


def _assistant(blocks, parent=None):
    ev = {"type": "assistant", "message": {"role": "assistant", "content": blocks}}
    if parent:
        ev["parent_tool_use_id"] = parent
    return json.dumps(ev)


def _agent_call(tool_id=AGENT_ID, subagent_type=None, description="Probe the tree"):
    inp = {"description": description, "prompt": "Read the thing and report back."}
    if subagent_type:
        inp["subagent_type"] = subagent_type
    return _assistant([{"type": "tool_use", "id": tool_id, "name": "Agent", "input": inp}])


def _tool_result(tool_id=AGENT_ID, text="The tree has three files.", parent=None):
    ev = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id,
         "content": [{"type": "text", "text": text}]},
    ]}}
    if parent:
        ev["parent_tool_use_id"] = parent
    return json.dumps(ev)


def _turn():
    return bs.Turn(id="t1", thread_id="th1", agent_slug="general")


def _feed(turn, lines):
    """Run real journal lines through the real parser and the real segment
    router — the same two functions the streaming driver calls, so a change that
    breaks routing breaks these tests instead of only breaking live chats."""
    for line in lines:
        parsed = bs._parse_stream_event(line)
        assert parsed is not None, line
        bs._segment_ingest(turn, *parsed)


class TestParsing:
    def test_an_agent_call_is_labelled_with_its_subagent(self):
        kind, payload = bs._parse_stream_event(_agent_call(subagent_type="work-reviewer"))
        ev = payload["events"][0]
        assert ev[0] == "tool"
        assert ev[1]["agent"] == "work-reviewer"
        assert ev[1]["id"] == AGENT_ID

    def test_an_unnamed_agent_call_is_the_general_purpose_worker(self):
        _, payload = bs._parse_stream_event(_agent_call())
        assert payload["events"][0][1]["agent"] == "general-purpose"

    def test_an_ordinary_tool_is_not_an_agent(self):
        line = _assistant([{"type": "tool_use", "id": "tu_9", "name": "Bash",
                            "input": {"command": "ls"}}])
        _, payload = bs._parse_stream_event(line)
        assert payload["events"][0][1]["agent"] is None

    def test_the_older_task_spelling_still_reads_as_a_subagent(self):
        """`Task` was the name before 2.1.x and is still accepted on --tools.
        Pinning only `Agent` would make this silently regress on an older CLI."""
        line = _assistant([{"type": "tool_use", "id": "tu_1", "name": "Task",
                            "input": {"prompt": "go"}}])
        _, payload = bs._parse_stream_event(line)
        assert payload["events"][0][1]["agent"] == "general-purpose"

    def test_sidechain_events_carry_their_parent(self):
        line = _assistant([{"type": "text", "text": "reading"}], parent=AGENT_ID)
        _, payload = bs._parse_stream_event(line)
        assert payload["parent"] == AGENT_ID
        assert payload["events"][0][1]["parent"] == AGENT_ID

    def test_a_subagent_prompt_is_marked_as_sidechain(self):
        """It arrives in the same shape as a replayed operator message. Only the
        parent id tells them apart, and a subagent prompt that matched a queued
        injection's text would otherwise consume its receipt."""
        line = json.dumps({"type": "user", "parent_tool_use_id": AGENT_ID,
                           "message": {"role": "user", "content": [
                               {"type": "text", "text": "Read the thing and report back."}]}})
        kind, payload = bs._parse_stream_event(line)
        assert kind == "user_echo"
        assert payload["parent"] == AGENT_ID

    def test_the_agent_result_text_comes_back_flattened(self):
        kind, payload = bs._parse_stream_event(_tool_result(text="PONG"))
        assert kind == "tool_result"
        assert payload["text"] == "PONG"


class TestSegments:
    def test_a_launch_becomes_one_agent_segment(self):
        t = _turn()
        _feed(t, [_agent_call(subagent_type="work-reviewer")])
        assert [s["t"] for s in t.segments] == ["agent"]
        seg = t.segments[0]
        assert seg["name"] == "work-reviewer"
        # The chip says what the subagent was ASKED TO DO, not what it was told.
        # This assertion used to read "Read the thing and report back." (the
        # prompt), which is how a page of raw briefing ended up rendered as a
        # delegation's title.
        assert seg["input"] == "Probe the tree"
        assert seg["done"] is False

    def test_a_launch_with_no_description_falls_back_to_the_prompts_first_line(self):
        t = _turn()
        line = _assistant([{"type": "tool_use", "id": AGENT_ID, "name": "Agent",
                            "input": {"prompt": "Read the thing and report back."}}])
        _feed(t, [line])
        assert t.segments[0]["input"] == "Read the thing and report back."

    def test_a_launch_without_a_description_never_titles_itself_with_the_brief(self):
        # The old fallback took 300 characters of raw instructions. A title is
        # one line, so a multi-line brief contributes only its first.
        brief = "Audit the store.\n\nRead these first:\n" + ("x" * 500)
        t = _turn()
        _feed(t, [_assistant([{"type": "tool_use", "id": AGENT_ID, "name": "Agent",
                               "input": {"prompt": brief}}])])
        seg = t.segments[0]
        assert seg["input"] == "Audit the store."
        # ...and the brief itself is still there to open.
        assert seg["detail"].startswith("Audit the store.")
        assert "x" * 100 in seg["detail"]

    def test_a_long_single_line_brief_is_clamped_to_a_title(self):
        t = _turn()
        _feed(t, [_assistant([{"type": "tool_use", "id": AGENT_ID, "name": "Agent",
                               "input": {"prompt": "y" * 400}}])])
        assert len(t.segments[0]["input"]) == 80

    def test_an_ordinary_tool_still_shows_what_it_ran_not_its_gloss(self):
        # Bash carries a `description` too, and there the command is the useful
        # thing; only a subagent launch prefers the description.
        t = _turn()
        _feed(t, [_assistant([{"type": "tool_use", "id": "tu_x", "name": "Bash",
                               "input": {"command": "ls -la",
                                         "description": "List files"}}])])
        assert t.segments[0]["input"] == "ls -la"

    def test_the_subagents_steps_nest_under_it(self):
        t = _turn()
        _feed(t, [
            _agent_call(),
            _assistant([{"type": "tool_use", "id": "tu_a", "name": "Bash",
                         "input": {"command": "ls -la"}}], parent=AGENT_ID),
            _assistant([{"type": "tool_use", "id": "tu_b", "name": "Read",
                         "input": {"file_path": "/tmp/x"}}], parent=AGENT_ID),
        ])
        # ONE top-level segment, not three: the parent did one thing (delegate).
        assert [s["t"] for s in t.segments] == ["agent"]
        assert [s["name"] for s in t.segments[0]["steps"]] == ["Bash", "Read"]

    def test_a_subagents_text_never_enters_the_answer(self):
        """The regression this whole path exists to prevent: the delegate's
        working notes appearing in the operator's reply as if the assistant had
        written them."""
        t = _turn()
        _feed(t, [
            _agent_call(),
            _assistant([{"type": "text", "text": "Let me check the second file."}], parent=AGENT_ID),
            _tool_result(text="Three files."),
            _assistant([{"type": "text", "text": "It found three files."}]),
        ])
        answer = "".join(s["text"] for s in t.segments if s["t"] == "text")
        assert answer == "It found three files."
        assert "second file" in t.segments[0]["steps"][0]["text"]

    def test_the_result_closes_the_segment(self):
        t = _turn()
        _feed(t, [_agent_call(), _tool_result(text="Three files.")])
        seg = t.segments[0]
        assert seg["done"] is True
        assert seg["result"] == "Three files."

    def test_an_unfinished_subagent_stays_open(self):
        """A turn that dies mid-delegation must render as a subagent still
        running, not as one that reported nothing."""
        t = _turn()
        _feed(t, [_agent_call()])
        assert t.segments[0]["done"] is False

    def test_the_parents_own_tools_stay_at_the_top_level(self):
        t = _turn()
        _feed(t, [
            _assistant([{"type": "tool_use", "id": "tu_0", "name": "Grep",
                         "input": {"pattern": "foo"}}]),
            _agent_call(),
            _assistant([{"type": "tool_use", "id": "tu_a", "name": "Bash",
                         "input": {"command": "ls"}}], parent=AGENT_ID),
        ])
        assert [s["t"] for s in t.segments] == ["tool", "agent"]
        assert t.segments[0]["name"] == "Grep"

    def test_two_subagents_keep_their_own_steps(self):
        """Independent delegates launched in one turn must not pool their work."""
        t = _turn()
        second = "toolu_second"
        _feed(t, [
            _agent_call(),
            _agent_call(tool_id=second, subagent_type="work-reviewer"),
            _assistant([{"type": "tool_use", "id": "x", "name": "Bash",
                         "input": {"command": "one"}}], parent=AGENT_ID),
            _assistant([{"type": "tool_use", "id": "y", "name": "Read",
                         "input": {"file_path": "two"}}], parent=second),
        ])
        assert [s["steps"][0]["name"] for s in t.segments] == ["Bash", "Read"]

    def test_an_orphan_sidechain_event_is_not_dropped(self):
        """If a parent id is unknown (a journal tailed from mid-stream after a
        restart), the step still has to show up somewhere rather than vanish."""
        t = _turn()
        _feed(t, [_assistant([{"type": "tool_use", "id": "z", "name": "Bash",
                               "input": {"command": "ls"}}], parent="toolu_unknown")])
        assert [s["t"] for s in t.segments] == ["tool"]


class TestUnfinishedDelegates:
    """A delegate is killed with its parent, so the chip must stop saying it is
    working the moment the turn ends.

    Nothing used to close an open `agent` segment: `done` was set only by the
    Agent tool's own `tool_result`, and a turn that was stopped or lost never
    produced one. `_commit_turn` then committed the segment as-is, so the chip
    read "working" for the rest of the thread's life, describing a process that
    had died with the turn.
    """

    def test_stop_closes_an_open_delegate_and_says_so(self):
        t = _turn()
        _feed(t, [
            _agent_call(),
            _assistant([{"type": "tool_use", "id": "x", "name": "Bash",
                         "input": {"command": "ls"}}], parent=AGENT_ID),
        ])
        seg = t.segments[0]
        assert seg["done"] is False        # still running, correctly
        bs._close_open_agents(t, stopped=True, lost=False)
        assert seg["done"] is True
        assert seg["outcome"] == "stopped"
        assert "Stopped" in seg["result"]

    def test_a_lost_turn_says_lost_not_stopped(self):
        """The two endings are different events and the chip names which."""
        t = _turn()
        _feed(t, [_agent_call()])
        bs._close_open_agents(t, stopped=False, lost=True)
        assert t.segments[0]["outcome"] == "lost"

    def test_an_ending_with_no_named_reason_still_closes(self):
        t = _turn()
        _feed(t, [_agent_call()])
        bs._close_open_agents(t, stopped=False, lost=False)
        assert t.segments[0]["done"] is True
        assert t.segments[0]["outcome"] == "unfinished"

    def test_a_delegate_that_reported_back_is_left_alone(self):
        """Closing must not relabel work that genuinely finished."""
        t = _turn()
        _feed(t, [_agent_call(), _tool_result(text="Found three files.")])
        bs._close_open_agents(t, stopped=True, lost=False)
        seg = t.segments[0]
        assert seg["done"] is True
        assert seg["outcome"] == ""
        assert seg["result"] == "Found three files."

    def test_partial_text_it_had_already_reported_survives(self):
        t = _turn()
        _feed(t, [_agent_call()])
        t.segments[0]["result"] = "Halfway through: two of five files read."
        bs._close_open_agents(t, stopped=True, lost=False)
        assert t.segments[0]["result"].startswith("Halfway through")
        assert t.segments[0]["outcome"] == "stopped"
